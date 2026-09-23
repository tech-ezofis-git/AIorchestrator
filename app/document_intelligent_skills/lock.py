"""Lock Document Intelligent model output to the tenant catalog."""
from __future__ import annotations

from typing import Any, Optional

from app.dashboard.ids import normalize_guid
from app.document_intelligent_skills.rules import EMPTY_TEXT, MIN_CONFIDENCE, NO_MATCH
from app.summary_skills.lock import loads_json_object, normalize_json_text


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


def _index_catalog(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in catalog or []:
        raw_id = str(row.get("repository_id") or "").strip()
        if not raw_id:
            continue
        key = (normalize_guid(raw_id) or raw_id).lower()
        by_id[key] = {
            "repository_id": raw_id,
            "repository_name": str(row.get("repository_name") or "").strip() or raw_id,
        }
    return by_id


def _lookup(catalog_index: dict[str, dict[str, Any]], repo_id: Any, repo_name: Any) -> Optional[dict[str, Any]]:
    rid = normalize_guid(str(repo_id or "")) or str(repo_id or "").strip()
    if rid and rid.lower() in catalog_index:
        return catalog_index[rid.lower()]
    name = str(repo_name or "").strip().lower()
    if not name:
        return None
    for row in catalog_index.values():
        if row["repository_name"].strip().lower() == name:
            return row
    return None


def _candidates_from(value: Any, catalog_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        hit = _lookup(catalog_index, item.get("repository_id") or item.get("id"), item.get("repository_name") or item.get("name"))
        if not hit:
            continue
        key = hit["repository_id"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "repository_id": hit["repository_id"],
                "repository_name": hit["repository_name"],
                "score": _coerce_confidence(item.get("score", item.get("confidence_score", 0.0))),
            }
        )
        if len(out) >= 5:
            break
    return out


def locked_payload(
    *,
    ocr_text: str,
    catalog: Optional[list[dict[str, Any]]] = None,
    confidence_score: float = 0.0,
    repository_id: Any = None,
    repository_name: Any = None,
    rationale: str = "",
    candidates: Any = None,
) -> dict:
    text = (ocr_text or "").strip()
    catalog_index = _index_catalog(catalog or [])
    score = _coerce_confidence(confidence_score)
    hit = _lookup(catalog_index, repository_id, repository_name) if catalog_index else None
    if not text:
        return {
            "repository_id": None,
            "repository_name": None,
            "confidence_score": 0.0,
            "rationale": EMPTY_TEXT,
            "candidates": [],
            "ocr_text": ocr_text or "",
        }
    if not hit or score < MIN_CONFIDENCE:
        hit = None
        if score >= MIN_CONFIDENCE:
            score = min(score, MIN_CONFIDENCE - 0.1)
    ranked = _candidates_from(candidates, catalog_index)
    if hit and not any(c["repository_id"].lower() == hit["repository_id"].lower() for c in ranked):
        ranked.insert(
            0,
            {
                "repository_id": hit["repository_id"],
                "repository_name": hit["repository_name"],
                "score": score,
            },
        )
        ranked = ranked[:5]
    reason = (rationale or "").strip()
    if not hit and not reason:
        reason = NO_MATCH
    return {
        "repository_id": hit["repository_id"] if hit else None,
        "repository_name": hit["repository_name"] if hit else None,
        "confidence_score": score if hit else score,
        "rationale": reason[:800],
        "candidates": ranked,
        "ocr_text": ocr_text or "",
    }


def parse_json_content(content: Any, *, ocr_text: str, catalog: list[dict[str, Any]]) -> dict:
    text = normalize_json_text(content)
    data = loads_json_object(text)
    if not isinstance(data, dict):
        return locked_payload(ocr_text=ocr_text, catalog=catalog, rationale=text[:800] if text else "")
    return locked_payload(
        ocr_text=ocr_text,
        catalog=catalog,
        confidence_score=data.get("confidence_score", data.get("confidence", 0.0)),
        repository_id=data.get("repository_id") or data.get("id"),
        repository_name=data.get("repository_name") or data.get("name"),
        rationale=str(data.get("rationale") or data.get("reason") or ""),
        candidates=data.get("candidates"),
    )
