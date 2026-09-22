"""Classification skill lock/parse helpers."""
from __future__ import annotations

from typing import Any, Optional

from app.classification_skills.rules import EMPTY_CLASSIFICATION_TEXT
from app.summary_skills.lock import loads_json_object, normalize_json_text


def locked_classification_payload(
    *,
    ocr_text: str,
    confidence_score: float = 0.0,
    document_type: str = "",
    rationale: str = "",
    suggested_labels: Optional[list] = None,
) -> dict:
    text = (ocr_text or "").strip()
    dtype = (document_type or "").strip()
    reason = (rationale or "").strip()
    if not text:
        dtype = dtype or "Unknown"
        reason = reason or EMPTY_CLASSIFICATION_TEXT
    labels = _labels_from(suggested_labels)
    return {
        "confidence_score": _coerce_confidence(confidence_score),
        "document_type": dtype,
        "rationale": reason[:800],
        "suggested_labels": labels,
        "ocr_text": ocr_text or "",
    }


def _labels_from(value: Any) -> list[str]:
    labels: list[str] = []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str) and value.strip():
        raw = [p.strip() for p in value.split(",")]
    else:
        raw = []
    seen: set[str] = set()
    for item in raw:
        slug = str(item or "").strip().lower().replace(" ", "-")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        labels.append(slug)
        if len(labels) >= 8:
            break
    return labels


def _coerce_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return round(score, 1)


def parse_classification_json_content(content: Any, *, ocr_text: str) -> dict:
    text = normalize_json_text(content)
    data = loads_json_object(text)
    if not isinstance(data, dict):
        fallback_type = ""
        if text and not text.startswith("{") and len(text) <= 80:
            fallback_type = text.strip().strip('"')
        return locked_classification_payload(
            ocr_text=ocr_text,
            document_type=fallback_type,
            rationale=text[:800] if text and not fallback_type else "",
        )
    return payload_from_parsed(data, ocr_text=ocr_text)


def payload_from_parsed(data: dict, *, ocr_text: str) -> dict:
    dtype = data.get("document_type") or data.get("type") or data.get("class") or ""
    rationale = data.get("rationale") or data.get("reason") or data.get("explanation") or ""
    labels = data.get("suggested_labels") or data.get("labels") or data.get("categories")
    return locked_classification_payload(
        ocr_text=ocr_text,
        confidence_score=data.get("confidence_score", data.get("confidence", 0.0)),
        document_type=str(dtype or ""),
        rationale=str(rationale or ""),
        suggested_labels=labels,
    )
