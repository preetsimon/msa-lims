"""QC Sentinel HTTP client — bounded, non-fatal, advisory.

The client sends a batch's QC export to Sentinel and polls for a verdict.
Three design constraints are non-negotiable:

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

from dataclasses import dataclass

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
        headers = {"Content-Type": "text/csv"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
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
    ) -> SubmitResult:
        """POST a CSV export to Sentinel.

        Returns a ``SubmitResult`` regardless of success or failure.  The
        caller records the result in ``sentinel_submission`` — this function
        does not touch the database.
        """
        with self._client() as client:
            # Bounded retry: up to 1 retry on 5xx or connection errors.
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    resp = client.post(
                        "/api/v1/ingest",
                        content=payload,
                        params={"format": fmt},
                    )
                    if resp.status_code >= 500 and attempt == 0:
                        last_exc = httpx.TransportError(
                            f"Sentinel returned {resp.status_code}"
                        )
                        continue
                    return SubmitResult(
                        http_status=resp.status_code,
                        sentinel_import_id=_extract_import_id(resp),
                        error=None,
                    )
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_exc = exc
                    if attempt == 0:
                        continue
                    return SubmitResult(
                        http_status=None,
                        sentinel_import_id=None,
                        error=str(exc),
                    )
            # Should not reach here, but safety net.
            return SubmitResult(
                http_status=None,
                sentinel_import_id=None,
                error=str(last_exc) if last_exc else "unknown error",
            )

    def poll_verdict(self, sentinel_import_id: str) -> VerdictResult:
        """GET the verdict for a previously submitted import.

        Returns a ``VerdictResult`` regardless of success or failure.
        """
        with self._client() as client:
            try:
                resp = client.get(f"/api/v1/imports/{sentinel_import_id}/verdict")
                if resp.status_code == 200:
                    return VerdictResult(
                        verdict=resp.json(),
                        error=None,
                    )
                return VerdictResult(
                    verdict=None,
                    error=f"Sentinel returned {resp.status_code}",
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                return VerdictResult(
                    verdict=None,
                    error=str(exc),
                )


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """The outcome of a POST to Sentinel's ingest endpoint."""

    http_status: int | None
    sentinel_import_id: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class VerdictResult:
    """The outcome of a GET to Sentinel's verdict endpoint."""

    verdict: dict[str, object] | None
    error: str | None


def _extract_import_id(resp: httpx.Response) -> str | None:
    """Extract the import ID from Sentinel's response body or Location header."""
    # Sentinel may return the import ID in the JSON body or as a Location header.
    try:
        body: dict[str, object] = resp.json()
        if isinstance(body, dict) and "import_id" in body:
            return str(body["import_id"])
    except Exception:
        pass
    location = resp.headers.get("Location", "")
    if location:
        # Extract the last path segment as the import ID.
        parts = location.rstrip("/").split("/")
        if parts:
            return str(parts[-1])
    return None
