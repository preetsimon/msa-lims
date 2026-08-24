"""OIDC token verification, against tokens signed with a real key.

A test keypair is generated here and the verifier is pinned to it, so signatures
are genuinely checked — a test that stubbed out verification would pass against
an implementation that skipped it, which is the one thing this module must never
do.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from msa_lims.auth.oidc import (
    AuthenticationError,
    AuthorisationError,
    OidcVerifier,
    parse_role_map,
)
from msa_lims.domain.enums import Role

ISSUER = "https://id.example-lab.test"
AUDIENCE = "msa-lims"
ROLE_MAP = {
    "lab-prep-techs": Role.PREP_TECH,
    "lab-analysts": Role.ANALYST,
    "lab-supervisors": Role.SUPERVISOR,
    "lab-managers": Role.LAB_MANAGER,
    "clients": Role.CLIENT,
}

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class PinnedKeyClient:
    """Stands in for the provider's key endpoint, returning one known key."""

    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("Key", (), {"key": self._key})()


def verifier(**overrides: Any) -> OidcVerifier:
    settings: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "role_map": ROLE_MAP,
        "jwk_client": PinnedKeyClient(SIGNING_KEY.public_key()),
    }
    settings.update(overrides)
    return OidcVerifier(**settings)


def token(
    *,
    key: Any = SIGNING_KEY,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    subject: str = "user-42",
    groups: Any = ("lab-analysts",),
    expires_in: int = 600,
    email: str | None = "chemist@lab",
    omit: tuple[str, ...] = (),
) -> str:
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
        "groups": groups,
    }
    if email is not None:
        claims["email"] = email
    for claim in omit:
        claims.pop(claim, None)
    return jwt.encode(claims, key, algorithm="RS256")


class TestAValidToken:
    def test_it_identifies_the_caller(self) -> None:
        identity = verifier().verify(token())
        assert identity.subject == "user-42"
        assert identity.name == "chemist@lab"
        assert identity.role is Role.ANALYST

    def test_groups_are_mapped_to_roles(self) -> None:
        assert verifier().verify(token(groups=["lab-managers"])).role is Role.LAB_MANAGER
        assert verifier().verify(token(groups=["clients"])).role is Role.CLIENT

    def test_the_most_privileged_group_wins(self) -> None:
        # A manager who is also on the analyst rota is a manager.
        identity = verifier().verify(token(groups=["lab-analysts", "lab-managers"]))
        assert identity.role is Role.LAB_MANAGER

    def test_a_client_group_never_outranks_an_internal_one(self) -> None:
        identity = verifier().verify(token(groups=["clients", "lab-prep-techs"]))
        assert identity.role is Role.PREP_TECH

    def test_every_group_is_recorded_including_unmapped_ones(self) -> None:
        # So "why was I refused?" is answerable without the provider's logs.
        identity = verifier().verify(token(groups=["lab-analysts", "facilities"]))
        assert identity.groups == ("lab-analysts", "facilities")

    @pytest.mark.parametrize(
        "groups",
        [
            ["lab-analysts"],  # a JSON array
            "lab-analysts",  # a bare string
            "lab-analysts other-group",  # space separated
            "lab-analysts,other-group",  # comma separated
        ],
    )
    def test_the_groups_claim_is_accepted_in_every_shape_providers_send(self, groups: Any) -> None:
        # Providers render this inconsistently. Supporting one shape produces an
        # integration that works against Okta and refuses everyone against Entra.
        assert verifier().verify(token(groups=groups)).role is Role.ANALYST

    def test_the_subject_is_used_when_no_name_claim_is_present(self) -> None:
        assert verifier().verify(token(email=None)).name == "user-42"


class TestSignaturesAreActuallyChecked:
    def test_a_token_signed_by_another_key_is_refused(self) -> None:
        # The assertion the module exists for.
        with pytest.raises(AuthenticationError):
            verifier().verify(token(key=OTHER_KEY))

    def test_an_unsigned_token_is_refused(self) -> None:
        forged = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "intruder", "exp": int(time.time()) + 600},
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthenticationError):
            verifier().verify(forged)

    def test_a_tampered_payload_is_refused(self) -> None:
        header, _payload, signature = token().split(".")
        tampered = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "intruder",
                "exp": int(time.time()) + 600,
                "groups": ["lab-managers"],
            },
            OTHER_KEY,
            algorithm="RS256",
        ).split(".")
        with pytest.raises(AuthenticationError):
            verifier().verify(f"{header}.{tampered[1]}.{signature}")

    def test_there_is_no_way_to_switch_verification_off(self) -> None:
        # A "trust the token" flag is the most common way this kind of
        # integration is quietly compromised. It must not be reachable.
        import inspect

        from msa_lims.auth import oidc

        source = inspect.getsource(oidc)
        assert '"verify_signature": True' in source
        assert '"verify_signature": False' not in source


