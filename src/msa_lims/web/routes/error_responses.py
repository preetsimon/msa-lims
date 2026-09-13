"""Shared error response declarations for OpenAPI ``responses`` declarations.

Every route that can raise a domain exception should import the matching
response dict from here and pass it in the ``responses={}`` parameter of
its ``@router`` decorator.  This tells Schemathesis and other schema-aware
tools about the ``{"detail": "..."}`` shape FastAPI's error handler returns,
rather than leaving them to assume every non-2xx response is an opaque blob.

The pattern — use ``merge_responses()`` to combine multiple error dicts::

    from msa_lims.web.routes.error_responses import NOT_FOUND_404, CONFLICT_409, merge_responses

    @router.post("", responses=merge_responses(NOT_FOUND_404, CONFLICT_409))
"""

from __future__ import annotations

from typing import Any

from msa_lims.web.schemas import ErrorResponse

# Keys are int (status codes) to match FastAPI's expected responses type:
# dict[int | str, dict[str, Any]]
RESPONSE_403: dict[int, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "Insufficient role"},
}
RESPONSE_404: dict[int, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Resource not found"},
}
RESPONSE_409: dict[int, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Conflict (duplicate name, wrong state, etc.)"},
}
RESPONSE_422: dict[int, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Validation error"},
}
RESPONSE_500: dict[int, dict[str, Any]] = {
    500: {"model": ErrorResponse, "description": "Internal server error"},
}


def merge_responses(*responses: dict[int, dict[str, Any]]) -> dict[int | str, dict[str, Any]]:
    """Merge multiple error response dicts into one for the ``responses`` kwarg."""
    merged: dict[int | str, dict[str, Any]] = {}
    for r in responses:
        for key, value in r.items():
            merged[key] = value
    return merged


# Common combinations
NOT_FOUND_404 = RESPONSE_404
CONFLICT_409 = RESPONSE_409
VALIDATION_422 = RESPONSE_422
FORBIDDEN_403 = RESPONSE_403
CLIENT_OR_PROJECT_NOT_FOUND = {**RESPONSE_404}
CLIENT_CONFLICT = {**RESPONSE_409}
CLIENT_VALIDATION = {**RESPONSE_422}
SAMPLE_NOT_FOUND = {**RESPONSE_404}
SAMPLE_CONFLICT = {**RESPONSE_409}
BATCH_NOT_FOUND = {**RESPONSE_404}
BATCH_CONFLICT = {**RESPONSE_409}
CERTIFICATE_NOT_FOUND = {**RESPONSE_404}
CERTIFICATE_VALIDATION = {**RESPONSE_422}
CERTIFICATE_CORRUPTED = {**RESPONSE_500}
FLUX_RECIPE_NOT_FOUND = {**RESPONSE_404}
FLUX_RECIPE_CONFLICT = {**RESPONSE_409}
FLUX_RECIPE_VALIDATION = {**RESPONSE_422}
QC_MATERIAL_NOT_FOUND = {**RESPONSE_404}
QC_MATERIAL_VALIDATION = {**RESPONSE_422}
INSTRUMENT_NOT_FOUND = {**RESPONSE_404}
INSTRUMENT_CONFLICT = {**RESPONSE_409}
