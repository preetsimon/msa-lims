"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from msa_lims.clients.service import (
    ClientNotFoundError,
    ClientValidationError,
    ProjectNotFoundError,
    ProjectValidationError,
)
from msa_lims.config import get_settings
from msa_lims.domain.assay import AssayCalculationError
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
    FireAssayResultValidationError,
    SampleNotFoundError,
)
from msa_lims.submissions.service import SubmissionValidationError
from msa_lims.web.routes import (
    clients,
    drill_holes,
    fire_assay_results,
    health,
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
    app.include_router(submissions.router)
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
    FireAssayResultValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SubmissionValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def _register_error_handlers(app: FastAPI) -> None:
    for error_type, status_code in _ERROR_STATUS.items():

        def handler(request: Request, exc: Exception, *, code: int = status_code) -> JSONResponse:
            # The messages these carry are written for the person who hit the
            # refusal — they name what is missing or who could do it — so they
            # are passed through rather than replaced with a generic string.
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        app.add_exception_handler(error_type, handler)


app = create_app()
