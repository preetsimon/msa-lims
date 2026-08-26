"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError

from msa_lims.batches.service import (
    BatchNotFoundError,
    BatchValidationError,
    CrucibleValidationError,
)
from msa_lims.certificates.service import (
    CertificateCorruptedError,
    CertificateNotFoundError,
    CertificateValidationError,
)
from msa_lims.clients.service import (
    ClientNotFoundError,
    ClientValidationError,
    ProjectNotFoundError,
    ProjectValidationError,
)
from msa_lims.config import get_settings
from msa_lims.domain.assay import AssayCalculationError
from msa_lims.domain.batch_lifecycle import FurnacePositionError
from msa_lims.domain.flux import FluxCalculationError
from msa_lims.domain.lifecycle import (
    InsufficientRoleError,
    ReasonRequiredError,
    TransitionNotAllowedError,
)
from msa_lims.domain.sample_id import DepthIntervalError, SampleIdError
from msa_lims.domain.units import UnitError
from msa_lims.domain.values import ValueParseError
from msa_lims.drill_holes.service import DrillHoleValidationError
from msa_lims.fire_assay_results.service import (
    CrucibleNotFoundError,
    FireAssayResultValidationError,
    SampleNotFoundError,
)
from msa_lims.flux_recipes.service import FluxRecipeNotFoundError, FluxRecipeValidationError
from msa_lims.qc_materials.service import QcMaterialNotFoundError, QcMaterialValidationError
from msa_lims.submissions.service import SubmissionValidationError
from msa_lims.web.routes import (
    batches,
    certificates,
    clients,
    drill_holes,
    fire_assay_results,
    flux_recipes,
    health,
    qc_materials,
    sample_lifecycle,
    samples,
    submissions,
    whoami,
)

DESCRIPTION = """
Laboratory information management for fire assay and geochemical analysis:
sample custody, preparation, fire assay batching, results, and certificates.

MSA LIMS is the **system of record**. Quality control surveillance is a separate
system — [QC Sentinel](http://localhost:8001/docs) — which reads QC results
exported from here and returns advisory verdicts. Sentinel never writes to this
database, and nothing it concludes can block a result from being reported.
"""


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="MSA LIMS",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(whoami.router)
    app.include_router(clients.router)
    app.include_router(drill_holes.router)
    app.include_router(fire_assay_results.router)
    app.include_router(certificates.router)
    app.include_router(samples.router)
    app.include_router(sample_lifecycle.router)
    app.include_router(submissions.router)
    app.include_router(flux_recipes.router)
    app.include_router(batches.router)
    app.include_router(qc_materials.router)
    _register_error_handlers(app)
    return app


#: Domain refusals mapped to the status code that means what they mean. Each
#: says something different to a client, and collapsing them all to 400 would
#: throw that away: a 403 means find someone with authority, a 409 means the
#: sample moved under you, and a 422 means the request itself was wrong.
_ERROR_STATUS: dict[type[Exception], int] = {
    InsufficientRoleError: status.HTTP_403_FORBIDDEN,
    TransitionNotAllowedError: status.HTTP_409_CONFLICT,
    ReasonRequiredError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SampleIdError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DepthIntervalError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ValueParseError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnitError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AssayCalculationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ClientNotFoundError: status.HTTP_404_NOT_FOUND,
    ClientValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProjectNotFoundError: status.HTTP_404_NOT_FOUND,
    ProjectValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DrillHoleValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SampleNotFoundError: status.HTTP_404_NOT_FOUND,
    CrucibleNotFoundError: status.HTTP_404_NOT_FOUND,
    FireAssayResultValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SubmissionValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CertificateNotFoundError: status.HTTP_404_NOT_FOUND,
    CertificateValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # A stored PDF that no longer hashes to what its row claims is a data
    # integrity failure, not something the caller did wrong.
    CertificateCorruptedError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    FluxRecipeNotFoundError: status.HTTP_404_NOT_FOUND,
    FluxRecipeValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FluxCalculationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FurnacePositionError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BatchNotFoundError: status.HTTP_404_NOT_FOUND,
    BatchValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CrucibleValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    QcMaterialNotFoundError: status.HTTP_404_NOT_FOUND,
    QcMaterialValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def _register_error_handlers(app: FastAPI) -> None:
    for error_type, status_code in _ERROR_STATUS.items():

        def handler(request: Request, exc: Exception, *, code: int = status_code) -> JSONResponse:
            # The messages these carry are written for the person who hit the
            # refusal — they name what is missing or who could do it — so they
            # are passed through rather than replaced with a generic string.
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        app.add_exception_handler(error_type, handler)

    app.add_exception_handler(DataError, _handle_out_of_range_id)


def _handle_out_of_range_id(request: Request, exc: Exception) -> JSONResponse:
    """An id larger than Postgres ``BIGINT`` can hold — found by Schemathesis
    fuzzing the live app with ``9223372036854775808``, one past the maximum —
    reaches the database as a syntactically valid integer no Pydantic field
    refuses, and Postgres raises a raw ``DataError`` before any query
    executes. Functionally that is "no row has this id", so it is mapped
    the same way every ``*NotFoundError`` in this file already is.

    Written fresh rather than passed through like every handler above:
    ``str(exc)`` here is a raw driver message carrying the failed SQL and its
    bind parameters — fine to surface for a domain refusal a person wrote on
    purpose, not for an unfiltered database error.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "no resource with that id exists"},
    )


app = create_app()
