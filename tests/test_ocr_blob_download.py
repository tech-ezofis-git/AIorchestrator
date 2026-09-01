"""Unit tests for OcrEngineClient's blob download paths — code-review
findings #7 (enforce the size limit before buffering the whole blob) and
#8 (a SAS token query string on a blob URL must survive to the HTTP
fallback request, not get silently dropped)."""
import pytest

from app.agents.ocr_helpers import BlobRef, parse_blob_filepath
from app.config import Settings
from app.integrations.ocr_engine import OcrEngineClient, OcrEngineError


def test_parse_blob_filepath_preserves_sas_query_string():
    ref = parse_blob_filepath(
        "https://acct.blob.core.windows.net/container/folder/file.pdf?sv=2021-08-06&sig=abc123%3D",
        allowed_host_suffixes=[".blob.core.windows.net"],
    )
    assert ref.blob_name == "folder/file.pdf"
    assert ref.query == "sv=2021-08-06&sig=abc123%3D"


def test_parse_blob_filepath_no_query_string_is_none():
    ref = parse_blob_filepath(
        "https://acct.blob.core.windows.net/container/file.pdf",
        allowed_host_suffixes=[".blob.core.windows.net"],
    )
    assert ref.query is None


class _FakeResponse:
    def __init__(self, url: str, content: bytes = b"pdf-bytes"):
        self.url = url
        self.content = content

    def raise_for_status(self):
        return None


class _FakeHttpxClient:
    """Records the exact URL requested so the test can assert the SAS
    query string reached the outgoing HTTP request."""

    captured_urls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str):
        _FakeHttpxClient.captured_urls.append(url)
        return _FakeResponse(url)


async def test_sas_token_reaches_the_http_fallback_request(monkeypatch):
    monkeypatch.setattr("app.integrations.ocr_engine.httpx.AsyncClient", _FakeHttpxClient)
    _FakeHttpxClient.captured_urls.clear()

    client = OcrEngineClient(Settings(azure_storage_connection_string=None))
    blob_ref = BlobRef(
        container="ezts123",
        blob_name="folder/file.pdf",
        account_url_host="acct.blob.core.windows.net",
        query="sv=2021-08-06&sig=abc123%3D",
    )

    data = await client._download_blob(blob_ref, max_bytes=1024, timeout=5.0)

    assert data == b"pdf-bytes"
    assert _FakeHttpxClient.captured_urls == [
        "https://acct.blob.core.windows.net/ezts123/folder/file.pdf?sv=2021-08-06&sig=abc123%3D"
    ]


async def test_no_query_string_omits_trailing_question_mark(monkeypatch):
    monkeypatch.setattr("app.integrations.ocr_engine.httpx.AsyncClient", _FakeHttpxClient)
    _FakeHttpxClient.captured_urls.clear()

    client = OcrEngineClient(Settings(azure_storage_connection_string=None))
    blob_ref = BlobRef(container="ezts123", blob_name="file.pdf", account_url_host="acct.blob.core.windows.net")

    await client._download_blob(blob_ref, max_bytes=1024, timeout=5.0)

    assert _FakeHttpxClient.captured_urls == ["https://acct.blob.core.windows.net/ezts123/file.pdf"]


class _FakeStream:
    def __init__(self, size: int, data: bytes):
        self.size = size
        self._data = data
        self.readall_called = False

    async def readall(self):
        self.readall_called = True
        return self._data


class _FakeBlobClient:
    def __init__(self, stream: _FakeStream):
        self._stream = stream

    async def download_blob(self):
        return self._stream


class _FakeBlobServiceClient:
    def __init__(self, stream: _FakeStream):
        self._stream = stream

    def get_blob_client(self, container, blob_name):
        return _FakeBlobClient(self._stream)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_oversized_blob_rejected_before_readall(monkeypatch):
    """Code-review finding #7: the size check must happen against
    `stream.size` (known from the initial response) BEFORE `readall()` —
    proven here by asserting readall() was never even called."""
    oversized_stream = _FakeStream(size=10_000_000, data=b"x" * 10_000_000)
    fake_service = _FakeBlobServiceClient(oversized_stream)

    monkeypatch.setattr(
        "azure.storage.blob.aio.BlobServiceClient.from_connection_string",
        lambda conn: fake_service,
    )

    client = OcrEngineClient(Settings())
    blob_ref = BlobRef(container="ezts123", blob_name="huge.pdf")

    with pytest.raises(OcrEngineError, match="size limit"):
        await client._download_via_azure_sdk("fake-conn-string", blob_ref, max_bytes=1024)

    assert oversized_stream.readall_called is False


async def test_within_limit_blob_is_downloaded(monkeypatch):
    small_stream = _FakeStream(size=9, data=b"small-pdf")
    fake_service = _FakeBlobServiceClient(small_stream)

    monkeypatch.setattr(
        "azure.storage.blob.aio.BlobServiceClient.from_connection_string",
        lambda conn: fake_service,
    )

    client = OcrEngineClient(Settings())
    blob_ref = BlobRef(container="ezts123", blob_name="small.pdf")

    data = await client._download_via_azure_sdk("fake-conn-string", blob_ref, max_bytes=1024)

    assert data == b"small-pdf"
    assert small_stream.readall_called is True
