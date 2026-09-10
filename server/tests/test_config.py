"""Configuration rules.

Each of these exists because getting it wrong produces a failure that is hard
to trace back to configuration.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE = {
    "google_client_id": "test.apps.googleusercontent.com",
    "google_client_secret": "test-secret",
    "drive_folder_id": "folder-1",
}


def _settings(**overrides) -> Settings:
    # _env_file=None matters: without it, any field not passed here is read from
    # the developer's .env, so "not specified" would silently mean "whatever is
    # on this machine" rather than "the declared default".
    return Settings(
        _env_file=None,
        **{**_BASE, "public_base_url": "https://example.run.app", **overrides},
    )


class TestBaseUrlNormalisation:
    @pytest.mark.parametrize(
        "given",
        [
            "https://example.run.app",
            "https://example.run.app/",
            "https://example.run.app///",
            "  https://example.run.app/  ",
        ],
    )
    def test_trailing_slashes_are_stripped(self, given):
        """Google compares redirect URIs character by character. A trailing
        slash here produces "//oauth/google/callback" downstream and fails with
        redirect_uri_mismatch — an error that never says which character is
        wrong."""
        assert _settings(public_base_url=given).google_redirect_uri == (
            "https://example.run.app/oauth/google/callback"
        )

    def test_a_missing_scheme_is_rejected(self):
        """Catches a bare host pasted from the Cloud Run console."""
        with pytest.raises(ValidationError, match="http"):
            _settings(public_base_url="example.run.app")


class TestDerivedValues:
    def test_redirect_uri_and_resource_follow_the_base_url(self):
        """Derived rather than configured, so they cannot drift apart when the
        service moves between local and deployed."""
        settings = _settings(public_base_url="http://localhost:8080")

        assert (
            settings.google_redirect_uri
            == "http://localhost:8080/oauth/google/callback"
        )
        assert settings.resource_identifier == "http://localhost:8080/mcp"


class TestBackendRequirements:
    def test_firestore_without_a_project_id_fails_at_startup(self):
        """A misconfigured revision should die on boot with a readable message,
        rather than appearing healthy and failing at the first token write."""
        with pytest.raises(ValidationError, match="GCP_PROJECT_ID"):
            _settings(token_store_backend="firestore", gcp_project_id=None)

    def test_memory_backend_needs_no_project(self):
        assert _settings(token_store_backend="memory").token_store_backend == "memory"


class TestSecretHandling:
    def test_the_client_secret_does_not_appear_in_output(self):
        """SecretStr keeps the value out of logs and tracebacks. The cost is
        having to unwrap it explicitly wherever it is genuinely needed — a
        trade that fails loudly rather than leaking quietly."""
        settings = _settings()

        assert "test-secret" not in repr(settings)
        assert "test-secret" not in str(settings)
        assert settings.google_client_secret.get_secret_value() == "test-secret"


class TestRequiredFields:
    @pytest.mark.parametrize(
        "field", ["google_client_id", "google_client_secret", "drive_folder_id"]
    )
    def test_required_fields_have_no_default(self, field):
        """Defaults would let the service start with the wrong configuration and
        say nothing about it."""
        values = {**_BASE, "public_base_url": "https://example.run.app"}
        values.pop(field)

        with pytest.raises(ValidationError, match=field):
            Settings(_env_file=None, **values)
