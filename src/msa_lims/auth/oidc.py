"""OIDC bearer-token authentication.

Ported from QC Sentinel's ``auth/oidc.py`` — the module that lifted Sentinel out
of "cannot be deployed at all" — with the role vocabulary swapped for this
system's. The design stays deliberately narrow: MSA LIMS verifies tokens; it
does not issue them, does not manage users, and does not store passwords. An
identity provider the laboratory already runs does all of that, and the only
thing needed here is the ability to check that a token really came from it and
to find out who the bearer is.

Four decisions worth knowing before changing anything:

**Signatures are verified against the provider's published keys, never skipped.**
The JWKS document is fetched from the issuer's discovery endpoint and cached.
There is no configuration flag that turns verification off — a "trust the token"
switch is the single most common way this kind of integration is quietly
compromised, so the switch does not exist.

**Roles are mapped explicitly, and an unrecognised group grants nothing.** The
provider's group names are the laboratory's, not ours, so the mapping is
configuration. A group with no mapping does not fall back to analyst: the
default is *no role at all*, and the request is refused. Falling back would mean
that adding a group at the provider silently granted access here.

**Claims are read, never trusted for authorisation beyond the mapping.** The
token says who the caller is; this application decides what that identity may
do, using the same :class:`~msa_lims.domain.enums.Role` checks the sample
lifecycle already enforces. A token cannot carry "may sign a certificate".

**Nothing token-shaped is ever logged.** Failures log the reason and the key id,
never the token, the claims, or the bearer's email — an access log full of
bearer tokens is a credential store nobody meant to build.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from msa_lims.domain.enums import Role

logger = logging.getLogger(__name__)

#: How long a fetched JWKS document is reused. Long enough that the provider is
#: not hit per request, short enough that a revoked key stops working the same
#: shift it was revoked.
JWKS_CACHE_SECONDS = 600

#: Clock skew tolerated on ``exp`` and ``nbf``. Servers drift; ten seconds is
#: enough for that and far too little to matter for a stolen token.
LEEWAY_SECONDS = 10


class AuthenticationError(Exception):
    """The token is missing, malformed, expired, or not from the issuer."""


class AuthorisationError(Exception):
    """The token is valid but carries no role this application recognises."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Who the token says the caller is, after the signature checked out."""

    subject: str
    name: str
    role: Role
    issuer: str
    #: Every group the token carried, including ones with no mapping. Recorded
    #: so that "why was I refused?" is answerable without the provider's logs.
    groups: tuple[str, ...] = ()
    expires_at: int | None = None


def parse_role_map(raw: str) -> dict[str, Role]:
    """Parse ``group=role,group=role`` into a mapping.

    Configuration rather than code because the group names belong to the
    laboratory's directory. An unparseable entry raises at startup rather than
    being skipped — a typo in a role mapping that silently dropped a group would
    lock out a whole shift with no error anywhere.
    """
    mapping: dict[str, Role] = {}
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        group, _, role_name = entry.partition("=")
        if not group.strip() or not role_name.strip():
            raise ValueError(f"role mapping entry {entry!r} is not 'group=role'")
        try:
            mapping[group.strip()] = Role(role_name.strip())
        except ValueError as exc:
            permitted = ", ".join(r.value for r in Role)
            raise ValueError(
                f"role mapping entry {entry!r} names an unknown role; expected one of: {permitted}"
            ) from exc
    return mapping


