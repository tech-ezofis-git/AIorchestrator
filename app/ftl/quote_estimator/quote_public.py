"""Public /chat quote shape: Quote 1 fields stay on quote_result; Quote 2 / Quote 3 /
compare are nested under that same object at the API boundary only. The agent JSON
(snake_case Quote 1) is unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ftl.key_format import title_key, title_keys
from app.ftl.quote_estimator import quote_controls as qc
from app.ftl.quote_estimator import quote_variants as qv
from app.ftl.quote_estimator import rules_engine
from app.ftl.quote_estimator.quote_template import compute_totals

TRACE_PUBLIC = "Line Traceability (Why, Quantity Source, Rule, Price Source)"

# Status envelope is always emitted, even when no variant was generated.
_STATUS_KEYS = ("status", "reason", "rules")

# Compared vs Quote 1. Header fields (project, company, contact, …) are never a variant change.
_DIFF_KEYS = (
    "estimate_number",
    "line_items",
    "freight_estimate",
    "subtotal",
    "hst",
    "total",
    "remarks",
    "assumptions",
    "held_items",
    "release_status",
)


def _qty(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("qty") or item.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def _public_trace(trace: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(trace, dict):
        return None
    rules = trace.get("rules") or []
    if isinstance(rules, str):
        rules = [rules]
    return {
        "Why": trace.get("why") or "",
        "Quantity Source": trace.get("qty_source") or "",
        "Rule": list(rules),
        "Price Source": trace.get("price_source") or "",
    }


def _stamp_price_source(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Fill price_source from the Product Master without changing unit prices."""
    q = dict(quote)
    items: List[Dict[str, Any]] = []
    for raw in q.get("line_items") or []:
        if not isinstance(raw, dict):
            continue
        it = dict(raw)
        if not it.get("price_source"):
            row = rules_engine.resolve_master_row(str(it.get("product_code") or ""))
            if row:
                it["price_source"] = row.get("price_source") or rules_engine.MASTER_SOURCE_LABEL
        items.append(it)
    q["line_items"] = items
    return q


