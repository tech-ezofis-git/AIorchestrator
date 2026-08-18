"""Parse uploaded tenant skill / rule files for SQLite storage."""
from __future__ import annotations

from pathlib import Path

from app.agent_skills.loader import _parse_frontmatter

_ALLOWED_SUFFIXES = {".md", ".mdc"}


def upload_kind(filename: str) -> str:
    """Return ``skill`` for .md files and ``rule`` for .mdc files."""
    suffix = Path((filename or "").strip()).suffix.lower()
    if suffix == ".md":
        return "skill"
    if suffix == ".mdc":
        return "rule"
    raise ValueError("only .md (skill) and .mdc (rule) files are supported")


def parse_tenant_upload(*, filename: str, raw: str) -> tuple[str, str]:
    """Return (source_file, body) with YAML frontmatter stripped when present."""
    name = (filename or "").strip()
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("only .md (skill) and .mdc (rule) files are supported")
    text = (raw or "").lstrip("\ufeff")
    if not text.strip():
        raise ValueError("uploaded file is empty")
    _, body = _parse_frontmatter(text)
    body = body.strip()
    if not body:
        raise ValueError("file has no instruction body after frontmatter")
    return name, body
