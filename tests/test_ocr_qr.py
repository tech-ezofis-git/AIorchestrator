"""OCR agent QR detection: OpenCV scan of PDF pages/images, payload decoding, OCR JSON merge."""
import base64
import json

import pytest

cv2 = pytest.importorskip("cv2")
fitz = pytest.importorskip("fitz")

from app.agents.ocr_helpers import resolve_pageno  # noqa: E402
from app.integrations.qr_scan import decode_qr_payload, scan_document_qr  # noqa: E402


def _qr_png(text: str) -> bytes:
    # cv2.QRCodeEncoder emits undecodable codes from QR version 7 up; keep payloads under ~100 chars.
    image = cv2.QRCodeEncoder.create().encode(text)
    image = cv2.resize(image, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    image = cv2.copyMakeBorder(image, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", image)
    assert ok
    return png.tobytes()


def _pdf_with_qr(qr_text: str, *, pages: int = 3, qr_page: int = 2) -> bytes:
    doc = fitz.open()
    for page_no in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Invoice No: INV-9 page {page_no}")
        if page_no == qr_page:
            page.insert_image(fitz.Rect(72, 120, 272, 320), stream=_qr_png(qr_text))
    data = doc.tobytes()
    doc.close()
    return data


def _jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.c2lnbmF0dXJl"


def test_decode_upi_payload():
    decoded = decode_qr_payload("upi://pay?pa=acme@okbank&pn=Acme%20Ltd&am=1180.00&cu=INR")
    assert decoded == {
        "type": "upi",
        "pa": "acme@okbank",
        "pn": "Acme Ltd",
        "am": "1180.00",
        "cu": "INR",
    }


def test_decode_gst_einvoice_jwt():
    inner = {"SellerGstin": "29AAACA1234A1Z5", "DocNo": "INV-9", "TotInvVal": 1180}
    decoded = decode_qr_payload(_jwt({"iss": "NIC", "data": json.dumps(inner)}))
    assert decoded == {"type": "gst_einvoice", "issuer": "NIC", **inner}


def test_decode_plain_text_is_none():
    assert decode_qr_payload("https://example.com/invoice/9") is None
    assert decode_qr_payload("") is None


def test_scan_pdf_finds_qr_on_its_page():
    data = _pdf_with_qr("upi://pay?pa=acme@okbank&am=1180.00", pages=3, qr_page=2)
    hits = scan_document_qr(
        data,
        filename="inv.pdf",
        content_type="application/pdf",
        page_selection=resolve_pageno("-1"),
    )
    assert len(hits) == 1
    assert hits[0]["page"] == 2
    assert hits[0]["data"] == "upi://pay?pa=acme@okbank&am=1180.00"
    assert hits[0]["decoded"]["am"] == "1180.00"


def test_scan_respects_page_selection():
    data = _pdf_with_qr("hello-qr", pages=3, qr_page=2)
    hits = scan_document_qr(
        data,
        filename="inv.pdf",
        content_type="application/pdf",
        page_selection=resolve_pageno("1"),
    )
    assert hits == []


def test_scan_image_upload():
    hits = scan_document_qr(
        _qr_png("hello-qr"),
        filename="qr.png",
        content_type="image/png",
        page_selection=resolve_pageno("1"),
    )
    assert hits == [{"page": 1, "data": "hello-qr"}]


def test_scan_unsupported_or_broken_input_returns_empty():
    sel = resolve_pageno("1")
    assert scan_document_qr(b"text", filename="a.txt", content_type="text/plain", page_selection=sel) == []
    assert scan_document_qr(b"not a pdf", filename="a.pdf", content_type="application/pdf", page_selection=sel) == []


def test_ocr_multipart_merges_qr_codes_into_result(client, monkeypatch):
    seen_prompts: list[str] = []

    async def fake_completion(self, messages, **_kwargs):
        seen_prompts.append(json.dumps(messages))
        return {
            "content": json.dumps(
                {"ocrResult": [{"name": "Invoice No", "value": "INV-9", "type": "SHORT_TEXT"}]}
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    qr_text = "upi://pay?pa=acme@okbank&am=1180.00"
    response = client.post(
        "/chat",
        data={
            "session_id": "s-qr",
            "intent": "ocr",
            "pageno": "-1",
            "parameters": json.dumps(["Invoice No,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("inv.pdf", _pdf_with_qr(qr_text), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    reply = json.loads(body["reply"])
    assert reply["qr_codes"][0]["page"] == 2
    assert reply["qr_codes"][0]["data"] == qr_text
    assert body["ocr_result"]["qr_codes"] == reply["qr_codes"]
    assert "Invoice No: INV-9" in reply["ocr_text"]
    assert any("QR (page 2)" in prompt for prompt in seen_prompts)


def test_ocr_keeps_qr_codes_when_text_extraction_fails(client, monkeypatch):
    from app.integrations.ocr_engine import OcrEngineClient, OcrEngineError

    async def failing_extract(self, **_kwargs):
        raise OcrEngineError("OCR extract_text request failed.")

    async def fake_completion(self, messages, **_kwargs):
        assert "QR (page 2)" in json.dumps(messages)
        return {
            "content": json.dumps(
                {"ocrResult": [{"name": "UPI ID", "value": "acme@okbank", "type": "SHORT_TEXT"}]}
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(OcrEngineClient, "_extract_text", failing_extract)
    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-qr-fail",
            "intent": "ocr",
            "pageno": "-1",
            "parameters": json.dumps(["UPI ID,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("scan.pdf", _pdf_with_qr("upi://pay?pa=acme@okbank&am=1180.00"), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["ocr_result"]
    assert result["ocr_status"] == "fallback"
    assert result["ocr_text"] == ""
    assert result["qr_codes"][0]["decoded"]["pa"] == "acme@okbank"
    assert result["ocrResult"][0]["value"] == "acme@okbank"


def test_ocr_text_failure_without_qr_still_falls_back(client, monkeypatch):
    from app.integrations.ocr_engine import OcrEngineClient, OcrEngineError

    async def failing_extract(self, **_kwargs):
        raise OcrEngineError("OCR extract_text request failed.")

    monkeypatch.setattr(OcrEngineClient, "_extract_text", failing_extract)

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "no qr here")
    response = client.post(
        "/chat",
        data={
            "session_id": "s-noqr-fail",
            "intent": "ocr",
            "pageno": "1",
            "parameters": json.dumps(["Invoice No,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("scan.pdf", doc.tobytes(), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["ocr_result"]
    assert result["ocr_status"] == "fallback"
    assert result["qr_codes"] == []
    assert result["ocrResult"] == [{"name": "Invoice No", "value": None, "type": "SHORT_TEXT"}]
