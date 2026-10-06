"""OCR tool: plain function (file bytes or blob path → Paddle text, no LLM), plus its /chat intent=ocr_tool wiring."""
from app.tools.ocr_tool import EMPTY_FILE_ERROR, EMPTY_TEXT_ERROR, NO_INPUT_ERROR, run_ocr_tool

_LAYOUT_TEXT = "  INVOICE            INV-2026-6001\n  Total              5203.65\n"


class _FakeEngine:
    def __init__(self, text: str = _LAYOUT_TEXT, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    async def run_ocr(self, reference="", **kwargs):
        self.calls.append({"reference": reference, **kwargs})
        if self.error:
            raise self.error
        return {"text": self.text, "filename": kwargs.get("filename")}


async def test_upload_keeps_layout():
    engine = _FakeEngine()

    result = await run_ocr_tool(engine, file_bytes=b"%PDF-1.4 fake", filename="invoice.pdf", pageno="1")

    assert result["tool"] == "ocr"
    assert result["status"] == "SUCCEEDED"
    assert result["text"] == _LAYOUT_TEXT
    assert result["chars"] == len(_LAYOUT_TEXT)
    assert result["filename"] == "invoice.pdf"
    assert result["pages"] == "page 1"
    assert result["error"] is None
    call = engine.calls[0]
    assert call["layout"] is True
    assert call["file_bytes"] == b"%PDF-1.4 fake"
    assert call["filepath"] is None


async def test_blob_path_and_layout_off():
    engine = _FakeEngine()

    result = await run_ocr_tool(engine, filepath="folder/invoice.pdf", layout=False, tenant_id="11")

    assert result["status"] == "SUCCEEDED"
    assert result["text"] == _LAYOUT_TEXT.strip()
    call = engine.calls[0]
    assert call["filepath"] == "folder/invoice.pdf"
    assert call["file_bytes"] is None
    assert call["tenant_id"] == "11"
    assert call["layout"] is False


async def test_requires_file_or_path():
    engine = _FakeEngine()

    result = await run_ocr_tool(engine, pageno="1")

    assert result["status"] == "FAILED"
    assert result["error"] == NO_INPUT_ERROR
    assert engine.calls == []


async def test_rejects_empty_upload():
    result = await run_ocr_tool(_FakeEngine(), file_bytes=b"", filename="a.png")

    assert result["status"] == "FAILED"
    assert result["error"] == EMPTY_FILE_ERROR


async def test_rejects_bad_page():
    result = await run_ocr_tool(_FakeEngine(), file_bytes=b"\x89PNG", filename="a.png", pageno="99")

    assert result["status"] == "FAILED"
    assert "Invalid pageno" in result["error"]


async def test_reports_ocr_failure():
    engine = _FakeEngine(error=RuntimeError("OCR extract_text request failed."))

    result = await run_ocr_tool(engine, file_bytes=b"\x89PNG", filename="a.png")

    assert result["status"] == "FAILED"
    assert result["error"] == "OCR failed: OCR extract_text request failed."
    assert result["text"] == ""


async def test_reports_empty_text():
    result = await run_ocr_tool(_FakeEngine(text="   \n"), file_bytes=b"\x89PNG", filename="a.png")

    assert result["status"] == "FAILED"
    assert result["error"] == EMPTY_TEXT_ERROR


def _patch_engine(monkeypatch, captured: dict, text: str = _LAYOUT_TEXT):
    async def run_ocr(self, reference="", **kwargs):
        captured.update(kwargs)
        return {"text": text, "filename": kwargs.get("filename")}

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", run_ocr)


def test_chat_intent_ocr_tool_multipart_upload(client, monkeypatch):
    captured: dict = {}
    _patch_engine(monkeypatch, captured)

    response = client.post(
        "/chat",
        data={"session_id": "s-ocr-tool", "intent": "ocr_tool", "pageno": "1"},
        files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["ocr_tool_result"]
    assert result["status"] == "SUCCEEDED"
    assert result["text"] == _LAYOUT_TEXT
    assert body["reply"] == f"OCR extracted {len(_LAYOUT_TEXT)} characters from invoice.pdf."
    assert captured["layout"] is True
    assert captured["file_bytes"] == b"%PDF-1.4 fake"


def test_chat_intent_ocr_tool_blob_path_layout_off(client, monkeypatch):
    captured: dict = {}
    _patch_engine(monkeypatch, captured)

    fields = {
        "session_id": "s-ocr-tool-blob",
        "intent": "ocr_tool",
        "filepath": "folder/invoice.pdf",
        "layout": "false",
        "tenant_id": "11",
    }
    response = client.post("/chat", files={k: (None, v) for k, v in fields.items()})

    assert response.status_code == 200, response.text
    result = response.json()["ocr_tool_result"]
    assert result["status"] == "SUCCEEDED"
    assert result["text"] == _LAYOUT_TEXT.strip()
    assert captured["filepath"] == "folder/invoice.pdf"
    assert captured["tenant_id"] == "11"
    assert captured["layout"] is False


def test_old_tools_ocr_endpoint_is_gone(client):
    response = client.post("/tools/ocr", files={"file": ("a.png", b"\x89PNG", "image/png")})

    assert response.status_code in {404, 405}
