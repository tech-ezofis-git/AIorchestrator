"""FTL API keys are Title Case with spaces ("Project Name", "Line Items", "Pdf Base64"); the agents,
stores and PDF renderer work in snake_case. Convert at the API boundary only.

Short code values (decision, project type, match, category, flags) get the same treatment:
"needs_review" <-> "Needs Review", "door_operator" <-> "Door Operator". Free text, product codes
and numbers are left untouched.

A few keys have a public name that isn't the Title Case of the internal key (PUBLIC_KEY_NAMES,
e.g. customer_name -> "Company Name"). On input both the public name and the older Title Case name
are accepted, so every older input shape keeps working.
"""

from __future__ import annotations

import re
from typing import Any

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")
CODE_VALUE_KEYS = frozenset({"qualify", "project_type", "match", "category", "flags"})

PUBLIC_KEY_NAMES = {
    "project_type": "Project type",
    "project_name": "Project",
    "matched_items": "Matched items",
    "excluded_items": "Excluded items",
    "hold_items": "Hold items",
    "customer_name": "Company Name",
    "contact_name": "Contact",
    "contact_phone": "Phone Number",
    "estimate_number": "Order Number",
    "quote_date": "Date",
    "line_items": "Line Item",
    "product_code": "Product",
    "unit_price": "Price",
    "duplicate_info": "Duplicate Info",
}


def snake_key(key: Any) -> str:
    text = _CAMEL_BOUNDARY_RE.sub(r"_\1", str(key).strip())
    return re.sub(r"[\s_]+", "_", text).lower()


_INTERNAL_KEY_ALIASES = {
    snake_key(public): internal
    for internal, public in PUBLIC_KEY_NAMES.items()
    if snake_key(public) != internal
}


def internal_key(key: Any) -> str:
    snake = snake_key(key)
    return _INTERNAL_KEY_ALIASES.get(snake, snake)


def title_key(key: Any) -> str:
    internal = internal_key(key)
    if internal in PUBLIC_KEY_NAMES:
        return PUBLIC_KEY_NAMES[internal]
    return " ".join(word.capitalize() for word in internal.split("_") if word)


def title_value(value: Any) -> Any:
    if isinstance(value, list):
        return [title_value(v) for v in value]
    if not isinstance(value, str):
        return value
    return " ".join(word.capitalize() for word in re.split(r"[\s_]+", value.strip()) if word)


def snake_value(value: Any) -> Any:
    if isinstance(value, list):
        return [snake_value(v) for v in value]
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s_]+", "_", value.strip()).lower()


def snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = internal_key(k)
            out[key] = snake_value(v) if key in CODE_VALUE_KEYS else snake_keys(v)
        return out
    if isinstance(value, list):
        return [snake_keys(v) for v in value]
    return value


def camel_key(key: Any) -> str:
    """Convert snake_case, Title Case, or space-separated key to camelCase."""
    text = str(key).strip()
    if not text:
        return ""
    s = snake_key(text)
    parts = [p for p in s.split("_") if p]
    if not parts:
        return ""
    return parts[0].lower() + "".join(w.capitalize() for w in parts[1:])


def camel_keys(value: Any) -> Any:
    """Recursively convert dictionary keys to camelCase."""
    if isinstance(value, dict):
        return {camel_key(k): camel_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camel_keys(v) for v in value]
    return value


def title_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            ik = internal_key(k)
            if ik == "duplicate_info":
                out[title_key(k)] = camel_keys(v) if isinstance(v, (dict, list)) else v
                continue
            out[title_key(k)] = title_value(v) if ik in CODE_VALUE_KEYS else title_keys(v)
        return out
    if isinstance(value, list):
        return [title_keys(v) for v in value]
    return value

