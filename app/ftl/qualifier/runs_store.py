"""
Run history — one JSON file (data/runs.json) holding every qualification run, newest last.
Supports thread-safe atomic writes, EML content fingerprint indexing, and metadata persistence
for duplicate detection.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("orchestrator.ftl.qualifier.runs_store")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RUNS_PATH = os.path.join(DATA_DIR, "runs.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

# Ensure directory structure exists in any deployment environment
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_runs() -> List[Dict[str, Any]]:
    with _lock:
        if not os.path.exists(RUNS_PATH):
            return []
        try:
            with open(RUNS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error reading runs from {RUNS_PATH}: {e}")
            return []


def _save_runs(runs: List[Dict[str, Any]]) -> None:
    """Atomically write runs list to data/runs.json using tempfile and os.replace."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        temp_fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, prefix="runs_", suffix=".tmp")
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(runs, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, RUNS_PATH)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.error(f"Failed atomic save to {RUNS_PATH}: {e}")
            raise


def save_upload(filename: str, content: bytes) -> str:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOADS_DIR, stored_name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def find_run_by_fingerprint(fingerprint: str) -> Optional[Dict[str, Any]]:
    """Look up an existing run with an exact content fingerprint."""
    if not fingerprint:
        return None
    with _lock:
        for r in reversed(load_runs()):
            if r.get("content_fingerprint") == fingerprint:
                return r
    return None


def list_eml_records() -> List[Dict[str, Any]]:
    """Return all runs that originated from an EML file or have email metadata."""
    with _lock:
        runs = load_runs()
        return [
            r for r in reversed(runs)
            if r.get("input_type") == "eml"
            or (r.get("input_filename") or "").lower().endswith(".eml")
            or r.get("content_fingerprint")
        ]


def create_run(
    *,
    input_filename: str,
    input_type: str,
    input_file_path: str,
    content_fingerprint: Optional[str] = None,
    normalized_content: Optional[str] = None,
    email_metadata: Optional[Dict[str, Any]] = None,
    duplicate_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    with _lock:
        runs = load_runs()
        run = {
            "id": (runs[-1]["id"] + 1) if runs else 1,
            "input_filename": input_filename,
            "input_type": input_type,
            "input_file_path": input_file_path,
            "content_fingerprint": content_fingerprint,
            "normalized_content": normalized_content,
            "email_metadata": email_metadata or {},
            "duplicate_info": duplicate_info,
            "status": "running",
            "decision": None,
            "project_type": None,
            "confidence": None,
            "project_name": "",
            "deadline_text": "",
            "result": None,
            "error_detail": None,
            "total_tokens": 0,
            "started_at": _now_iso(),
            "finished_at": None,
        }
        runs.append(run)
        _save_runs(runs)
        return run


def update_run(run_id: int, **fields: Any) -> Dict[str, Any]:
    with _lock:
        runs = load_runs()
        for r in runs:
            if r["id"] == run_id:
                r.update(fields)
                _save_runs(runs)
                return r
        raise KeyError(f"Run {run_id} not found")


def list_runs() -> List[Dict[str, Any]]:
    with _lock:
        return list(reversed(load_runs()))


def append_run(
    *,
    input_filename: str,
    input_type: str,
    candidate_text: str,
    decision: Dict[str, Any],
    total_tokens: int = 0,
    raw_file_bytes: Optional[bytes] = None,
    content_fingerprint: Optional[str] = None,
    normalized_content: Optional[str] = None,
    email_metadata: Optional[Dict[str, Any]] = None,
    duplicate_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    with _lock:
        input_file_path = ""
        if raw_file_bytes:
            input_file_path = save_upload(input_filename, raw_file_bytes)

        run = create_run(
            input_filename=input_filename,
            input_type=input_type,
            input_file_path=input_file_path,
            content_fingerprint=content_fingerprint,
            normalized_content=normalized_content,
            email_metadata=email_metadata,
            duplicate_info=duplicate_info,
        )
        return update_run(
            run["id"],
            status="done",
            decision=decision.get("qualify"),
            project_type=decision.get("project_type"),
            confidence=decision.get("confidence"),
            project_name=decision.get("project_name") or "",
            deadline_text=decision.get("deadline") or "",
            result=decision,
            total_tokens=total_tokens,
            finished_at=_now_iso(),
        )


def get_run(run_id: Any) -> Optional[Dict[str, Any]]:
    with _lock:
        target = str(run_id).strip()
        for r in load_runs():
            if str(r.get("id")) == target:
                return r
        return None
