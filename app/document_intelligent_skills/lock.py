"""Lock Document Intelligent model output to the tenant catalog.

The model picks the folder; each candidate's `rationale` is built here from the folder fields that
appear in the document text, so it always states the real reason in one plain line. When the model
returns too few candidates, folders whose fields or name appear in the text are added as suggestions.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.dashboard.ids import normalize_guid
from app.document_intelligent_skills.rules import MIN_CONFIDENCE
from app.summary_skills.lock import loads_json_object, normalize_json_text

_MAX_DETAILS = 3
_MIN_DETAIL_CHARS = 3
_MAX_CANDIDATES = 5
_MIN_SUGGESTIONS = 3
_MIN_NAME_WORD_CHARS = 4
_SMALL_WORDS = {"of", "and", "or", "the", "to", "for", "in", "on", "by", "at"}


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
            "fields": [str(f) for f in (row.get("fields") or []) if str(f or "").strip()],
        }
    return by_id


def _field_words(field: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", field)
    return re.findall(r"[A-Za-z0-9]+", spaced)


def _usable_fields(fields: list[str]) -> dict[str, tuple[str, list[str]]]:
    out: dict[str, tuple[str, list[str]]] = {}
    for field in fields:
        words = _field_words(field)
        if words and len("".join(words)) >= _MIN_DETAIL_CHARS:
            out.setdefault(" ".join(words).lower(), (field, words))
    return out


def _all_matched_details(fields: list[str], text: str) -> list[str]:
    """Every field name of the folder found in the document text, in the order they appear."""
    found: list[tuple[int, str]] = []
    for field, words in _usable_fields(fields).values():
        pattern = r"(?<![A-Za-z0-9])" + r"[\s_\-.:/]*".join(map(re.escape, words)) + r"(?![A-Za-z0-9])"
        match = re.search(pattern, text, re.I)
        if match:
            found.append((match.start(), _detail_label(field, words)))
    return [label for _, label in sorted(found)]


def _matched_details(fields: list[str], text: str) -> list[str]:
    return _all_matched_details(fields, text)[:_MAX_DETAILS]


def _name_in_text(name: str, text: str) -> bool:
    """True when a meaningful word of the folder name ("Invoices" -> "invoice") is in the text."""
    for word in _field_words(name):
        word = word.lower()
        if len(word) < _MIN_NAME_WORD_CHARS or word in _SMALL_WORDS:
            continue
        stem = re.sub(r"(es|s)$", "", word) if len(word) > _MIN_NAME_WORD_CHARS else word
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(stem) + r"(?:es|s)?(?![A-Za-z0-9])", text, re.I):
            return True
    return False


def _suggested_folders(
    catalog_index: dict[str, dict[str, Any]], text: str, exclude: set[str]
) -> list[dict[str, Any]]:
    """Folders ranked by how many of their fields (and name) show up in the text; code-side fallback."""
    ceiling = MIN_CONFIDENCE - 5
    scored: list[tuple[float, int, str, dict[str, Any]]] = []
    for key, folder in catalog_index.items():
        if key in exclude:
            continue
        hits = len(_all_matched_details(folder["fields"], text))
        name_hit = _name_in_text(folder["repository_name"], text)
        if not hits and not name_hit:
            continue
        total = len(_usable_fields(folder["fields"])) + 1
        score = round(ceiling * (hits + int(name_hit)) / total, 1)
        scored.append((score, hits, folder["repository_name"].lower(), folder))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [
        {"repository_id": folder["repository_id"], "repository_name": folder["repository_name"], "score": score}
        for score, _, _, folder in scored
    ]


def _detail_label(field: str, words: list[str]) -> str:
    """"BL Number" stays as typed; "freightCharge" / "BILL_OF_LADING" become "Freight Charge" / "Bill of Lading"."""
    if " " in field.strip():
        return field.strip()
    out = []
    for i, word in enumerate(words):
        if word.isupper() and len(word) <= 3 and word.lower() not in _SMALL_WORDS:
            out.append(word)
        elif i and word.lower() in _SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _folder_rationale(folder: dict[str, Any], text: str) -> str:
    """One line on why `folder` fits this document, built from the details they share."""
    details = _matched_details(folder.get("fields") or [], text)
    name = folder["repository_name"]
    if details:
        return f"This document has {_join(details)} details that match {name}."
    return f"This document shares no specific details with {name}; it was suggested from its overall content."


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
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


def locked_payload(
    *,
    ocr_text: str,
    catalog: Optional[list[dict[str, Any]]] = None,
    confidence_score: float = 0.0,
    repository_id: Any = None,
    repository_name: Any = None,
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
            "candidates": [],
            "ocr_text": ocr_text or "",
        }
    if not hit or score < MIN_CONFIDENCE:
        hit = None
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
        ranked = ranked[:_MAX_CANDIDATES]
    if len(ranked) < _MIN_SUGGESTIONS:
        taken = {c["repository_id"].lower() for c in ranked}
        extra = _suggested_folders(catalog_index, text, taken)
        ranked.extend(extra[: _MIN_SUGGESTIONS - len(ranked)])
    for candidate in ranked:
        folder = _lookup(catalog_index, candidate["repository_id"], candidate["repository_name"])
        candidate["rationale"] = _folder_rationale(folder, text)
    return {
        "repository_id": hit["repository_id"] if hit else None,
        "repository_name": hit["repository_name"] if hit else None,
        "candidates": ranked,
        "ocr_text": ocr_text or "",
    }


def parse_json_content(content: Any, *, ocr_text: str, catalog: list[dict[str, Any]]) -> dict:
    text = normalize_json_text(content)
    data = loads_json_object(text)
    if not isinstance(data, dict):
        return locked_payload(ocr_text=ocr_text, catalog=catalog)
    return locked_payload(
        ocr_text=ocr_text,
        catalog=catalog,
        confidence_score=data.get("confidence_score", data.get("confidence", 0.0)),
        repository_id=data.get("repository_id") or data.get("id"),
        repository_name=data.get("repository_name") or data.get("name"),
        candidates=data.get("candidates"),
    )
