from app.integrations.docx_text import (
    DocxExtractError,
    extract_docx_text,
    looks_like_docx,
    looks_like_legacy_doc,
)
from tests.test_summary_endpoint import _minimal_docx_bytes


def test_looks_like_docx_by_name_and_type():
    assert looks_like_docx(filename="policy.docx")
    assert looks_like_docx(
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert not looks_like_docx(filename="letter.doc")
    assert not looks_like_docx(filename="scan.pdf")


def test_looks_like_legacy_doc_does_not_match_docx():
    assert looks_like_legacy_doc(filename="letter.doc")
    assert not looks_like_legacy_doc(filename="letter.docx")


def test_extract_docx_text_joins_paragraphs():
    data = _minimal_docx_bytes(["Insurer: ABC", "Policy: POL-1"])
    assert extract_docx_text(data) == "Insurer: ABC\nPolicy: POL-1"


def test_extract_docx_text_rejects_garbage():
    try:
        extract_docx_text(b"not a zip")
    except DocxExtractError:
        return
    raise AssertionError("expected DocxExtractError")
