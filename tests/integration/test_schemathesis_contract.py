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

Every generated example for one operation runs inside its own
:meth:`Session.begin_nested` savepoint (the same primitive
``db/numbering.py`` uses for race-safe retries), unconditionally rolled back
afterward — not merely wrapped in the fixture's one outer transaction like
every other integration test. A crash Schemathesis triggers is often a raw,
uncaught database error (a CHECK constraint reached directly, bypassing the
service-level pre-check that normally refuses it first), which leaves
Postgres refusing every further command until a rollback happens. Sharing
one plain transaction across many generated examples would let the first
such crash poison every example after it in the same operation, burying the
one request that actually mattered under a wall of identical "transaction is
aborted" noise — the savepoint is what keeps each generated example an
independent trial, the way each one would be an independent request in
production.

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
from hypothesis import HealthCheck, settings
from schemathesis import Case
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from msa_lims.web.app import create_app
from msa_lims.web.deps import get_db

pytestmark = [pytest.mark.integration, pytest.mark.fuzz]

MANAGER_HEADERS = {"X-Actor": "fuzz@lab", "X-Actor-Role": "lab_manager"}


@pytest.fixture
def api_schema(app_engine: Engine) -> Iterator[schemathesis.BaseSchema]:
    connection = app_engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        """One savepoint per fake request — see the module docstring.

        ``session.rollback()``, not the returned ``SessionTransaction``'s own
        ``.rollback()``: a raw ``IntegrityError`` during flush (a CHECK
        constraint reached directly, past the service's own pre-check)
        leaves the ORM Session itself flagged as needing an explicit
        rollback, not only the savepoint — ``Session.rollback()`` is what
        SQLAlchemy's own error message names as the recovery, and it
        correctly unwinds to the savepoint either way.
        """
        session.begin_nested()
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
        # A route's own successful `session.commit()` can end the outer
        # transaction's association with the connection before this runs
        # (the savepoint machinery above interacts with SQLAlchemy's own
        # transaction-stack tracking in a way the simpler fixtures in every
        # other integration test file never trigger, since none of them nest
        # a transaction). Guarded rather than unconditional so cleanup after
        # a run that ended this way doesn't raise on an already-ended
        # transaction.
        if connection.in_transaction():
            outer_transaction.rollback()
        connection.close()


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_no_endpoint_returns_a_server_error(case: Case) -> None:
    case.call_and_validate(headers=MANAGER_HEADERS, checks=[schemathesis.checks.not_a_server_error])
