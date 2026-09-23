"""QC Sentinel HTTP client — bounded, non-fatal, advisory.

The client sends a batch's QC export to Sentinel and polls for per-run
evaluations.  Three design constraints are non-negotiable:

1. **Non-fatal.**  Sentinel being down must never stop the lab from assaying
   samples.  Every public function catches transport errors and returns a
   result object rather than raising.

2. **Append-only record.**  Every attempt — successful or not — is recorded
   in ``sentinel_submission``.  The ``http_status`` column is the fact; the
   verdict is an optional follow-up.

3. **Advisory disposition.**  The verdict is stored as-is and never written
   back to any result or sample row.  Judging is Sentinel's job; the LIMS
   just remembers what Sentinel said.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from msa_lims.config import get_settings

__all__ = [
    "SentinelClient",
    "SubmitResult",
    "VerdictResult",
]

_SENTINEL_TIMEOUT = 10.0


class SentinelClient:
    """A thin HTTP client for QC Sentinel.

    Uses ``httpx`` with a hard timeout and bounded retry on 5xx / connection
    errors only.  Retries are limited to 2 attempts (1 initial + 1 retry) to
    keep the total latency bounded.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.sentinel_base_url).rstrip("/")
        self._token = token or settings.sentinel_token
        self._timeout = timeout or settings.sentinel_timeout_seconds

    def _headers(self) -> dict[str, str]:
        """Build request headers.  In dev mode, send X-Actor / X-Actor-Role
        headers (the same convention this app uses internally).  In production,
        send a Bearer token.  Never set Content-Type — httpx needs to set the
        multipart boundary itself."""
        settings = get_settings()
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif getattr(settings, "sentinel_auth_mode", "dev_headers") == "dev_headers":
            headers["X-Actor"] = getattr(settings, "sentinel_actor", "msa-lims@localhost")
            headers["X-Actor-Role"] = "analyst"
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
        )

    def submit_batch(
        self,
        payload: bytes,
        *,
        fmt: str = "generic_csv_v1",
        batch_id: int,
        instrument_id: int,
        method_id: int,
        filename: str = "qc_export.csv",
    ) -> SubmitResult:
        """POST a CSV export to Sentinel as multipart/form-data.

        Sentinel's ``POST /api/imports`` expects multipart fields:
        ``file`` (the CSV), ``instrument_id`` (required int),
        ``method_id`` (required int), and optional ``parser_format``.

        Returns a ``SubmitResult`` regardless of success or failure.  The
        caller records the result in ``sentinel_submission`` — this function
        does not touch the database.
        """
        with self._client() as client:
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    resp = client.post(
                        "/api/imports",
                        files={"file": (filename, payload, "text/csv")},
                        data={
                            "instrument_id": str(instrument_id),
                            "method_id": str(method_id),
                            "parser_format": fmt,
                        },
                    )
                    if resp.status_code >= 500 and attempt == 0:
                        last_exc = httpx.TransportError(
                            f"Sentinel returned {resp.status_code}"
                        )
                        continue
                    return SubmitResult(
                        http_status=resp.status_code,
                        sentinel_import_id=_extract_export_file_id(resp),
                        run_ids=_extract_run_ids(resp),
                        error=None,
                    )
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_exc = exc
                    if attempt == 0:
                        continue
                    return SubmitResult(
                        http_status=None,
                        sentinel_import_id=None,
                        run_ids=[],
                        error=str(exc),
                    )
            return SubmitResult(
                http_status=None,
                sentinel_import_id=None,
                run_ids=[],
                error=str(last_exc) if last_exc else "unknown error",
            )

    def get_run_evaluations(self, run_ids: list[int]) -> VerdictResult:
        """Query Sentinel for evaluations of specific runs.

        Sentinel does not have a per-import verdict endpoint.  Verdicts are
        per-run, surfaced through the exceptions review queue or evaluation
        exports.  This method queries ``GET /api/exceptions`` filtered by
        run_id to check if any exceptions were raised for the batch's runs.

        Returns a ``VerdictResult`` with the combined evaluation status.
        """
        if not run_ids:
            return VerdictResult(verdict=None, error=None)

        with self._client() as client:
            try:
                # Query exceptions for our run IDs.  Sentinel's GET /api/exceptions
                # supports filtering by run_id.
                exceptions: list[dict[str, object]] = []
                for run_id in run_ids:
                    resp = client.get("/api/exceptions", params={"run_id": run_id})
                    if resp.status_code == 200:
                        body = resp.json()
                        if isinstance(body, list):
                            exceptions.extend(body)
                        elif isinstance(body, dict) and "items" in body:
                            items = body["items"]
                            if isinstance(items, list):
                                exceptions.extend(items)

                return VerdictResult(
                    verdict={
                        "run_ids": run_ids,
                        "exception_count": len(exceptions),
                        "exceptions": exceptions[:10],  # cap for payload size
                    },
                    error=None,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                return VerdictResult(
                    verdict=None,
                    error=str(exc),
                )


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """The outcome of a POST to Sentinel's import endpoint."""

    http_status: int | None
    sentinel_import_id: str | None
    run_ids: list[int] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class VerdictResult:
    """The outcome of a GET to Sentinel's exceptions/evaluations."""

    verdict: dict[str, object] | None
    error: str | None = None


def _extract_export_file_id(resp: httpx.Response) -> str | None:
    """Extract the export_file_id from Sentinel's ImportResponse."""
    try:
        body: dict[str, object] = resp.json()
        if isinstance(body, dict) and "export_file_id" in body:
            return str(body["export_file_id"])
    except Exception:
        pass
    return None


def _extract_run_ids(resp: httpx.Response) -> list[int]:
    """Extract run_ids from Sentinel's ImportResponse."""
    try:
        body: dict[str, object] = resp.json()
        if isinstance(body, dict) and "run_ids" in body:
            raw = body["run_ids"]
            if isinstance(raw, list):
                return [int(r) for r in raw if isinstance(r, (int, str))]
    except Exception:
        pass
    return []
