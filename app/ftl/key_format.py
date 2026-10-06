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


def title_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[title_key(k)] = title_value(v) if internal_key(k) in CODE_VALUE_KEYS else title_keys(v)
        return out
    if isinstance(value, list):
        return [title_keys(v) for v in value]
    return value
