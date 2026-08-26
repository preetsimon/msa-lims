"""Property-based fuzzing of the whole HTTP contract, driven from the API's
own OpenAPI schema.

Every hand-written integration test asserts one specific behaviour the author
already thought of. Schemathesis instead generates schema-valid *and*
adversarial requests for every operation and checks the one property that
should hold no matter what: a malformed or unexpected request is a client
error, never a crash. It finds the inputs nobody thought to type by hand —
an empty string where a `Decimal` is expected, a negative integer in a field
only ever tested with positives, a boundary value one past a `Field(le=...)`.

Authenticated as ``lab_manager`` throughout, the one role every write
endpoint's gate admits (``BENCH_ROLES``, ``MAY_MANAGE_ACCOUNTS``,
``MAY_ENTER_RESULTS``, ``MAY_SIGN_CERTIFICATE``, and ``MAY_CONFIGURE_LAB`` all
include it) — the point is exercising each endpoint's own request validation,
not re-discovering the role gates the unit and integration suites already
cover directly.

Every request a generated example sends runs a real ``session.commit()``
inside the route handler it exercises — the fixture's session is
constructed with ``join_transaction_mode="create_savepoint"``
(SQLAlchemy 2.0), so each commit or rollback the application code performs
is transparently scoped to its own SAVEPOINT rather than reaching the
fixture's own outer, externally-managed transaction. This is the
difference between "isolated" and "merely wrapped": a plain
``Session(bind=connection)`` bound to an already-``connection.begin()``'d
connection *looks* isolated because ``session.commit()`` correctly defers
to the external owner — but ``session.rollback()`` does **not** defer the
same way. The very first generated example that gets refused (a validation
error, overwhelmingly the common case when fuzzing) calls
``session.rollback()``, which — absent ``create_savepoint`` mode — ends the
whole outer transaction for real, not just that one example's work. Every
request after that point runs inside a *fresh, session-owned* transaction
that the fixture's teardown no longer has a reference to, and any
one of those that succeeds commits to the database permanently. This was
found the hard way: real garbage rows (`QcMaterial` named ``"0"``,
stray ``Batch`` rows, and more) kept surviving into ``msa_test`` across
separate ``pytest -m fuzz`` runs despite this fixture's teardown appearing
to roll everything back — ``join_transaction_mode="create_savepoint"``
makes SQLAlchemy manage the SAVEPOINT-per-operation bookkeeping itself
instead of this fixture doing it by hand with :meth:`Session.begin_nested`,
closing the exact gap a hand-rolled version left open.

**Scoped to ``not_a_server_error`` alone, not Schemathesis's full default
check set.** Two of its other built-in checks fire constantly here for
reasons that are not bugs: ``response_schema_conformance`` expects every 422
to match FastAPI's generic ``HTTPValidationError`` shape (an array of
per-field errors), but this API's domain refusals — checked in the service
layer, not by Pydantic — return ``{"detail": "<message>"}`` on purpose (see
``web/app.py``'s error-handler comment: the message is written for the
person who hit the refusal, not a machine-parseable field list), and nothing
in the generated OpenAPI schema declares that second shape.
``positive_data_acceptance`` assumes any request satisfying the *JSON
schema* should succeed, but this codebase deliberately keeps cross-field and
stateful business rules (a CRM needs a certified grade, a name must be
unique, a sample must genuinely be ``READY_FOR_ASSAY``) out of the Pydantic
schema and in the service layer, exactly where every other test in this
suite already exercises them — Schemathesis has no way to know those rules
exist, so it reports every one of their refusals as a false positive.
Documenting accurate per-status response models so schema conformance means
something here is real, separate scope (see PROGRESS.md); ``not_a_server_error``
— the actual "does this crash" question idea #7 exists to answer — needs
none of that first.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from schemathesis import Case
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from msa_lims.db.models import FluxRecipe
from msa_lims.web.app import create_app
from msa_lims.web.deps import get_db

pytestmark = [pytest.mark.integration, pytest.mark.fuzz]

MANAGER_HEADERS = {"X-Actor": "fuzz@lab", "X-Actor-Role": "lab_manager"}


@pytest.fixture
def api_schema(app_engine: Engine) -> Iterator[schemathesis.BaseSchema]:
    connection = app_engine.connect()
    outer_transaction = connection.begin()
    # `join_transaction_mode="create_savepoint"` is what actually makes this
    # safe — see the module docstring. Every `session.commit()`/
    # `session.rollback()` the application performs while handling one
    # generated request is transparently scoped to its own SAVEPOINT;
    # `outer_transaction` is the only thing this fixture itself ever needs
    # to roll back.
    session = Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    def override_get_db() -> Iterator[Session]:
        try:
            yield session
        finally:
            session.rollback()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield schemathesis.openapi.from_asgi("/openapi.json", app)
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_no_endpoint_returns_a_server_error(case: Case) -> None:
    case.call_and_validate(headers=MANAGER_HEADERS, checks=[schemathesis.checks.not_a_server_error])


def _flux_recipe_body(name: str) -> dict[str, object]:
    return {
        "name": name,
        "matrix_type": "silicate",
        "nominal_portion_g": "30",
        "litharge_g": "60",
        "soda_ash_g": "90",
        "borax_g": "30",
        "silica_g": "15",
        "flour_g": "3",
        "nitre_g": "0",
    }


class TestFixtureIsolation:
    """A direct regression test for the isolation bug this fixture's own
    ``api_schema`` used to have — not exercised through Schemathesis's own
    randomness, so it fails deterministically if the isolation ever breaks
    again, rather than only occasionally when a fuzz run happens to land on
    the right mix of inputs.

    Found the hard way: a plain ``Session(bind=connection)`` wrapping an
    externally-``connection.begin()``'d transaction *looks* isolated
    because ``session.commit()`` correctly defers to the external owner —
    but ``session.rollback()`` does not defer the same way, and ends the
    whole outer transaction for real the first time it runs. Fuzzing
    triggers exactly that: most generated requests are refused (a
    validation error), which calls ``session.rollback()`` on nearly every
    request. Reproduced here directly: one request that succeeds, followed
    by one that is refused (calling ``session.rollback()``, the trigger),
    followed by a second request that succeeds — without
    ``join_transaction_mode="create_savepoint"`` on the fixture's session,
    that second success used to commit to the database for real.
    """

    def test_a_success_after_a_refusal_does_not_leak(self, app_engine: Engine) -> None:
        connection = app_engine.connect()
        outer_transaction = connection.begin()
        session = Session(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

        def override_get_db() -> Iterator[Session]:
            try:
                yield session
            finally:
                session.rollback()

        app = create_app()
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        first = client.post(
            "/api/flux-recipes", json=_flux_recipe_body("isolation-a"), headers=MANAGER_HEADERS
        )
        assert first.status_code == 201

        # Refused by the service's own uniqueness check — this is what
        # calls session.rollback() mid-run, the exact trigger.
        refused = client.post(
            "/api/flux-recipes", json=_flux_recipe_body("isolation-a"), headers=MANAGER_HEADERS
        )
        assert refused.status_code == 422

        second = client.post(
            "/api/flux-recipes", json=_flux_recipe_body("isolation-b"), headers=MANAGER_HEADERS
        )
        assert second.status_code == 201

        session.close()
        outer_transaction.rollback()
        connection.close()

        with (
            app_engine.connect() as fresh_connection,
            Session(bind=fresh_connection) as fresh_session,
        ):
            leaked = fresh_session.scalars(
                select(FluxRecipe).where(FluxRecipe.name.in_(["isolation-a", "isolation-b"]))
            ).all()
            assert leaked == []
