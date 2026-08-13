"""Extract plain text from a .docx (Office Open XML) without Paddle.

A .docx is a zip. We read word/document.xml and join paragraph text.
Legacy .doc (OLE) is not handled here.
"""
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class DocxExtractError(ValueError):
    """Raised when bytes are not a readable .docx."""


def looks_like_docx(
    filename: str | None = None,
    content_type: str | None = None,
    filepath: str | None = None,
) -> bool:
    name = (filename or filepath or "").lower().replace("\\", "/")
    ctype = (content_type or "").lower().split(";")[0].strip()
    return name.endswith(".docx") or ctype in _DOCX_TYPES


def looks_like_legacy_doc(
    filename: str | None = None,
    content_type: str | None = None,
    filepath: str | None = None,
) -> bool:
    if looks_like_docx(filename, content_type, filepath):
        return False
    name = (filename or filepath or "").lower().replace("\\", "/")
    ctype = (content_type or "").lower().split(";")[0].strip()
    return name.endswith(".doc") or ctype == "application/msword"


def extract_docx_text(data: bytes) -> str:
    if not data:
        raise DocxExtractError("Uploaded .docx is empty.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except KeyError as exc:
        raise DocxExtractError("File is not a valid .docx (missing word/document.xml).") from exc
    except zipfile.BadZipFile as exc:
        raise DocxExtractError("File is not a valid .docx.") from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise DocxExtractError("File is not a valid .docx (broken XML).") from exc

    paragraphs: list[str] = []
    for para in root.iter(f"{_W}p"):
        parts = [(node.text or "") for node in para.iter(f"{_W}t")]
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)
