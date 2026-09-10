"""Shared fixtures.

Settings are supplied here so the suite never depends on a developer's .env or
on any Google credentials. Everything under test is reachable without network.
"""

import io

import pytest
from pypdf import PdfWriter

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Pin configuration for the whole suite.

    Without this, `Settings()` reads the developer's .env, so a test would pass
    or fail depending on whose machine it ran on.
    """
    fixed = Settings(
        google_client_id="test.apps.googleusercontent.com",
        google_client_secret="test-secret",
        public_base_url="https://test.example.com",
        drive_folder_id="test-folder",
        token_store_backend="memory",
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: fixed)
    yield fixed
    get_settings.cache_clear()


@pytest.fixture
def scanned_pdf() -> bytes:
    """A PDF with pages but no text layer, standing in for a scan."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