class OidcVerifier:
    """Verifies bearer tokens against an OIDC provider's published keys."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        role_map: dict[str, Role],
        jwks_url: str | None = None,
        groups_claim: str = "groups",
        name_claim: str = "email",
        cache_seconds: int = JWKS_CACHE_SECONDS,
        jwk_client: Any | None = None,
    ) -> None:
        if not issuer or not audience:
            raise ValueError("OIDC requires both an issuer and an audience")
        if not role_map:
            raise ValueError(
                "OIDC requires a group-to-role mapping; without one every token "
                "would be refused, which is a misconfiguration rather than a policy"
            )

        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._role_map = role_map
        self._groups_claim = groups_claim
        self._name_claim = name_claim
        self._jwks_url = jwks_url or f"{self._issuer}/.well-known/jwks.json"
        self._cache_seconds = cache_seconds

        self._lock = threading.Lock()
        self._client: Any | None = jwk_client
        self._fetched_at = time.monotonic() if jwk_client is not None else 0.0
        self._pinned = jwk_client is not None

    # -- verification -------------------------------------------------------

    def verify(self, token: str) -> VerifiedIdentity:
        """Check a bearer token and return who it identifies.

        Raises :class:`AuthenticationError` when the token is not genuinely from
        the issuer, and :class:`AuthorisationError` when it is genuine but
        carries no group this deployment maps to a role. The two are separate
        because they mean different things to the caller: one is "log in again",
        the other is "ask an administrator".
        """
        if not token:
            raise AuthenticationError("no bearer token was supplied")

        try:
            signing_key = self._signing_key(token)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("the token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError("the token was issued for a different audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError("the token was issued by a different provider") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise AuthenticationError(
                f"the token is missing a required claim: {exc.claim}"
            ) from exc
        except jwt.InvalidTokenError as exc:
            # Deliberately unspecific to the caller. The reason is logged;
            # telling an unauthenticated client exactly which check failed helps
            # nobody but somebody probing the endpoint.
            logger.warning("token rejected: %s", exc)
            raise AuthenticationError("the token could not be verified") from exc

        return self._identify(claims)

    def _identify(self, claims: dict[str, Any]) -> VerifiedIdentity:
        groups = _string_list(claims.get(self._groups_claim))
        roles = [self._role_map[group] for group in groups if group in self._role_map]

        if not roles:
            # No fallback. Defaulting to analyst would mean that adding a group
            # at the provider silently granted access here.
            logger.info(
                "token for subject %s carries no mapped group (%d group(s) presented)",
                claims.get("sub"),
                len(groups),
            )
            raise AuthorisationError(
                "your account is authenticated but belongs to no group this system "
                "maps to a role; ask an administrator to grant one"
            )

        # Most privileged wins where a person is in several groups, which is the
        # ordinary case for a manager who is also on the analyst rota.
        role = max(roles, key=_PRIVILEGE.index)

        subject = str(claims.get("sub", ""))
        return VerifiedIdentity(
            subject=subject,
            name=str(claims.get(self._name_claim) or claims.get("preferred_username") or subject),
            role=role,
            issuer=str(claims.get("iss", self._issuer)),
            groups=tuple(groups),
            expires_at=claims.get("exp"),
        )

    # -- keys ---------------------------------------------------------------

    def _signing_key(self, token: str) -> Any:
        client = self._jwk_client()
        try:
            return client.get_signing_key_from_jwt(token).key
        except Exception as exc:
            # A key id the cache has not seen usually means rotation. One forced
            # refresh, then give up: retrying per request on unknown key ids
            # turns a stream of junk tokens into a denial of service against the
            # identity provider.
            if self._pinned:
                raise AuthenticationError("the token's signing key is not recognised") from exc
            logger.info("signing key not found; refreshing the provider's key set")
            client = self._jwk_client(force=True)
            try:
                return client.get_signing_key_from_jwt(token).key
            except Exception as retry_exc:
                raise AuthenticationError(
                    "the token's signing key is not recognised by the provider"
                ) from retry_exc

    def _jwk_client(self, *, force: bool = False) -> Any:
        with self._lock:
            expired = (time.monotonic() - self._fetched_at) > self._cache_seconds
            if (
                self._client is None
                or (force and not self._pinned)
                or (expired and not self._pinned)
            ):
                self._client = PyJWKClient(
                    self._jwks_url, cache_keys=True, lifespan=self._cache_seconds
                )
                self._fetched_at = time.monotonic()
            return self._client


#: Least to most privileged, for resolving a caller who is in several groups.
#: CLIENT sits at the bottom with everyone else above it: a client's group
#: membership must never be the one that decides a person's role when they also
#: hold an internal one.
_PRIVILEGE: tuple[Role, ...] = (
    Role.CLIENT,
    Role.PREP_TECH,
    Role.ANALYST,
    Role.SUPERVISOR,
    Role.LAB_MANAGER,
)


def _string_list(value: Any) -> list[str]:
    """Read a groups claim, which providers render inconsistently.

    Some send a JSON array, some a space-separated string, some a single string.
    All three are accepted because the alternative is an integration that works
    against one provider and mysteriously refuses everyone against the next.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return []
