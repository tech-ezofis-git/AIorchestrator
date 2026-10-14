"""
Run history — one JSON file (data/runs.json) holding every qualification run, newest last. No
database: this is a standalone single-user tool, so a flat file is enough for the "past runs"
dashboard tab.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RUNS_PATH = os.path.join(DATA_DIR, "runs.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_runs() -> List[Dict[str, Any]]:
    if not os.path.exists(RUNS_PATH):
        return []
    with open(RUNS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_runs(runs: List[Dict[str, Any]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RUNS_PATH, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)


def save_upload(filename: str, content: bytes) -> str:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOADS_DIR, stored_name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def create_run(*, input_filename: str, input_type: str, input_file_path: str) -> Dict[str, Any]:
    runs = load_runs()
    run = {
        "id": (runs[-1]["id"] + 1) if runs else 1,
        "input_filename": input_filename,
        "input_type": input_type,
        "input_file_path": input_file_path,
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
    runs = load_runs()
    for r in runs:
        if r["id"] == run_id:
            r.update(fields)
            _save_runs(runs)
            return r
    raise KeyError(f"Run {run_id} not found")


def list_runs() -> List[Dict[str, Any]]:
    return list(reversed(load_runs()))


def append_run(
    *,
    input_filename: str,
    input_type: str,
    candidate_text: str,
    decision: Dict[str, Any],
    total_tokens: int = 0,
    raw_file_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    input_file_path = ""
    if raw_file_bytes:
        input_file_path = save_upload(input_filename, raw_file_bytes)

    run = create_run(
        input_filename=input_filename,
        input_type=input_type,
        input_file_path=input_file_path,
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
    target = str(run_id).strip()
    for r in load_runs():
        if str(r.get("id")) == target:
            return r
    return None
