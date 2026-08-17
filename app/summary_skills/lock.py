"""Summary skill lock/parse helpers — apply Summary rules to model output."""
from __future__ import annotations

from typing import Optional

from app.summary_skills.rules import EMPTY_SUMMARY_TEXT, apply_highlight_rules


def locked_summary_payload(
    *,
    ocr_text: str,
    confidence_score: float = 0.0,
    document_type: str = "",
    document_title: str = "",
    document_language: str = "",
    document_summary: str = "",
    key_facts_extracted: Optional[list] = None,
) -> dict:
    summary = (document_summary or "").strip()
    if not (ocr_text or "").strip() and not summary:
        summary = EMPTY_SUMMARY_TEXT
    facts = list(key_facts_extracted or [])
    if summary != EMPTY_SUMMARY_TEXT:
        summary, facts = apply_highlight_rules(
            document_summary=summary,
            key_facts_extracted=facts,
            ocr_text=ocr_text or "",
        )
    return {
        "confidence_score": confidence_score,
        "document_type": (document_type or "").strip(),
        "document_title": (document_title or "").strip(),
        "document_language": (document_language or "").strip(),
        "document_summary": summary,
        "key_facts_extracted": facts,
        "ocr_text": ocr_text or "",
    }


def normalize_json_text(content) -> str:
    import json as _json
    import re as _re

    if isinstance(content, dict):
        return _json.dumps(content, ensure_ascii=False)
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", "replace")
    text = str(content or "").strip().lstrip("\ufeff")
    if text.startswith("```"):
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
        text = text.strip()
    text = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    return text


def brace_block(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    quote = ""
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def repair_json_text(text: str) -> str:
    import re as _re

    return _re.sub(r",\s*([}\]])", r"\1", text)


def balance_json_text(text: str) -> str:
    in_str = False
    escape = False
    quote = ""
    stack: list[str] = []
    for ch in text:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        text += quote
    return text + "".join(reversed(stack))


def loads_json_object(content):
    import json as _json

    if isinstance(content, dict):
        return content

    text = normalize_json_text(content)
    if not text:
        return None

    candidates = [text]
    block = brace_block(text)
    if block and block not in candidates:
        candidates.append(block)
    repaired = repair_json_text(text)
    if repaired not in candidates:
        candidates.append(repaired)
    if block:
        repaired_block = repair_json_text(block)
        if repaired_block not in candidates:
            candidates.append(repaired_block)
    for base in list(candidates):
        balanced = balance_json_text(base)
        if balanced not in candidates:
            candidates.append(balanced)
        balanced_repaired = balance_json_text(repair_json_text(base))
        if balanced_repaired not in candidates:
            candidates.append(balanced_repaired)

    for candidate in candidates:
        current = candidate
        for _ in range(4):
            try:
                data = _json.loads(current)
            except _json.JSONDecodeError:
                start = current.find("{")
                if start < 0:
                    break
                try:
                    data, _ = _json.JSONDecoder().raw_decode(current[start:])
                except Exception:
                    break
            if isinstance(data, dict):
                return data
            if isinstance(data, str):
                current = normalize_json_text(data)
                nested_block = brace_block(current)
                if nested_block:
                    current = nested_block
                continue
            break
    return None


def facts_from(value) -> list[str]:
    facts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if item is None:
                continue
            fact = str(item).strip()
            if fact:
                facts.append(fact)
    elif isinstance(value, str) and value.strip():
        nested = loads_json_object(value)
        if isinstance(nested, list):
            return facts_from(nested)
        facts = [value.strip()]
    return facts


def coerce_confidence(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return round(score, 1)


def payload_from_parsed(data: dict, *, ocr_text: str) -> dict:
    nested = loads_json_object(data.get("document_summary"))
    if isinstance(nested, dict) and (
        "key_facts_extracted" in nested or "confidence_score" in nested
    ):
        data = nested

    return locked_summary_payload(
        ocr_text=ocr_text,
        confidence_score=coerce_confidence(data.get("confidence_score")),
        document_type=str(data.get("document_type") or ""),
        document_title=str(data.get("document_title") or ""),
        document_language=str(data.get("document_language") or ""),
        document_summary=str(data.get("document_summary") or ""),
        key_facts_extracted=facts_from(data.get("key_facts_extracted")),
    )


def parse_summary_json_content(content: str, *, ocr_text: str) -> dict:
    text = normalize_json_text(content)
    data = loads_json_object(text)
    if isinstance(data, dict):
        payload = payload_from_parsed(data, ocr_text=ocr_text)
    else:
        payload = locked_summary_payload(
            ocr_text=ocr_text,
            document_summary=text,
        )

    if (
        payload["confidence_score"] == 0.0
        and not payload["key_facts_extracted"]
        and (payload["document_summary"] or "").lstrip().startswith("{")
    ):
        nested = loads_json_object(payload["document_summary"])
        if isinstance(nested, dict):
            return payload_from_parsed(nested, ocr_text=ocr_text)
    return payload
