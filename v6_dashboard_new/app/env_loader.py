"""Load local.settings.json Values into os.environ."""
from __future__ import annotations

import json
import os
from pathlib import Path

_LOADED = False


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_local_settings(*, force: bool = False, override: bool = False) -> Path | None:
    global _LOADED
    if _LOADED and not force:
        return project_root() / "local.settings.json"

    root = project_root()
    path = root / "local.settings.json"
    if not path.is_file():
        _LOADED = True
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"[ENV] could not read local.settings.json: {error}")
        _LOADED = True
        return path

    values = payload.get("Values") or {}
    if isinstance(values, dict):
        for key, value in values.items():
            if value is None:
                continue
            name = str(key).strip()
            if not name:
                continue
            if not override and os.getenv(name):
                continue
            os.environ[name] = str(value)

    _LOADED = True
    return path
