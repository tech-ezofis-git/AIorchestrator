"""Pick Quote 1 / Quote 2 / Quote 3 form data for a PDF fill (no model call)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_STATUS_GENERATED = "GENERATED"
_SKIP_TOP = frozenset(
    {
        "quote_2",
        "quote_3",
        "compare",
        "quote 2",
        "quote 3",
        "line_traceability",
    }
)
_SKIP_OVERLAY = frozenset({"status", "reason", "rules"})

_TYPE_ALIASES = {
    "1": 1,
    "q1": 1,
    "quote 1": 1,
    "main": 1,
    "base": 1,
    "2": 2,
    "q2": 2,
    "quote 2": 2,
    "opt": 2,
    "option": 2,
    "recommended": 2,
    "3": 3,
    "q3": 3,
    "quote 3": 3,
    "alt": 3,
    "alternate": 3,
}


def parse_quote_type(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return 1
    token = re.sub(r"[\s_\-]+", " ", str(value).strip().lower())
    if token not in _TYPE_ALIASES:
        raise ValueError("quoteType must be Quote 1, Quote 2, or Quote 3.")
    return _TYPE_ALIASES[token]


def _norm_key(key: Any) -> str:
    return re.sub(r"[\s_]+", "_", str(key or "").strip().lower())


def _unprefix_variant(obj: Dict[str, Any], n: int) -> Dict[str, Any]:
    prefixes = (f"quote_{n} ", f"quote_{n}_", f"quote {n} ", f"quote {n}_")
    out: Dict[str, Any] = {}
    for key, value in obj.items():
        text = str(key)
        lower = text.lower()
        stripped = text
        for prefix in prefixes:
            if lower.startswith(prefix):
                stripped = text[len(prefix) :]
                break
        if stripped and _norm_key(stripped) not in _SKIP_OVERLAY:
            out[stripped] = value
    return out


def _variant_from(form_data: Dict[str, Any], explicit: Any, n: int) -> Optional[Dict[str, Any]]:
    if isinstance(explicit, dict) and explicit:
        return explicit
    for key, value in form_data.items():
        if _norm_key(key) == f"quote_{n}" and isinstance(value, dict):
            return value
    prefixed = {
        k: v
        for k, v in form_data.items()
        if str(k).lower().startswith(f"quote_{n} ") or str(k).lower().startswith(f"quote_{n}_")
    }
    return prefixed or None


def _skip_variant_key(key: Any) -> bool:
    nk = _norm_key(key)
    return nk in _SKIP_TOP or nk.startswith("quote_2") or nk.startswith("quote_3")


def _base_form(form_data: Dict[str, Any], quote_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    if isinstance(quote_result, dict):
        for key, value in quote_result.items():
            if not _skip_variant_key(key):
                base[key] = value
    for key, value in form_data.items():
        if _skip_variant_key(key):
            continue
        base[key] = value
    return base


def _status_of(variant: Dict[str, Any], n: int) -> str:
    for key in (f"quote_{n} Status", f"quote_{n}_Status", "Status", "status"):
        if key in variant and variant[key] not in (None, ""):
            return str(variant[key]).strip()
    return _STATUS_GENERATED


def form_data_for_quote_type(
    *,
    form_data: Optional[Dict[str, Any]] = None,
    quote_type: Any = None,
    quote_2: Optional[Dict[str, Any]] = None,
    quote_3: Optional[Dict[str, Any]] = None,
    quote_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], int]:
    """Return (template formData, 1|2|3). Quote 2/3 overlay unprefixed fields onto Quote 1."""
    form = form_data if isinstance(form_data, dict) else {}
    n = parse_quote_type(quote_type)
    base = _base_form(form, quote_result)
    if n == 1:
        return base, 1
    explicit = quote_2 if n == 2 else quote_3
    variant = _variant_from(form, explicit, n)
    if not variant and isinstance(quote_result, dict):
        variant = _variant_from(quote_result, None, n)
    if not variant:
        raise ValueError(
            f"Quote {n} data was not sent. Pass quote_{n} (or nest it in formData) to generate that PDF."
        )
    status = _status_of(variant, n)
    if status.upper() != _STATUS_GENERATED:
        raise ValueError(f"Quote {n} is '{status}'. A PDF is only generated when Status is GENERATED.")
    merged = dict(base)
    merged.update(_unprefix_variant(variant, n))
    return merged, n
