"""SentinelClient tests against the real Sentinel contract.

Mocks httpx to simulate Sentinel's actual POST /api/imports response
(multipart, export_file_id, run_ids) and GET /api/exceptions response.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from msa_lims.sentinel.client import SentinelClient, SubmitResult, VerdictResult

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> SentinelClient:
    return SentinelClient(
        base_url="http://sentinel-local:8001",
        token="test-token",
        timeout=5.0,
    )


class TestSubmitBatch:
    def test_successful_submit_returns_export_file_id_and_run_ids(
        self, client: SentinelClient
    ) -> None:
        """Sentinel returns 201 with export_file_id and run_ids."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "export_file_id": 42,
            "filename": "batch_1_qc.csv",
            "content_hash": "sha256:abcdef",
            "outcome": "imported",
            "accepted": 18,
            "quarantined": 0,
            "duplicates": 2,
            "total_rows": 20,
            "run_ids": [101, 102, 103],
            "evaluation_queued": True,
            "message": None,
        }

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_response
            mock_client_cls.return_value = mock_http

            result = client.submit_batch(
                b"sample_id,analyte,value\nAu,1.5",
                batch_id=1,
                instrument_id=5,
                method_id=1,
            )

        assert result.http_status == 201
        assert result.sentinel_import_id == "42"
        assert result.run_ids == [101, 102, 103]
        assert result.error is None

    def test_submit_sends_multipart_not_raw_csv(
        self, client: SentinelClient
    ) -> None:
        """The request must be multipart/form-data, not raw CSV body."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "export_file_id": 1,
            "filename": "test.csv",
            "content_hash": "sha256:aaa",
            "outcome": "imported",
            "accepted": 1,
            "quarantined": 0,
            "duplicates": 0,
            "total_rows": 1,
            "run_ids": [10],
            "evaluation_queued": True,
            "message": None,
        }

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_response
            mock_client_cls.return_value = mock_http

            client.submit_batch(
                b"data",
                batch_id=1,
                instrument_id=5,
                method_id=1,
            )

            call_args = mock_http.post.call_args
            # Must use files= (multipart), not content= (raw body)
            assert "files" in call_args.kwargs
            assert "data" in call_args.kwargs
            assert call_args.kwargs["data"]["instrument_id"] == "5"
            assert call_args.kwargs["data"]["method_id"] == "1"

    def test_submit_sends_auth_header(self, client: SentinelClient) -> None:
        """Bearer token is sent in headers."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "export_file_id": 1,
            "filename": "test.csv",
            "content_hash": "sha256:aaa",
            "outcome": "imported",
            "accepted": 1,
            "quarantined": 0,
            "duplicates": 0,
            "total_rows": 1,
            "run_ids": [],
            "evaluation_queued": True,
            "message": None,
        }

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_response
            mock_client_cls.return_value = mock_http

            client.submit_batch(
                b"data",
                batch_id=1,
                instrument_id=5,
                method_id=1,
            )

            call_args = mock_client_cls.call_args
            headers = call_args.kwargs.get("headers", {})
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer test-token"
            # Must NOT have Content-Type: text/csv
            assert headers.get("Content-Type") != "text/csv"

    def test_submit_server_error_retries_once(
        self, client: SentinelClient
    ) -> None:
        """500 errors trigger one retry, then return the error."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        ok_response = MagicMock(spec=httpx.Response)
        ok_response.status_code = 201
        ok_response.json.return_value = {
            "export_file_id": 1,
            "filename": "test.csv",
            "content_hash": "sha256:aaa",
            "outcome": "imported",
            "accepted": 1,
            "quarantined": 0,
            "duplicates": 0,
            "total_rows": 1,
            "run_ids": [10],
            "evaluation_queued": True,
            "message": None,
        }

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.side_effect = [error_response, ok_response]
            mock_client_cls.return_value = mock_http

            result = client.submit_batch(
                b"data",
                batch_id=1,
                instrument_id=5,
                method_id=1,
            )

        assert result.http_status == 201
        assert mock_http.post.call_count == 2

    def test_submit_connection_error_returns_error(
        self, client: SentinelClient
    ) -> None:
        """Connection errors are caught and returned, not raised."""
        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.side_effect = httpx.ConnectError("connection refused")
            mock_client_cls.return_value = mock_http

            result = client.submit_batch(
                b"data",
                batch_id=1,
                instrument_id=5,
                method_id=1,
            )

        assert result.http_status is None
        assert result.error is not None
        assert "connection refused" in result.error


class TestGetRunEvaluations:
    def test_queries_exceptions_per_run(self, client: SentinelClient) -> None:
        """Queries GET /api/exceptions for each run_id."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 1, "run_id": 101, "rule": "blank_threshold", "verdict": "exception"},
            {"id": 2, "run_id": 101, "rule": "duplicate_rpd", "verdict": "pass"},
        ]

        with patch.object(httpx, "Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = mock_response
            mock_client_cls.return_value = mock_http

            result = client.get_run_evaluations([101, 102])

        assert result.verdict is not None
        assert result.verdict["run_ids"] == [101, 102]
        assert result.verdict["exception_count"] >= 1
        assert result.error is None

    def test_empty_run_ids_returns_none(self, client: SentinelClient) -> None:
        result = client.get_run_evaluations([])
        assert result.verdict is None
        assert result.error is None


class TestDevHeadersAuth:
    def test_dev_mode_sends_x_actor_headers(self) -> None:
        """In dev_headers auth mode, X-Actor and X-Actor-Role are sent."""
        with patch("msa_lims.sentinel.client.get_settings") as mock_settings:
            mock_settings.return_value.sentinel_base_url = "http://localhost:8001"
            mock_settings.return_value.sentinel_token = ""
            mock_settings.return_value.sentinel_timeout_seconds = 5.0
            mock_settings.return_value.sentinel_auth_mode = "dev_headers"
            mock_settings.return_value.sentinel_actor = "msa-lims@test"

            client = SentinelClient()
            headers = client._headers()

        assert headers.get("X-Actor") == "msa-lims@test"
        assert headers.get("X-Actor-Role") == "analyst"
        assert "Authorization" not in headers
        assert "Content-Type" not in headers
