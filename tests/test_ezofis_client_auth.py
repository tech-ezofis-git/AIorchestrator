"""EzofisClient auth + live-master fallback behavior."""
from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.integrations.ezofis_client import EzofisClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if payload is not None else b""
        self.text = "{}" if payload is not None else ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_authenticate_accepts_v6_camelcase_access_token(monkeypatch):
    settings = Settings(
        ezofis_api_base="http://app/api",
        ezofis_login_email="pilot@ezofis.com",
        ezofis_login_password="secret",
        ezofis_env="live",
    )
    client = EzofisClient(settings=settings)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            assert url.endswith("/auth/tenants")
            return _FakeResponse(
                200,
                {"tenants": [{"tenantId": "27c3bd05-d668-47f9-926e-5223aeef28f3", "name": "EZOFIS"}]},
            )

        async def post(self, url, **kwargs):
            assert url.endswith("/auth/ezofis/login")
            return _FakeResponse(
                200,
                {
                    "userId": "183bb531-b8aa-4b6e-b5f4-510345069455",
                    "accessToken": "jwt-from-v6",
                    "tokenType": "Bearer",
                    "expiresIn": 86400,
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    auth = await client.authenticate(tenant_id="27c3bd05-d668-47f9-926e-5223aeef28f3")
    assert auth["access_token"] == "jwt-from-v6"
    assert auth["token_type"] == "Bearer"
    headers = await client._auth_headers("27c3bd05-d668-47f9-926e-5223aeef28f3")
    assert headers["Authorization"] == "Bearer jwt-from-v6"


@pytest.mark.asyncio
async def test_lookup_po_live_does_not_return_acme_mock(monkeypatch):
    settings = Settings(
        ezofis_api_base="http://app/api",
        ezofis_login_email="pilot@ezofis.com",
        ezofis_login_password="secret",
        ezofis_env="live",
    )
    client = EzofisClient(settings=settings)
    client.use_access_token("preissued", tenant_id="t1")

    async def _no_master(*args, **kwargs):
        return None

    monkeypatch.setattr(client, "_get_master", _no_master)
    po = await client.lookup_po(tenant_id="t1", po_number="PO-60001", form_id="form-1")
    assert po is None


@pytest.mark.asyncio
async def test_lookup_po_without_login_still_mocks():
    # _live_enabled is login credentials, not EZOFIS_ENV — clear both so
    # local .env cannot flip this into a live HTTP lookup.
    settings = Settings(
        ezofis_env="trial",
        ezofis_login_email=None,
        ezofis_login_password=None,
    )
    client = EzofisClient(settings=settings)
    po = await client.lookup_po(tenant_id="t1", po_number="PO-1")
    assert po is not None
    assert po.get("mock") is True
    assert po.get("vendor") == "ACME Supplies"
