"""Dispatch: given a file, pick and run an extraction strategy.

Separated from extractors.py so the routing rules live in one readable place
and each extractor stays testable against raw bytes, with no network.
"""

from ..errors import DriveAPIError, FileTooLarge, UnsupportedFileType
from .client import call
from .extractors import (
    ExtractionResult,
    extract_image,
    extract_pdf,
    extract_plaintext,
)
from .listing import DriveFile

_GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": "text/markdown",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

_TEXT_TYPES = {"application/json", "application/xml", "application/x-yaml"}

MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _is_text(mime_type: str) -> bool:
    return (
        mime_type.startswith("text/")
        or mime_type in _TEXT_TYPES
        or mime_type.endswith(("+xml", "+json"))
    )


async def _download(service, file_id: str) -> bytes:
    try:
        return await call(service.files().get_media(fileId=file_id))
    except Exception as exc:
        raise DriveAPIError(f"Could not download file {file_id}: {exc}") from exc


async def _export(service, file_id: str, export_mime: str) -> bytes:
    try:
        return await call(
            service.files().export_media(fileId=file_id, mimeType=export_mime)
        )
    except Exception as exc:
        # Fallback to text/plain if the requested export type fails
        if export_mime == "text/plain":
            raise DriveAPIError(f"Could not export file {file_id}: {exc}") from exc
        return await _export(service, file_id, "text/plain")


async def read_file_content(service, file: DriveFile) -> ExtractionResult:
    mime = file.mime_type

    if file.is_folder:
        raise UnsupportedFileType(
            f"'{file.name}' is a folder, not a file. List its contents instead."
        )
    if file.is_shortcut:
        raise UnsupportedFileType(
            f"'{file.name}' is a shortcut rather than a file. "
            "Ask for the file it points at."
        )

    if mime in _GOOGLE_EXPORTS:
        data = await _export(service, file.id, _GOOGLE_EXPORTS[mime])
        if len(data) > MAX_TEXT_BYTES:
            raise FileTooLarge(
                f"'{file.name}' exports to {len(data):,} bytes, over the "
                f"{MAX_TEXT_BYTES:,} byte limit."
            )
        result = extract_plaintext(data, _GOOGLE_EXPORTS[mime])
        result.note = (
            f"exported from {mime.rsplit('.', 1)[-1]} as {_GOOGLE_EXPORTS[mime]}"
        )
        return result

    cap = MAX_IMAGE_BYTES if mime in _IMAGE_TYPES else MAX_TEXT_BYTES
    if file.size is not None and file.size > cap:
        raise FileTooLarge(
            f"'{file.name}' is {file.size:,} bytes, over the {cap:,} byte limit."
        )

    if mime == "application/pdf":
        return extract_pdf(await _download(service, file.id))

    if mime in _IMAGE_TYPES:
        return extract_image(await _download(service, file.id), mime)

    if mime.startswith("image/"):
        raise UnsupportedFileType(
            f"'{file.name}' is {mime}, which cannot be displayed. "
            "Supported image types are PNG, JPEG, GIF and WebP."
        )

    if _is_text(mime):
        return extract_plaintext(await _download(service, file.id), mime)

    raise UnsupportedFileType(
        f"'{file.name}' is {mime}. This server can read PDFs, images, plain "
        "text, and native Google Docs, Sheets and Slides."
    )
