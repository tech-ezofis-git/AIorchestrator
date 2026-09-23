"""
Quote history — one JSON file (data/quotes.json) holding every generated quote, newest last. No
database: standalone single-user tool, same pattern as the Qualifier project's runs_store.py.
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

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
QUOTES_PATH = os.path.join(DATA_DIR, "quotes.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_quotes() -> List[Dict[str, Any]]:
    with _lock:
        if not os.path.exists(QUOTES_PATH):
            return []
        try:
            with open(QUOTES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error reading quotes from {QUOTES_PATH}: {e}")
            return []


def _save_quotes(quotes: List[Dict[str, Any]]) -> None:
    """Atomically writes quotes list to data/quotes.json using a tempfile and os.replace."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        temp_fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, prefix="quotes_", suffix=".tmp")
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(quotes, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, QUOTES_PATH)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.error(f"Failed atomic save to {QUOTES_PATH}: {e}")
            raise


def save_upload(filename: str, content: bytes) -> str:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOADS_DIR, stored_name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def next_estimate_number() -> str:
    """FTL's real estimates are numbered EST-NNNNNN (see sample: EST-261114). We don't know their
    real sequence, so this just increments a local counter starting from a distinguishable base —
    swap for the real numbering scheme if this ever needs to match FTL's actual estimate log."""
    with _lock:
        quotes = load_quotes()
        n = 900001 + len(quotes)
        return f"EST-{n}"


def create_quote(*, input_filename: str, input_type: str, input_file_path: str) -> Dict[str, Any]:
    with _lock:
        quotes = load_quotes()
        quote = {
            "id": (quotes[-1]["id"] + 1) if quotes else 1,
            "estimate_number": f"EST-{900001 + len(quotes)}",
            "input_filename": input_filename,
            "input_type": input_type,
            "input_file_path": input_file_path,
            "status": "running",
            "result": None,  # the structured quote object returned by the agent
            "html": None,  # rendered HTML, cached so history can redisplay without re-rendering
            "error_detail": None,
            "total_tokens": 0,
            "started_at": _now_iso(),
            "finished_at": None,
        }
        quotes.append(quote)
        _save_quotes(quotes)
        return quote


def update_quote(quote_id: int, **fields: Any) -> Dict[str, Any]:
    with _lock:
        quotes = load_quotes()
        for q in quotes:
            if q["id"] == quote_id:
                q.update(fields)
                _save_quotes(quotes)
                return q
        raise KeyError(f"Quote {quote_id} not found")


def list_quotes() -> List[Dict[str, Any]]:
    with _lock:
        return list(reversed(load_quotes()))


def append_quote(
    *,
    estimate_number: str,
    input_filename: str,
    input_type: str,
    candidate_text: str,
    quote_result: Dict[str, Any],
    rendered_html: Optional[str] = None,
    total_tokens: int = 0,
    raw_file_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    with _lock:
        input_file_path = ""
        if raw_file_bytes:
            input_file_path = save_upload(input_filename, raw_file_bytes)

        quotes = load_quotes()
        quote_id = (quotes[-1]["id"] + 1) if quotes else 1
        quote_entry = {
            "id": quote_id,
            "estimate_number": estimate_number,
            "input_filename": input_filename,
            "input_type": input_type,
            "input_file_path": input_file_path,
            "status": "done",
            "result": quote_result,
            "html": rendered_html,
            "error_detail": None,
            "total_tokens": total_tokens,
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
        }
        quotes.append(quote_entry)
        _save_quotes(quotes)
        return quote_entry


def get_quote(identifier: Any) -> Optional[Dict[str, Any]]:
    target = str(identifier).strip()
    with _lock:
        for q in load_quotes():
            if str(q.get("estimate_number", "")).strip() == target or str(q.get("id", "")).strip() == target:
                return q
        return None
