"""Request-scoped dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.auth.oidc import (
    AuthenticationError,
    AuthorisationError,
    OidcVerifier,
    parse_role_map,
)
from msa_lims.config import Settings, get_settings
from msa_lims.db.models import LabUser
from msa_lims.db.session import get_session_factory
from msa_lims.domain.enums import Role


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting, and with what authority.

    Just enough to pass to
    :func:`msa_lims.domain.lifecycle.check_transition` and to resolve the
    :class:`~msa_lims.db.models.LabUser` row an
    :class:`~msa_lims.db.models.AuditEvent` references. ``subject`` is the
    identity provider's stable identifier (or, in dev-header mode, the header
    value standing in for one) — never the display name, which people change.
    """

    subject: str
    name: str
    role: Role


def get_db() -> Iterator[Session]:
    """A session per request, rolled back if the handler raises.

    Committing is left to the handler rather than done here on the way out,
    so a route that needs to write an audit event and its subject atomically
    controls exactly when that transaction closes.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache
def get_verifier() -> OidcVerifier:
    """The token verifier this deployment uses, built once.

    Built lazily and cached rather than at import, so a process that never
    authenticates — a migration, a script — does not fail to start over an
    identity provider it will not talk to. Misconfiguration surfaces on the
    first authenticated request, with a message naming the missing setting.
    """
    settings = get_settings()
    if not settings.oidc_issuer or not settings.oidc_audience:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "auth_mode is 'oidc' but MSA_OIDC_ISSUER or MSA_OIDC_AUDIENCE is "
                "not set; this deployment cannot authenticate anyone"
            ),
        )
    try:
        role_map = parse_role_map(settings.oidc_role_map)
        return OidcVerifier(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            role_map=role_map,
            jwks_url=settings.oidc_jwks_url,
            groups_claim=settings.oidc_groups_claim,
            name_claim=settings.oidc_name_claim,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"the OIDC configuration is not usable: {exc}",
        ) from exc


def current_actor(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_actor: Annotated[str | None, Header()] = None,
    x_actor_role: Annotated[str | None, Header()] = None,
) -> Actor:
    """Who is making this request, and with what authority.

    Two modes, chosen by ``MSA_AUTH_MODE``, and no third mode that trusts a
    token without checking it.

    **oidc** verifies a bearer token against the provider's published keys and
    maps its groups to a role. This is what a deployment uses.

    **dev_headers** trusts ``X-Actor`` and ``X-Actor-Role``, and is refused
    outside local and CI. It exists so the role model can be exercised
    honestly in development without standing up an identity provider. A
    development shortcut that quietly kept working in production is exactly
    the kind of thing an NI 43-101 audit exists to find, so the environment
    check stays even though the mode is no longer the only option.
    """
    if settings.auth_mode == "oidc":
        return _actor_from_token(request, authorization)

    if settings.env not in ("local", "ci"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "this environment is running with development header authentication, "
                "which is refused outside local and CI; set MSA_AUTH_MODE=oidc"
            ),
        )

    try:
        # The default role is analyst — the least privileged role that can
        # still enter results — so forgetting the header cannot accidentally
        # grant the power to sign a certificate.
        role = Role(x_actor_role) if x_actor_role else Role.ANALYST
    except ValueError:
        permitted = ", ".join(r.value for r in Role)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown role {x_actor_role!r}; expected one of: {permitted}",
        ) from None

    name = x_actor or "dev@localhost"
    return Actor(subject=name, name=name, role=role)


def _actor_from_token(request: Request, authorization: str | None) -> Actor:
    """Verify a bearer token and turn it into an actor.

    401 and 403 mean different things and are kept apart: a 401 says the token
    is not usable and the caller should sign in again, a 403 says the person is
    who they claim but has not been granted a role here. Collapsing them would
    send somebody to the wrong place — the login page instead of an
    administrator.
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="this endpoint requires a bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verifier: OidcVerifier = getattr(request.app.state, "verifier", None) or get_verifier()
    try:
        identity = verifier.verify(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthorisationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return Actor(subject=identity.subject, name=identity.name, role=identity.role)


def current_lab_user(
    actor: Annotated[Actor, Depends(current_actor)], session: SessionDep
) -> LabUser:
    """The row an audit event's ``actor_id`` can reference, provisioned on first sight.

    The identity provider (or the dev-header shim standing in for one) is
    authoritative for who someone is and what role they currently hold — every
    authorisation check reads :attr:`Actor.role` fresh from the request, never
    this row's. ``LabUser`` exists only so a foreign key has something durable
    to point at across requests; its ``full_name`` and ``role`` are kept in
    sync as a courtesy so a report joining through it is not stale, not because
    anything is entitled to trust them over the actor making the current call.

    Looked up by ``subject`` — the provider's stable identifier — never by
    name or email, both of which people change.
    """
    user = session.scalar(select(LabUser).where(LabUser.subject == actor.subject))
    if user is None:
        user = LabUser(
            subject=actor.subject,
            # A dev-header actor's name is rarely a real address; the fallback
            # keeps the column's uniqueness meaningful without pretending a
            # non-email string is one.
            email=actor.name if "@" in actor.name else f"{actor.subject}@unknown.invalid",
            full_name=actor.name,
            role=actor.role,
        )
        session.add(user)
        session.flush()
        return user

    if user.full_name != actor.name or user.role is not actor.role:
        user.full_name = actor.name
        user.role = actor.role
    return user


SessionDep = Annotated[Session, Depends(get_db)]
ActorDep = Annotated[Actor, Depends(current_actor)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LabUserDep = Annotated[LabUser, Depends(current_lab_user)]
