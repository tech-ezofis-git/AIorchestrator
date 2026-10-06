"""Folder Mover tool — uploads prepared files over SFTP to the processed / unprocessed folder."""
from __future__ import annotations

import asyncio
import io
import logging
import posixpath
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import Settings, get_settings

logger = logging.getLogger("orchestrator.tools.folder_mover")

TOOL_ID = "folder_mover"
NO_FILES_ERROR = "At least one file with a name and content is required."
NOT_CONFIGURED_ERROR = (
    "SFTP server is missing: send sftp_host / sftp_username / sftp_password, "
    "or set FTP_HOST / FTP_USERNAME / FTP_PASSWORD."
)


def resolve_sftp(settings: Settings, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """SFTP connection + folders: request values win, FTP_* settings fill the rest."""
    o = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    return {
        "host": str(o.get("host") or settings.ftp_host or "").strip(),
        "port": int(o.get("port") or settings.ftp_sftp_port),
        "username": o.get("username") or settings.ftp_username,
        "password": o.get("password") or settings.ftp_password,
        "processed_dir": o.get("processed_dir") or settings.ftp_processed_dir,
        "unprocessed_dir": o.get("unprocessed_dir") or settings.ftp_unprocessed_dir,
        "timeout": settings.ftp_timeout_seconds,
    }


def _upload(sftp_cfg: dict[str, Any], remote_dir: str, files: list[tuple[str, bytes]]) -> list[str]:
    """Blocking; run in a worker thread. Creates `remote_dir` if missing; returns the remote paths."""
    import paramiko

    if not sftp_cfg["host"] or not sftp_cfg["username"] or not sftp_cfg["password"]:
        raise ConnectionError(NOT_CONFIGURED_ERROR)
    timeout = sftp_cfg["timeout"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        try:
            client.connect(
                hostname=sftp_cfg["host"],
                port=sftp_cfg["port"],
                username=sftp_cfg["username"],
                password=sftp_cfg["password"],
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except Exception as exc:
            raise ConnectionError(f"Unable to connect to the SFTP server: {exc}") from exc

        sftp = client.open_sftp()
        try:
            path = ""
            for part in [p for p in remote_dir.split("/") if p]:
                path = f"{path}/{part}"
                try:
                    sftp.stat(path)
                except IOError:
                    sftp.mkdir(path)
            remote_paths = []
            for name, data in files:
                remote_path = posixpath.join(remote_dir, name)
                sftp.putfo(io.BytesIO(data), remote_path)
                remote_paths.append(remote_path)
            return remote_paths
        finally:
            sftp.close()
    finally:
        client.close()


async def move_to_folder(
    *,
    files: list[dict[str, Any]],
    valid: bool,
    sftp: Optional[dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Valid → processed folder, not valid → unprocessed folder. Never raises.

    `files` is [{name, data: bytes}] (the File Preparation output). `sftp` may carry
    host / port / username / password / processed_dir / unprocessed_dir from the
    request; anything missing comes from FTP_* settings.
    """
    started = time.perf_counter()
    sftp_cfg = resolve_sftp(settings or get_settings(), sftp)
    folder = "processed" if valid else "unprocessed"
    remote_dir = sftp_cfg["processed_dir"] if valid else sftp_cfg["unprocessed_dir"]
    prepared = [
        (posixpath.basename(str(f.get("name") or "").strip().replace("\\", "/")), f.get("data"))
        for f in files or []
        if isinstance(f, dict)
    ]
    prepared = [(name, data) for name, data in prepared if name and data]

    def result(status: str, *, remote_paths: Optional[list[str]] = None, error: Optional[str] = None) -> dict:
        return {
            "tool": TOOL_ID,
            "status": status,
            "valid": valid,
            "folder": folder,
            "remote_dir": remote_dir,
            "files": [
                {"name": name, "size": len(data), "remote_path": (remote_paths or [None] * len(prepared))[i]}
                for i, (name, data) in enumerate(prepared)
            ],
            "delivered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if status == "SUCCEEDED" else None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
        }

    if not prepared:
        return result("FAILED", error=NO_FILES_ERROR)
    try:
        remote_paths = await asyncio.to_thread(_upload, sftp_cfg, remote_dir, prepared)
    except ConnectionError as exc:
        return result("FAILED", error=str(exc))
    except Exception as exc:
        logger.warning("folder_mover_failed", extra={"error_type": type(exc).__name__})
        return result("FAILED", error=f"SFTP upload failed: {exc}")

    logger.info("folder_mover_done", extra={"folder": folder, "files": len(prepared)})
    return result("SUCCEEDED", remote_paths=remote_paths)
