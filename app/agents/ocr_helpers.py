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
    # Original query string (SAS token: sv=...&sig=...), if the caller
    # supplied a full blob URL carrying one. Code-review finding #8: this
    # used to be silently discarded, so the HTTP fallback path in
    # OcrEngineClient._download_blob (used whenever
    # AZURE_STORAGE_CONNECTION_STRING isn't set) reconstructed the URL
    # with NO auth at all — any SAS-secured blob URL would 401/403.
    query: Optional[str] = None


_BLOB_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<container>[^/]+)/(?P<blob>.+)$",
    re.IGNORECASE,
)


def normalize_filepath(filepath: str) -> str:
    return (filepath or "").strip().replace("\\", "/")


DEFAULT_BLOB_CONTAINER_PREFIX = "ezts"


def tenant_blob_container(
    tenant_id: str,
    *,
    prefix: str = DEFAULT_BLOB_CONTAINER_PREFIX,
) -> str:
    """Azure container: ezts + tenant UUID with hyphens stripped, lowercased."""
    tid = (tenant_id or "").strip().replace("-", "").lower()
    head = (prefix or DEFAULT_BLOB_CONTAINER_PREFIX).strip() or DEFAULT_BLOB_CONTAINER_PREFIX
    if not tid:
        raise InvalidBlobPathError("tenant_id is required for blob filepath.")
    return f"{head}{tid}"


def parse_blob_filepath(
    filepath: str,
    *,
    allowed_host_suffixes: list[str],
    tenant_id: Optional[str] = None,
    container_prefix: str = DEFAULT_BLOB_CONTAINER_PREFIX,
) -> BlobRef:
    """Parse a full blob URL, or tenant-scoped folder/file path.

    Relative `filepath` is folder + file inside container `ezts{tenantid}`
    (hyphens stripped). Full https URLs still carry their own container.
    """
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
        raw_blob = match.group("blob")
        blob_name, _, query = raw_blob.partition("?")
        blob_name = unquote(blob_name)
        if not container or not blob_name:
            raise InvalidBlobPathError("Blob URL missing container or blob name.")
        return BlobRef(
            container=container,
            blob_name=blob_name,
            account_url_host=host,
            query=query or None,
        )

    blob_name = unquote(path.lstrip("/"))
    if not blob_name:
        raise InvalidBlobPathError("filepath is empty.")
    container = tenant_blob_container(tenant_id or "", prefix=container_prefix)
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


# Types used in Name,TYPE parameter strings. Used to recover when Swagger
# users paste several fields into one row: "Invoice No,SHORT_TEXT,Due Date,DATE".
_KNOWN_PARAM_TYPES = (
    "SHORT_TEXT",
    "LONG_TEXT",
    "DATE",
    "NUMBER",
    "AMOUNT",
    "CURRENCY",
    "BOOLEAN",
    "EMAIL",
    "PHONE",
)
_TYPE_BOUNDARY = re.compile(
    r",\s*(" + "|".join(_KNOWN_PARAM_TYPES) + r")(?=,|$)",
    re.IGNORECASE,
)


def parse_parameter_entries(entries: list[str]) -> list[tuple[str, str]]:
    """Split 'Name,TYPE' entries into (name, type).

    Also expands a single concatenated chain of Name,TYPE pairs.
    """
    parsed: list[tuple[str, str]] = []
    for entry in entries:
        raw = (entry or "").strip()
        if not raw:
            continue
        parsed.extend(_split_name_type_chain(raw))
    return parsed


def _split_name_type_chain(raw: str) -> list[tuple[str, str]]:
    matches = list(_TYPE_BOUNDARY.finditer(raw))
    if not matches:
        if "," in raw:
            name, typ = raw.rsplit(",", 1)
            return [(name.strip(), typ.strip() or "SHORT_TEXT")]
        return [(raw, "SHORT_TEXT")]

    result: list[tuple[str, str]] = []
    start = 0
    for match in matches:
        name = raw[start : match.start()].lstrip(" ,\t").rstrip()
        typ = match.group(1).strip().upper()
        if name:
            result.append((name, typ))
        start = match.end()
    return result