class TestRejections:
    def test_an_expired_token_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="expired"):
            verifier().verify(token(expires_in=-3600))

    def test_a_token_for_another_audience_is_refused(self) -> None:
        # Otherwise a token minted for a different application at the same
        # provider would be accepted here.
        with pytest.raises(AuthenticationError, match="audience"):
            verifier().verify(token(audience="some-other-app"))

    def test_a_token_from_another_issuer_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="provider"):
            verifier().verify(token(issuer="https://attacker.test"))

    def test_a_token_without_a_subject_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="sub"):
            verifier().verify(token(omit=("sub",)))

    def test_a_token_without_an_expiry_is_refused(self) -> None:
        # A token that never expires is a permanent credential.
        with pytest.raises(AuthenticationError, match="exp"):
            verifier().verify(token(omit=("exp",)))

    def test_an_empty_token_is_refused(self) -> None:
        with pytest.raises(AuthenticationError, match="no bearer token"):
            verifier().verify("")

    def test_rubbish_is_refused(self) -> None:
        with pytest.raises(AuthenticationError):
            verifier().verify("not-a-token")


class TestAuthorisation:
    def test_an_unmapped_group_grants_nothing(self) -> None:
        # No fallback to analyst. Adding a group at the provider must not
        # silently grant access here.
        with pytest.raises(AuthorisationError, match="maps to a role"):
            verifier().verify(token(groups=["facilities"]))

    def test_no_groups_at_all_grants_nothing(self) -> None:
        with pytest.raises(AuthorisationError):
            verifier().verify(token(groups=[]))

    def test_being_refused_a_role_is_not_the_same_as_a_bad_token(self) -> None:
        # They are different exception types because they send the caller to
        # different places: the login page, or an administrator.
        assert not issubclass(AuthorisationError, AuthenticationError)


class TestConfiguration:
    def test_a_role_map_is_parsed(self) -> None:
        mapping = parse_role_map("lab-analysts=analyst, lab-managers=lab_manager")
        assert mapping == {"lab-analysts": Role.ANALYST, "lab-managers": Role.LAB_MANAGER}

    def test_an_empty_map_parses_to_nothing(self) -> None:
        assert parse_role_map("") == {}

    def test_an_unknown_role_name_raises_at_startup(self) -> None:
        # Rather than being skipped. A typo that silently dropped a mapping
        # would lock out a whole shift with no error anywhere.
        with pytest.raises(ValueError, match="unknown role"):
            parse_role_map("lab-analysts=chief-wizard")

    def test_a_malformed_entry_raises(self) -> None:
        with pytest.raises(ValueError, match="not 'group=role'"):
            parse_role_map("lab-analysts")

    def test_a_verifier_without_a_role_map_is_refused(self) -> None:
        # Every token would be rejected; that is a misconfiguration, not a policy.
        with pytest.raises(ValueError, match="group-to-role mapping"):
            OidcVerifier(issuer=ISSUER, audience=AUDIENCE, role_map={})

    def test_a_verifier_without_an_issuer_is_refused(self) -> None:
        with pytest.raises(ValueError, match="issuer and an audience"):
            OidcVerifier(issuer="", audience=AUDIENCE, role_map=ROLE_MAP)


class TestSecretsDoNotLeak:
    def test_a_rejection_message_does_not_contain_the_token(self) -> None:
        bad = token(key=OTHER_KEY)
        try:
            verifier().verify(bad)
        except AuthenticationError as exc:
            assert bad not in str(exc)
            assert bad[:20] not in str(exc)
        else:  # pragma: no cover
            pytest.fail("expected a rejection")

    def test_a_rejection_does_not_say_which_check_failed_in_detail(self) -> None:
        # Telling an unauthenticated caller precisely which check failed helps
        # nobody but somebody probing the endpoint. The reason goes to the log.
        try:
            verifier().verify("not-a-token")
        except AuthenticationError as exc:
            assert str(exc) == "the token could not be verified"

    def test_no_log_record_carries_the_token(self, caplog: pytest.LogCaptureFixture) -> None:
        bad = token(key=OTHER_KEY)
        with caplog.at_level("DEBUG"), pytest.raises(AuthenticationError):
            verifier().verify(bad)
        assert bad not in caplog.text
