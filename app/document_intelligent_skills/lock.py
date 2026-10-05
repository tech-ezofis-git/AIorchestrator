"""Lock Document Intelligent model output to the tenant catalog.

Only the model picks folders and scores; code never adds its own suggestions. Each candidate's
`rationale` is built here from the folder fields that appear in the document text, so it always
states the real reason in one plain line. A pick with no shared fields is dropped unless it is
needed as the model's next closest alternative to reach two candidates. An empty, unreadable or off-catalog model reply raises
ModelResponseError instead of being replaced by a guess.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.dashboard.ids import normalize_guid
from app.document_intelligent_skills.rules import catalog_ref
from app.summary_skills.lock import loads_json_object, normalize_json_text

_MAX_DETAILS = 3
_MIN_DETAIL_CHARS = 3
_MAX_CANDIDATES = 5
# With at least one evidence-backed pick, the model's next picks fill up to this many candidates.
_MIN_CANDIDATES = 2
_MAX_SNIPPET_CHARS = 80
_SMALL_WORDS = {"of", "and", "or", "the", "to", "for", "in", "on", "by", "at"}
# Common label abbreviations ("Inv No" for "Invoice Number"). Only applied to
# multi-word fields so a lone "Number" field never matches the word "no".
_WORD_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "number": ("number", "no", "num", "nbr", "#"),
    "invoice": ("invoice", "inv"),
    "quantity": ("quantity", "qty"),
    "amount": ("amount", "amt"),
    "reference": ("reference", "ref"),
    "account": ("account", "acct", "a/c"),
    "description": ("description", "desc"),
    "address": ("address", "addr"),
    "telephone": ("telephone", "tel", "phone"),
}
_CANONICAL_WORD = {alt: word for word, alts in _WORD_ALTERNATIVES.items() for alt in alts}


class ModelResponseError(ValueError):
    """The model answered, but not with a usable folder pick (empty, not JSON, or off-catalog)."""


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
    for index, row in enumerate(catalog or []):
        raw_id = str(row.get("repository_id") or "").strip()
        if not raw_id:
            continue
        key = (normalize_guid(raw_id) or raw_id).lower()
        by_id[key] = {
            "repository_id": raw_id,
            "repository_name": str(row.get("repository_name") or "").strip() or raw_id,
            "fields": [str(f) for f in (row.get("fields") or []) if str(f or "").strip()],
            "ref": catalog_ref(index).lower(),
        }
    return by_id


def _name_key(name: Any) -> str:
    """'Invoices ' / 'invoice' / 'INVOICE-S' all -> 'invoice' for tolerant name lookup."""
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return re.sub(r"s$", "", key) if len(key) > 4 else key


def _canonical_key(label: Any) -> str:
    """'Inv No' and 'InvoiceNumber' both -> 'invoicenumber'."""
    words = [w.lower() for w in _field_words(str(label or ""))]
    return "".join(_CANONICAL_WORD.get(w, w) for w in words)


def _word_pattern(word: str, *, allow_alternatives: bool) -> str:
    alts = _WORD_ALTERNATIVES.get(word.lower()) if allow_alternatives else None
    if not alts:
        plural = "s?" if word.isalpha() and len(word) > 3 else ""
        return re.escape(word) + plural
    return "(?:" + "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True)) + ")"


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
        multi = len(words) > 1
        body = r"[\s_\-.:/]*".join(_word_pattern(w, allow_alternatives=multi) for w in words)
        pattern = r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"
        match = re.search(pattern, text, re.I)
        if match:
            found.append((match.start(), _detail_label(field, words)))
    return [label for _, label in sorted(found)]


def _model_matched_details(fields: list[str], named: Any) -> list[str]:
    """Field names the model reported for this folder, kept only when they are real fields of it."""
    by_key = {_canonical_key(field): (field, words) for field, words in _usable_fields(fields).values()}
    out: list[str] = []
    for name in named if isinstance(named, list) else []:
        key = _canonical_key(name)
        if key in by_key:
            field, words = by_key[key]
            label = _detail_label(field, words)
            if label not in out:
                out.append(label)
    return out


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


def _folder_rationale(name: str, keywords: list[str]) -> str:
    """One line on why the folder fits this document, built from the details they share."""
    return f"This document has {_join(keywords[:_MAX_DETAILS])} details that match {name}."


def _alternative_rationale(name: str) -> str:
    return f"This document may also fit {name}, the model's next closest folder."


def _lookup(
    catalog_index: dict[str, dict[str, Any]],
    repo_id: Any,
    repo_name: Any,
    ref: Any = None,
) -> Optional[dict[str, Any]]:
    """Resolve a model pick by catalog ref (R3), GUID, exact name, then tolerant name."""
    refs = [str(v or "").strip().lower() for v in (ref, repo_id) if str(v or "").strip()]
    for value in refs:
        if re.fullmatch(r"r?\d+", value):
            wanted = value if value.startswith("r") else f"r{value}"
            for row in catalog_index.values():
                if row.get("ref") == wanted:
                    return row
    rid = normalize_guid(str(repo_id or "")) or str(repo_id or "").strip()
    if rid and rid.lower() in catalog_index:
        return catalog_index[rid.lower()]
    name = str(repo_name or "").strip().lower()
    if not name:
        return None
    for row in catalog_index.values():
        if row["repository_name"].strip().lower() == name:
            return row
    key = _name_key(name)
    if key:
        for row in catalog_index.values():
            if _name_key(row["repository_name"]) == key:
                return row
    return None


def _candidates_from(value: Any, catalog_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        hit = _lookup(
            catalog_index,
            item.get("repository_id") or item.get("id"),
            item.get("repository_name") or item.get("name"),
            item.get("ref"),
        )
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
                "_model_fields": item.get("matched_fields"),
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
    ref: Any = None,
) -> dict:
    text = (ocr_text or "").strip()
    catalog_index = _index_catalog(catalog or [])
    score = _coerce_confidence(confidence_score)
    hit = _lookup(catalog_index, repository_id, repository_name, ref) if catalog_index else None
    if not text:
        return {
            "keywords": [],
            "candidates": [],
            "ocr_text": ocr_text or "",
        }
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
    supported: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    for candidate in ranked:
        folder = _lookup(catalog_index, candidate["repository_id"], candidate["repository_name"])
        fields = folder.get("fields") or []
        found = _all_matched_details(fields, text)
        found += [k for k in _model_matched_details(fields, candidate.pop("_model_fields", None)) if k not in found]
        candidate["keywords"] = found
        if found:
            candidate["rationale"] = _folder_rationale(folder["repository_name"], found)
            supported.append(candidate)
        else:
            candidate["rationale"] = _alternative_rationale(folder["repository_name"])
            alternatives.append(candidate)
    if supported and len(supported) < _MIN_CANDIDATES:
        alternatives.sort(key=lambda c: -c["score"])
        supported.extend(alternatives[: _MIN_CANDIDATES - len(supported)])
    ranked = sorted(supported, key=lambda c: -c["score"])
    keywords: list[str] = []
    seen: set[str] = set()
    for candidate in ranked:
        for keyword in candidate["keywords"]:
            if keyword.lower() not in seen:
                seen.add(keyword.lower())
                keywords.append(keyword)
    return {
        "keywords": keywords,
        "candidates": ranked,
        "ocr_text": ocr_text or "",
    }


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _MAX_SNIPPET_CHARS else flat[:_MAX_SNIPPET_CHARS] + "..."


def parse_json_content(content: Any, *, ocr_text: str, catalog: list[dict[str, Any]]) -> dict:
    """Lock the model reply to the catalog; raise ModelResponseError when it is unusable."""
    raw = str(content or "")
    if not raw.strip():
        raise ModelResponseError("returned an empty response")
    data = loads_json_object(normalize_json_text(raw))
    if not isinstance(data, dict):
        raise ModelResponseError(f"returned a response that is not valid JSON: '{_snippet(raw)}'")
    repository_id = data.get("repository_id") or data.get("id")
    repository_name = data.get("repository_name") or data.get("name")
    ref = data.get("ref")
    candidates = data.get("candidates")
    payload = locked_payload(
        ocr_text=ocr_text,
        catalog=catalog,
        confidence_score=data.get("confidence_score", data.get("confidence", 0.0)),
        repository_id=repository_id,
        repository_name=repository_name,
        candidates=candidates,
        ref=ref,
    )
    named = bool(repository_id or repository_name or ref) or any(
        isinstance(item, dict) for item in (candidates if isinstance(candidates, list) else [])
    )
    catalog_index = _index_catalog(catalog or [])
    resolved = bool(_candidates_from(candidates, catalog_index)) or bool(
        _lookup(catalog_index, repository_id, repository_name, ref)
    )
    if named and not resolved:
        raise ModelResponseError("did not return any folder from this tenant's catalog")
    return payload