def _with_trace(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    return qc.annotate_line_traceability(_stamp_price_source(quote), candidate_text or "")


def _public_line(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item)
    trace = row.pop("trace", None)
    row.pop("origin", None)
    public = title_keys(row)
    public[TRACE_PUBLIC] = _public_trace(trace)
    return public


def _public_lines(items: Any) -> List[Dict[str, Any]]:
    return [_public_line(it) for it in (items or []) if isinstance(it, dict)]


def _with_totals(quote: Dict[str, Any]) -> Dict[str, Any]:
    q = dict(quote)
    if q.get("freight_estimate") in (None, "") and q.get("freight") not in (None, ""):
        q["freight_estimate"] = q["freight"]
    totals = compute_totals(q)
    q["subtotal"] = totals["subtotal"]
    q["hst"] = totals["hst"]
    q["total"] = totals["total"]
    q["freight_estimate"] = totals["freight_estimate"]
    q["freight"] = totals["freight"]
    if totals.get("line_items"):
        merged: List[Dict[str, Any]] = []
        src = list(q.get("line_items") or [])
        for i, slim in enumerate(totals["line_items"]):
            row = dict(src[i]) if i < len(src) and isinstance(src[i], dict) else {}
            row.update(slim)
            merged.append(row)
        q["line_items"] = merged
    return q


def _lines_equal(a: Any, b: Any) -> bool:
    la = [x for x in (a or []) if isinstance(x, dict)]
    lb = [x for x in (b or []) if isinstance(x, dict)]
    if len(la) != len(lb):
        return False

    def sig(it: Dict[str, Any]) -> tuple:
        return (
            str(it.get("product_code") or ""),
            str(it.get("category") or ""),
            round(_qty(it), 4),
            round(float(it.get("unit_price") or it.get("price") or 0), 2),
        )

    return [sig(x) for x in la] == [sig(x) for x in lb]


def _values_equal(key: str, a: Any, b: Any) -> bool:
    if key == "line_items":
        return _lines_equal(a, b)
    if key in ("freight_estimate", "subtotal", "hst", "total"):
        try:
            return abs(float(a or 0) - float(b or 0)) < 0.005
        except (TypeError, ValueError):
            return a == b
    return a == b


def _public_value(key: str, value: Any) -> Any:
    if key == "line_items":
        return _public_lines(value)
    if key in ("held_items", "assumptions", "rules"):
        return title_keys(value) if value not in (None, "") else value
    return value


def _variant_object(prefix: str, info: Dict[str, Any], quote1: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        f"{prefix} Status": info.get("status"),
        f"{prefix} Reason": info.get("reason") or "",
        f"{prefix} Rules": list(info.get("rules") or []),
    }
    result = info.get("result")
    if not result or info.get("status") != qv.STATUS_GENERATED:
        return out
    q2 = _with_totals(result)
    q1 = _with_totals(quote1)
    for key in _DIFF_KEYS:
        if _values_equal(key, q1.get(key), q2.get(key)):
            continue
        out[f"{prefix} {title_key(key)}"] = _public_value(key, q2.get(key))
    return out


def _line_from_quote(quote: Dict[str, Any], code: Any) -> Optional[Dict[str, Any]]:
    for it in quote.get("line_items") or []:
        if isinstance(it, dict) and str(it.get("product_code") or "") == str(code or ""):
            return it
    return None


def _compare_line(slim: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    full = _line_from_quote(source, slim.get("product_code")) or slim
    public = title_keys(slim)
    public[TRACE_PUBLIC] = _public_trace(full.get("trace"))
    return public


def _line_compare(raw: Dict[str, Any], q1: Dict[str, Any], q2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    block: Dict[str, Any] = {}
    if raw.get("added"):
        block["Added"] = [_compare_line(row, q2) for row in raw["added"]]
    if raw.get("removed"):
        block["Removed"] = [_compare_line(row, q1) for row in raw["removed"]]
    changed: List[Dict[str, Any]] = []
    for row in raw.get("config_changes") or []:
        changed.append(title_keys(row))
    for row in raw.get("qty_changes") or []:
        changed.append(title_keys(row))
    for row in raw.get("price_changes") or []:
        changed.append(title_keys(row))
    if changed:
        block["Changed"] = changed
    return block or None


def _compare_object(quote1: Dict[str, Any], info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    result = info.get("result")
    if info.get("status") != qv.STATUS_GENERATED or not result:
        return None
    q1 = _with_totals(quote1)
    q2 = _with_totals(result)
    raw = qv.compare_quotes(q1, q2)
    out: Dict[str, Any] = {}
    line_block = _line_compare(raw, q1, q2)
    if line_block:
        out["Line Item"] = line_block
    delta = raw.get("delta") or {}
    for key, public in (
        ("subtotal", "Subtotal"),
        ("freight", "Freight Estimate"),
        ("hst", "Hst"),
        ("total", "Total"),
    ):
        if abs(float(delta.get(key) or 0)) < 0.005:
            continue
        out[public] = {"From": (qv._totals(q1)[key]), "To": (qv._totals(q2)[key])}
    for key in ("remarks", "assumptions", "held_items", "release_status"):
        if _values_equal(key, q1.get(key), q2.get(key)):
            continue
        out[title_key(key)] = {
            "From": _public_value(key, q1.get(key)),
            "To": _public_value(key, q2.get(key)),
        }
    return out or None


def derive_quote_variants(quote1: Dict[str, Any], candidate_text: str) -> List[Dict[str, Any]]:
    """Deterministic Quote 2 / Quote 3 from a finished Quote 1. Does not mutate quote1."""
    variants = qv.derive_variants(quote1, candidate_text or "")
    base = str(quote1.get("estimate_number") or "EST")
    for info in variants:
        result = info.get("result")
        if not result:
            continue
        result["estimate_number"] = f"{base}-Q{info.get('variant')}"
        info["result"] = _with_totals(result)
    return variants


def public_quote_payload(
    quote1: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
    candidate_text: str = "",
) -> Dict[str, Any]:
    """API-only envelope. quote_result is Title-Case Quote 1 plus nested quote_2 / quote_3
    (prefixed changed fields) and compare (unprefixed diffs only)."""
    q1 = _with_trace(_with_totals(quote1), candidate_text)
    if variants is None:
        variants = derive_quote_variants(q1, candidate_text)
    by_n = {int(v.get("variant") or 0): v for v in variants}
    q2 = by_n.get(2) or {
        "status": qv.STATUS_NO_RECOMMENDED,
        "reason": "",
        "rules": ["QUOTE-002"],
        "result": None,
    }
    q3 = by_n.get(3) or {
        "status": qv.STATUS_NO_THIRD,
        "reason": "",
        "rules": ["QUOTE-003"],
        "result": None,
    }
    body = {k: v for k, v in q1.items() if k != "line_items"}
    quote_result = title_keys(body)
    quote_result["Line Item"] = _public_lines(q1.get("line_items"))
    quote_result["quote_2"] = _variant_object("quote_2", q2, q1)
    quote_result["quote_3"] = _variant_object("quote_3", q3, q1)
    quote_result["compare"] = {
        "quote_2": _compare_object(q1, q2),
        "quote_3": _compare_object(q1, q3),
    }
    return {"quote_result": quote_result}
