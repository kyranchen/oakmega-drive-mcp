"""The MCP tool layer.

The point of these is wiring, not logic. A guard that exists but is never
called passes every test written about the guard itself.
"""

from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.auth.provider import AccessToken

from app.drive.extractors import ExtractedText
from app.drive.listing import DriveFile
from app.errors import FileOutsideAllowedFolder, NotAuthorized
from app.mcp_server import tools


@pytest.fixture
def authenticated():
    """Stand in for the bearer token the SDK's middleware resolves."""
    with patch.object(
        tools,
        "get_access_token",
        return_value=AccessToken(
            token="hash", client_id="client-1", scopes=[], subject="google-sub-1"
        ),
    ):
        yield


class TestIdentity:
    def test_subject_is_not_a_tool_argument(self):
        """A tool's signature is the schema the model sees. An identity the
        model can fill in is an identity it can choose."""
        import inspect

        assert "subject" not in inspect.signature(tools.read_file).parameters
        assert "subject" not in inspect.signature(tools.list_files).parameters

    async def test_an_unauthenticated_request_is_refused(self):
        with (
            patch.object(tools, "get_access_token", return_value=None),
            pytest.raises(NotAuthorized),
        ):
            tools._current_subject()

    async def test_a_token_without_a_subject_is_refused(self):
        """Identity travels on `subject`; a token missing it cannot name a Drive."""
        token = AccessToken(token="h", client_id="c", scopes=[], subject=None)
        with (
            patch.object(tools, "get_access_token", return_value=token),
            pytest.raises(NotAuthorized),
        ):
            tools._current_subject()


class TestFolderBoundaryIsEnforced:
    """Removing the call from read_file used to pass the whole suite, because
    every other test exercised assert_within_folder directly."""

    async def test_read_file_checks_the_boundary(self, authenticated):
        guard = AsyncMock()
        with (
            patch.object(tools, "get_drive_service", AsyncMock()),
            patch.object(tools, "assert_within_folder", guard),
            patch.object(tools, "get_file_metadata", AsyncMock()),
            patch.object(tools, "read_file_content", AsyncMock()),
        ):
            await tools.read_file(file_id="some-file")

        guard.assert_awaited_once()
        assert guard.await_args.args[1] == "some-file"

    async def test_the_boundary_is_checked_before_anything_is_fetched(
        self, authenticated
    ):
        """Checking after the download would mean the content already reached
        this process."""
        calls: list[str] = []

        async def guard(*a, **kw):
            calls.append("guard")

        async def metadata(*a, **kw):
            calls.append("metadata")
            return DriveFile(
                id="some-file",
                name="doc.txt",
                mime_type="text/plain",
                size=10,
                modified_time=None,
            )

        with (
            patch.object(tools, "get_drive_service", AsyncMock()),
            patch.object(tools, "assert_within_folder", guard),
            patch.object(tools, "get_file_metadata", metadata),
            patch.object(
                tools,
                "read_file_content",
                AsyncMock(return_value=ExtractedText(text="body")),
            ),
        ):
            await tools.read_file(file_id="some-file")

        assert calls == ["guard", "metadata"]

    async def test_a_refusal_becomes_a_readable_message(self, authenticated):
        async def guard(*a, **kw):
            raise FileOutsideAllowedFolder("File x is not inside the shared folder.")

        with (
            patch.object(tools, "get_drive_service", AsyncMock()),
            patch.object(tools, "assert_within_folder", guard),
        ):
            blocks = await tools.read_file(file_id="outside")

        text = blocks[0].text
        assert "not inside the shared folder" in text
        assert "list_files" in text, "should say how to find what is readable"
