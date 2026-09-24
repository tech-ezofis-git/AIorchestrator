"""Hybrid RFQ page text: keep selectable text, OCR only image pages."""
import pymupdf
import pytest

from app.ftl.page_text import extract_pdf_text_hybrid, prefer_spec_attachment, text_is_usable


def _mixed_pdf() -> bytes:
    doc = pymupdf.open()
    text_page = doc.new_page()
    text_page.insert_text(
        (72, 72),
        "Governor and governor ropes hall door equipment car roller guides " * 8,
    )
    image_page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 40), 1)
    pix.clear_with(180)
    image_page.insert_image(image_page.rect, pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_hybrid_keeps_text_page_and_ocrs_image_page(monkeypatch):
    calls = []

    async def fake_ocr(data, filename, content_type):
        calls.append(filename)
        return "center opening door operator 42 inch"

    monkeypatch.setattr("app.ftl.page_text._ocr_bytes", fake_ocr)

    text = await extract_pdf_text_hybrid(_mixed_pdf())

    assert "Governor and governor ropes" in text
    assert "center opening door operator" in text
    assert calls == ["page-2.pdf"]


def test_text_is_usable_ignores_a_page_number():
    assert text_is_usable("12") is False
    assert text_is_usable("Governor ropes and car door clutch specification") is True


def test_prefer_spec_attachment_picks_pdf_over_logo():
    chosen = prefer_spec_attachment(
        [
            {"filename": "logo.png", "bytes": b"img"},
            {"filename": "spec.pdf", "bytes": b"pdf"},
        ]
    )
    assert chosen["filename"] == "spec.pdf"
