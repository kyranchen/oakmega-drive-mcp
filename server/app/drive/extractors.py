"""Per-format extraction. One function per strategy, no network, no MCP.

Extractors return a union rather than a string, because the image path carries
bytes that become an MCP image block. Base64 encoding happens at the MCP
boundary so these stay testable against real bytes.
"""

import io
from dataclasses import dataclass

from pypdf import PdfReader

from ..errors import NoExtractableText, UnsupportedFileType

# Claude accepts these four image types and nothing else.
CLAUDE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Characters, not bytes: one CJK character is three UTF-8 bytes, and cutting on
# a byte offset would split it and produce mojibake.
DEFAULT_TEXT_LIMIT = 100_000


@dataclass
class ExtractedText:
    """Text pulled out of a file, ready to become an MCP text block."""

    text: str
    truncated: bool = False
    note: str | None = None  # e.g. "exported from a Google Doc as markdown"


@dataclass
class ExtractedImage:
    """Raw image bytes. Base64 encoding happens at the MCP boundary."""

    data: bytes
    mime_type: str


ExtractionResult = ExtractedText | ExtractedImage


# Truncate text to a limit
def truncate(text: str, limit: int = DEFAULT_TEXT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False

    cut = text[:limit]
    boundary = cut.rfind("\n")
    # Only honour the line break if it is not so far back that we throw away
    # most of what we were allowed to keep.
    if boundary > limit // 2:
        cut = cut[:boundary]
    return cut.rstrip(), True


def extract_pdf(data: bytes) -> ExtractedText:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise NoExtractableText(f"This PDF could not be opened: {exc}") from exc

    if reader.is_encrypted:
        raise NoExtractableText(
            "This PDF is password protected, so its text cannot be read."
        )

    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - pypdf raises a variety of types per page
            # A single malformed page (a broken font, usually) should not cost
            # the reader the rest of the document.
            pages.append("")

    if not any(page.strip() for page in pages):
        raise NoExtractableText(
            f"This PDF has {len(pages)} page(s) but no text layer — it appears "
            "to be scanned images. Reading it would need OCR, which this "
            "server does not perform."
        )

    # Page markers let the model cite a location rather than quoting blindly.
    body = "\n\n".join(
        f"--- Page {number} ---\n{text.strip()}"
        for number, text in enumerate(pages, start=1)
        if text.strip()
    )

    text, was_truncated = truncate(body)
    return ExtractedText(
        text=text,
        truncated=was_truncated,
        note=f"{len(pages)} page(s)",
    )


def extract_image(data: bytes, mime_type: str) -> ExtractedImage:
    if mime_type not in CLAUDE_IMAGE_TYPES:
        raise UnsupportedFileType(
            f"{mime_type} cannot be shown as an image. "
            f"Supported types are {', '.join(sorted(CLAUDE_IMAGE_TYPES))}."
        )
    return ExtractedImage(data=data, mime_type=mime_type)


def extract_plaintext(data: bytes, mime_type: str) -> ExtractedText:
    decoded = data.decode("utf-8", errors="replace")
    text, was_truncated = truncate(decoded)
    return ExtractedText(text=text, truncated=was_truncated)
