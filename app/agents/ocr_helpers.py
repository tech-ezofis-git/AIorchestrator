"""Helpers for OCR document jobs: pageno resolution, filepath parsing, MIME guesses."""
from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse


class InvalidOcrPageError(ValueError):
    """Raised when pageno is outside the allowed set."""


class InvalidBlobPathError(ValueError):
    """Raised when a filepath cannot be parsed or fails host allowlist checks."""


@dataclass(frozen=True)
class PageSelection:
    """Resolved OCR page range (1-based, inclusive)."""

    start: int
    end: int
    raw: str

    @property
    def is_range(self) -> bool:
        return self.start != self.end

    def label(self) -> str:
        if self.start == self.end:
            return f"page {self.start}"
        return f"pages {self.start}-{self.end}"


def resolve_pageno(pageno: Optional[str], *, max_pages: int = 5) -> PageSelection:
    """Default 1; 1..max_pages = single page; -1 = 1..max_pages; else invalid."""
    raw = (pageno if pageno is not None else "1").strip()
    if raw == "":
        raw = "1"
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidOcrPageError(f"Invalid pageno '{pageno}'.") from exc

    if value == -1:
        return PageSelection(start=1, end=max_pages, raw=raw)
    if 1 <= value <= max_pages:
        return PageSelection(start=value, end=value, raw=raw)
    raise InvalidOcrPageError(
        f"Invalid pageno '{pageno}'. Use 1..{max_pages}, or -1 for up to {max_pages} pages."
    )


@dataclass(frozen=True)
class BlobRef:
    container: str
    blob_name: str
    account_url_host: Optional[str] = None  # e.g. v6storage.blob.core.windows.net


_BLOB_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<container>[^/]+)/(?P<blob>.+)$",
    re.IGNORECASE,
)


def normalize_filepath(filepath: str) -> str:
    return (filepath or "").strip().replace("\\", "/")


def parse_blob_filepath(
    filepath: str,
    *,
    allowed_host_suffixes: list[str],
) -> BlobRef:
    """Parse full blob URL or container/blob relative path."""
    path = normalize_filepath(filepath)
    if not path:
        raise InvalidBlobPathError("filepath is empty.")

    if path.lower().startswith("http://") or path.lower().startswith("https://"):
        match = _BLOB_URL_RE.match(path)
        if not match:
            raise InvalidBlobPathError("Unrecognized blob URL shape.")
        host = match.group("host").lower()
        if not any(host.endswith(suffix.lower()) for suffix in allowed_host_suffixes if suffix):
            raise InvalidBlobPathError("Blob host is not allowlisted.")
        container = unquote(match.group("container"))
        blob_name = unquote(match.group("blob").split("?", 1)[0])
        if not container or not blob_name:
            raise InvalidBlobPathError("Blob URL missing container or blob name.")
        return BlobRef(container=container, blob_name=blob_name, account_url_host=host)

    # Relative container/blob (optional leading slash)
    path = path.lstrip("/")
    if "/" not in path:
        raise InvalidBlobPathError("Relative filepath must be container/blob.")
    container, blob_name = path.split("/", 1)
    container = unquote(container)
    blob_name = unquote(blob_name)
    if not container or not blob_name:
        raise InvalidBlobPathError("Relative filepath must be container/blob.")
    return BlobRef(container=container, blob_name=blob_name, account_url_host=None)


def guess_content_type(filename: str) -> str:
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"


def filename_from_blob_name(blob_name: str) -> str:
    name = (blob_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name or "document.bin"


DEFAULT_OCR_INSTRUCTION = (
    "Extract fields from the OCR text for whatever document type it is. "
    "Do not invent values. Normalize DATE fields to YYYY-MM-DD when possible."
)


def resolve_instruction(instruction: Optional[str]) -> str:
    text = (instruction or "").strip()
    return text or DEFAULT_OCR_INSTRUCTION


def parse_parameter_entries(entries: list[str]) -> list[tuple[str, str]]:
    """Split 'Name,TYPE' entries into (name, type)."""
    parsed: list[tuple[str, str]] = []
    for entry in entries:
        raw = (entry or "").strip()
        if not raw:
            continue
        if "," in raw:
            name, typ = raw.rsplit(",", 1)
            parsed.append((name.strip(), typ.strip() or "SHORT_TEXT"))
        else:
            parsed.append((raw, "SHORT_TEXT"))
    return parsed
