"""FTL API keys are Title Case with spaces ("Project Name", "Line Items", "Pdf Base64"); the agents,
stores and PDF renderer work in snake_case. Convert at the API boundary only.

Short code values (decision, project type, match, category, flags) get the same treatment:
"needs_review" <-> "Needs Review", "door_operator" <-> "Door Operator". Free text, product codes
and numbers are left untouched.

snake_key accepts Title Case, snake_case and camelCase, so every older input shape keeps working.
"""

from __future__ import annotations

import re
from typing import Any

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")
CODE_VALUE_KEYS = frozenset({"qualify", "project_type", "match", "category", "flags"})


def snake_key(key: Any) -> str:
    text = _CAMEL_BOUNDARY_RE.sub(r"_\1", str(key).strip())
    return re.sub(r"[\s_]+", "_", text).lower()


def title_key(key: Any) -> str:
    return " ".join(word.capitalize() for word in snake_key(key).split("_") if word)


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
            key = snake_key(k)
            out[key] = snake_value(v) if key in CODE_VALUE_KEYS else snake_keys(v)
        return out
    if isinstance(value, list):
        return [snake_keys(v) for v in value]
    return value


def title_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[title_key(k)] = title_value(v) if snake_key(k) in CODE_VALUE_KEYS else title_keys(v)
        return out
    if isinstance(value, list):
        return [title_keys(v) for v in value]
    return value
