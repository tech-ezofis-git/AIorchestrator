"""File Fetcher tool — tenant + Ezofis path → login → GET /uploadAndIndex/files/by-path → decoded file bytes."""
from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("orchestrator.tools.file_fetcher")

TOOL_ID = "file_fetcher"
NO_LOGIN_ERROR = "File Fetcher login is not configured (FILE_FETCHER_LOGIN_EMAIL / FILE_FETCHER_LOGIN_PASSWORD)."
NO_TENANT_ERROR = "A tenant id is required."
NO_PATH_ERROR = "Provide a path, or a timestamp and file name."


def build_path(
    *,
    path: Optional[str] = None,
    timestamp: Optional[str] = None,
    file_name: Optional[str] = None,
    folder: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """`path` as given, else `{folder}/{timestamp}/{file_name}` (folder defaults to monitor/Ramco_mjb)."""
    direct = (path or "").strip().strip("/")
    if direct:
        return direct
    ts = (timestamp or "").strip().strip("/")
    name = (file_name or "").strip().strip("/")
    if not ts or not name:
        return None
    base = (folder or "").strip().strip("/") or (settings or get_settings()).file_fetcher_default_folder.strip("/")
    return "/".join(p for p in (base, ts, name) if p)


def _api_base(settings: Settings) -> str:
    return (settings.file_fetcher_api_base or settings.ezofis_api_base or "https://cloud.ezofis.com/api").rstrip("/")


async def _login(client: httpx.AsyncClient, base: str, tenant_id: str, settings: Settings) -> str:
    """POST /auth/ezofis/login as `tenant_id`; returns the `Authorization` header value."""
    email = (settings.file_fetcher_login_email or "").strip()
    password = settings.file_fetcher_login_password or ""
    if not email or not password:
        raise RuntimeError(NO_LOGIN_ERROR)
    response = await client.post(
        f"{base}/auth/ezofis/login",
        headers={"accept": "application/json", "Content-Type": "application/json", "X-Tenant-Id": tenant_id},
        json={"email": email, "password": password},
    )
    if response.status_code != 200:
        detail = (response.text or "").strip()[:300]
        raise RuntimeError(f"Login failed (HTTP {response.status_code}): {detail or 'no details'}")
    data = response.json() if response.content else {}
    token = str(data.get("accessToken") or data.get("access_token") or data.get("token") or "").strip()
    if not token:
        raise RuntimeError("Login did not return an access token.")
    token_type = str(data.get("tokenType") or data.get("token_type") or "Bearer").strip() or "Bearer"
    return f"{token_type} {token}"


async def _download(client: httpx.AsyncClient, base: str, tenant_id: str, authorization: str, path: str) -> dict:
    response = await client.get(
        f"{base}/uploadAndIndex/files/by-path",
        params={"path": path, "base64": "true"},
        headers={"accept": "application/json", "Authorization": authorization, "X-Tenant-Id": tenant_id},
    )
    if response.status_code != 200:
        detail = (response.text or "").strip()[:300]
        raise RuntimeError(f"File download failed (HTTP {response.status_code}): {detail or 'no details'}")
    data = response.json() if response.content else None
    if not isinstance(data, dict):
        raise RuntimeError("File download returned an unexpected response.")
    return data


def _result(
    *,
    status: str,
    started: float,
    tenant_id: Optional[str] = None,
    path: Optional[str] = None,
    file_name: Optional[str] = None,
    content_type: Optional[str] = None,
    file_size: Optional[int] = None,
    file_bytes: Optional[bytes] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "tool": TOOL_ID,
        "status": status,
        "tenant_id": tenant_id,
        "path": path,
        "fileName": file_name,
        "contentType": content_type,
        "fileSize": file_size,
        "file_bytes": file_bytes,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
    }


async def fetch_file_by_path(
    *,
    tenant_id: str,
    path: Optional[str] = None,
    timestamp: Optional[str] = None,
    file_name: Optional[str] = None,
    folder: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Log in as `tenant_id` (fresh token every call), download the file at `path` as base64, and decode it.

    `file_bytes` holds the decoded file, ready for `run_ocr_tool(file_bytes=...)`.
    Never raises: missing input, login/HTTP errors and bad base64 come back as
    status FAILED with `error` set.
    """
    started = time.perf_counter()
    settings = settings or get_settings()
    tenant = (tenant_id or "").strip()
    if not tenant:
        return _result(status="FAILED", started=started, error=NO_TENANT_ERROR)
    full_path = build_path(path=path, timestamp=timestamp, file_name=file_name, folder=folder, settings=settings)
    if not full_path:
        return _result(status="FAILED", started=started, tenant_id=tenant, error=NO_PATH_ERROR)

    base = _api_base(settings)
    try:
        async with httpx.AsyncClient(timeout=settings.ezofis_timeout_seconds) as client:
            authorization = await _login(client, base, tenant, settings)
            data = await _download(client, base, tenant, authorization, full_path)
    except Exception as exc:
        logger.warning("file_fetcher_failed", extra={"error_type": type(exc).__name__})
        reason = str(exc).strip() or type(exc).__name__
        return _result(status="FAILED", started=started, tenant_id=tenant, path=full_path, error=reason)

    name = data.get("fileName") or full_path.rsplit("/", 1)[-1]
    content_type = data.get("contentType")
    try:
        file_bytes = base64.b64decode(data.get("base64") or "", validate=False)
    except (binascii.Error, ValueError) as exc:
        return _result(
            status="FAILED", started=started, tenant_id=tenant, path=full_path,
            file_name=name, content_type=content_type, error=f"Could not decode base64 file: {exc}",
        )
    if not file_bytes:
        return _result(
            status="FAILED", started=started, tenant_id=tenant, path=full_path,
            file_name=name, content_type=content_type, error="The file is empty.",
        )
    return _result(
        status="SUCCEEDED", started=started, tenant_id=tenant, path=full_path,
        file_name=name, content_type=content_type,
        file_size=data.get("fileSize") if data.get("fileSize") is not None else len(file_bytes),
        file_bytes=file_bytes,
    )
