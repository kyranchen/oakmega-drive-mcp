"""The two tools. Thin wrappers over drive/.

Each tool body is: resolve the caller, call into drive/, format, return. Any
logic worth testing belongs a layer down.

DOCSTRINGS HERE ARE PROMPT, NOT DOCUMENTATION. The SDK sends a tool's
docstring and parameter annotations to the model as its description, so this
is the entire interface between what a user says and what this code does. A
vague description is the most common reason a working MCP server appears to do
nothing: the model never picks the tool.
"""

import base64
import logging

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ImageContent, TextContent

from ..config import get_settings
from ..drive.client import get_drive_service
from ..drive.content import read_file_content
from ..drive.extractors import ExtractedImage
from ..drive.listing import (
    assert_within_folder,
    get_file_metadata,
    list_folder_tree,
)
from ..errors import DriveMCPError, FileOutsideAllowedFolder, NotAuthorized
from .server import mcp

logger = logging.getLogger(__name__)

_REAUTH_HINT = (
    "Run /mcp in Claude Code and reconnect the google-drive server to sign in again."
)


def _current_subject() -> str:
    """The Google account this request is acting for.

    Read from the authenticated token rather than taken as a tool argument:
    a tool's signature is the schema the model sees, and an identity the model
    could fill in is an identity it could choose.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise NotAuthorized(f"This request is not authenticated. {_REAUTH_HINT}")
    return token.subject


def _human_size(size: int | None) -> str:
    if size is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _describe(error: DriveMCPError) -> str:
    """Turn a domain error into something a user can act on.

    The extraction errors already carry a specific message — a scanned PDF says
    so, an unsupported type lists what is supported — so they are passed
    through rather than flattened into a generic failure.
    """
    if isinstance(error, NotAuthorized):
        return f"{error} {_REAUTH_HINT}"
    if isinstance(error, FileOutsideAllowedFolder):
        return f"{error} Use list_files to see what this server is allowed to read."
    return str(error)


@mcp.tool()
async def list_files() -> str:
    """List every file in the configured Google Drive folder, including files
    inside subfolders.

    Returns one line per file with its path, type, size and file ID. Use this
    before read_file: the user will refer to a file by name or path, and the
    ID needed to read it comes from here.
    """
    subject = _current_subject()
    folder_id = get_settings().drive_folder_id

    try:
        service = await get_drive_service(subject)
        files = await list_folder_tree(service, folder_id)
    except DriveMCPError as exc:
        logger.warning("list_files failed for subject %s: %s", subject, exc)
        return _describe(exc)

    if not files:
        return "The shared folder is empty."

    lines = [f"{len(files)} file(s) in the shared folder:", ""]
    for file in sorted(files, key=lambda f: (f.path or f.name).lower()):
        lines.append(f"• {file.path or file.name}")
        lines.append(
            f"    type: {file.mime_type}  size: {_human_size(file.size)}  id: {file.id}"
        )
    return "\n".join(lines)


@mcp.tool()
async def read_file(file_id: str) -> list[TextContent | ImageContent]:
    """Read the contents of one file from the configured Google Drive folder.

    Pass the file ID from list_files. Text documents, PDFs and native Google
    Docs, Sheets and Slides come back as text; images come back as an image
    you can look at directly.

    Args:
        file_id: The Drive file ID, as shown by list_files.
    """
    subject = _current_subject()
    folder_id = get_settings().drive_folder_id

    try:
        service = await get_drive_service(subject)
        await assert_within_folder(service, file_id, folder_id)
        metadata = await get_file_metadata(service, file_id)
        result = await read_file_content(service, metadata)
    except DriveMCPError as exc:
        logger.warning("read_file(%s) failed for subject %s: %s", file_id, subject, exc)
        return [TextContent(type="text", text=_describe(exc))]

    if isinstance(result, ExtractedImage):
        # The image arrives with no context of its own, so it is introduced by
        # a text block naming the file it came from.
        return [
            TextContent(
                type="text",
                text=f"'{metadata.name}' ({result.mime_type}, {_human_size(metadata.size)}):",
            ),
            ImageContent(
                type="image",
                # ImageContent.data is base64 text, not bytes. This is the only
                # place in the project where that encoding happens; extractors
                # deal in raw bytes so they stay testable.
                data=base64.b64encode(result.data).decode(),
                mime_type=result.mime_type,
            ),
        ]

    header = f"'{metadata.name}'"
    if result.note:
        header += f" ({result.note})"
    if result.truncated:
        header += " — truncated, showing the beginning of the file only"

    return [TextContent(type="text", text=f"{header}:\n\n{result.text}")]
