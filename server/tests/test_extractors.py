"""Extraction behaviour. Pure functions over bytes — no network, no Drive."""

import pytest

from app.drive.extractors import (
    ExtractedImage,
    extract_image,
    extract_pdf,
    extract_plaintext,
    truncate,
)
from app.errors import NoExtractableText, UnsupportedFileType


class TestPdf:
    def test_scanned_pdf_is_reported_not_returned_empty(self, scanned_pdf):
        """The branch that matters.

        pypdf returns "" for a page with no text layer without raising. Passing
        that through would tell the user the file is empty, when the truth is
        that it needs OCR — a different answer, and the wrong one.
        """
        with pytest.raises(NoExtractableText) as exc:
            extract_pdf(scanned_pdf)

        message = str(exc.value).lower()
        assert "no text layer" in message
        assert "ocr" in message, "the message should say what would be needed"

    def test_corrupt_pdf_raises_domain_error(self):
        """Not a bare pypdf exception: the tool layer maps domain errors only."""
        with pytest.raises(NoExtractableText):
            extract_pdf(b"this is definitely not a pdf")

    def test_page_markers_let_the_model_cite_a_location(self):
        pdf = _pdf_with_text(["first page body", "second page body"])
        if pdf is None:
            pytest.skip("reportlab not installed; covered by end-to-end runs")

        result = extract_pdf(pdf)
        assert "--- Page 1 ---" in result.text
        assert "--- Page 2 ---" in result.text


class TestImage:
    def test_bytes_pass_through_untouched(self):
        """No OCR, no re-encoding: Claude reads the image itself."""
        raw = b"\x89PNG\r\n\x1a\n" + b"payload"
        result = extract_image(raw, "image/png")

        assert isinstance(result, ExtractedImage)
        assert result.data == raw, "the bytes must not be transformed"
        assert result.mime_type == "image/png"

    @pytest.mark.parametrize("mime", ["image/tiff", "image/svg+xml", "image/bmp"])
    def test_types_claude_cannot_display_are_refused(self, mime):
        """Better a clear refusal than a block that fails downstream."""
        with pytest.raises(UnsupportedFileType) as exc:
            extract_image(b"\x00", mime)
        assert "png" in str(exc.value).lower(), "the message should list alternatives"


class TestPlaintext:
    def test_one_bad_byte_does_not_fail_the_whole_read(self):
        data = b"before" + b"\xff\xfe" + b"after"
        result = extract_plaintext(data, "text/plain")

        assert "before" in result.text and "after" in result.text

    def test_utf8_survives(self):
        result = extract_plaintext("繁體中文內容".encode(), "text/plain")
        assert result.text == "繁體中文內容"


class TestTruncate:
    def test_short_text_is_untouched(self):
        text, was_truncated = truncate("short", limit=100)
        assert (text, was_truncated) == ("short", False)

    def test_cuts_on_a_line_boundary(self):
        text, was_truncated = truncate(
            "\n".join(f"line {i}" for i in range(200)), limit=100
        )

        assert was_truncated
        assert len(text) <= 100
        assert not text.endswith("lin"), "should not cut mid-word"

    def test_cjk_is_not_split_into_mojibake(self):
        """len() counts characters, so a CJK character is never halved. Cutting
        on a byte offset would split its three UTF-8 bytes."""
        text, was_truncated = truncate(
            "\n".join(f"第 {i} 行的中文內容" for i in range(200)), limit=200
        )

        assert was_truncated
        assert "�" not in text, "no replacement characters"
        assert text.encode("utf-8").decode("utf-8") == text


def _pdf_with_text(pages: list[str]) -> bytes | None:
    """Build a PDF that actually carries a text layer, or None if unavailable."""
    try:
        import io

        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for body in pages:
        pdf.drawString(72, 720, body)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
