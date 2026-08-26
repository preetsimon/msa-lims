"""Application settings, read from the environment.

Everything configurable lives here rather than being read from ``os.environ``
at the point of use, so the full set of knobs is one file to read and one
object to override in tests.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MSA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["local", "ci", "staging", "production"] = "local"
    log_level: str = "INFO"
    # JSON in a deployment, human-readable lines locally. Same fields either way.
    log_json: bool = False

    # The application connects as a restricted role that holds no UPDATE or
    # DELETE grant on the append-only tables — results, certificates, audit.
    # Migrations connect as the schema owner, because an owner bypasses grants
    # and so could not be constrained by them. Keeping the two URLs separate is
    # what makes the append-only guarantee enforceable rather than aspirational.
    database_url: str = "postgresql+psycopg://msa_app:msa_app@localhost:5435/msa"
    migration_database_url: str = "postgresql+psycopg://msa:msa@localhost:5435/msa"

    # Authentication. "dev_headers" trusts X-Actor and is refused outside local
    # and CI; "oidc" verifies bearer tokens against a provider. There is no mode
    # that skips signature verification.
    auth_mode: Literal["dev_headers", "oidc"] = "dev_headers"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_groups_claim: str = "groups"
    oidc_name_claim: str = "email"
    # "lab-analysts=analyst,lab-supervisors=supervisor,lab-managers=lab_manager".
    # A group with no mapping grants nothing; there is deliberately no default.
    oidc_role_map: str = ""

    # --- QC Sentinel integration -------------------------------------------
    # The LIMS pushes each completed batch's QC rows to Sentinel and reads back
    # advisory verdicts. Off by default and non-fatal when it fails: Sentinel
    # being down must never stop the lab from assaying samples, which is the
    # whole point of the two systems being separate.
    sentinel_enabled: bool = False
    sentinel_base_url: str = "http://localhost:8001"
    sentinel_timeout_seconds: float = 10.0

    # --- Laboratory defaults ------------------------------------------------
    # Nominal fire assay portion in grams. 30 g is the modern convention; the
    # classical assay ton is 29.1666… g. Configurable because it is a per-lab
    # choice, and recorded on every batch so a change does not rewrite history.
    default_assay_portion_g: str = "30.0"
    # The contamination threshold QC dossier blanks are flagged against (g/t).
    # Advisory only — a flag here records that a blank came back above the
    # lab's line; deciding what that means is QC Sentinel's job. Configurable
    # because it is lab QA policy, not chemistry.
    blank_max_grade_g_t: str = "0.05"
    # Furnace tray geometry. The batch builder draws this grid, and a position
    # outside it is refused.
    furnace_rows: int = 6
    furnace_columns: int = 6

    max_upload_bytes: int = Field(
        default=50 * 1024 * 1024,
        description=(
            "Uploads are read fully into memory to be hashed and parsed, so the "
            "cap is what keeps one request from exhausting the process."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
