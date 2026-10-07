import pytest

from app.classification_skills.lock import parse_classification_json_content


@pytest.mark.parametrize(
    "content",
    [
        '{"label":"INVOICE","confidence":0.92}',
        '{"label":"INVOICE","confidence":92}',
        '{"document_type":"INVOICE","confidence_score":0.92}',
        '{"document_type":"INVOICE","confidence_score":92}',
    ],
)
def test_confidence_reads_both_scales(content):
    payload = parse_classification_json_content(content, ocr_text="Invoice")
    assert payload["document_type"] == "INVOICE"
    assert payload["confidence_score"] == 92.0
