"""Single source of truth for every runtime knob.

Nothing else in the codebase reads os.environ, so there is exactly one place
to check that no credential is hardcoded.
"""

from functools import lru_cache
from typing import Literal
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env", extra="ignore")

    # --- Google OAuth client ---
    google_client_id: str
    google_client_secret: SecretStr

    # --- This server's public origin (no trailing slash) ---
    # Used to build: the Google redirect URI, the OAuth metadata documents,
    # and the `resource` identifier Claude Code binds its token to.
    public_base_url: str

    # --- Drive ---
    drive_folder_id: str

    # --- Storage ---
    token_store_backend: Literal["firestore", "memory"] = "memory"
    gcp_project_id: str | None = None
    firestore_database: str = "(default)"

    # --- Misc ---
    log_level: str = "INFO"

    @field_validator("public_base_url", mode="after")
    @classmethod
    def _normalise_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError(
                f"PUBLIC_BASE_URL must start with http:// or https:// (got {v!r})"
            )
        return v

    @model_validator(mode="after")
    def _check_backend_requirements(self) -> "Settings":
        if self.token_store_backend == "firestore" and not self.gcp_project_id:
            raise ValueError(
                "TOKEN_STORE_BACKEND='firestore' requires GCP_PROJECT_ID to be set."
            )
        return self

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.public_base_url}/oauth/google/callback"

    @property
    def resource_identifier(self) -> str:
        return f"{self.public_base_url}/mcp"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance.

    Cached so config is parsed once, and so tests can override via
    get_settings.cache_clear().
    """
    return Settings()

