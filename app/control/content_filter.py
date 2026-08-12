"""Lightweight, rule-based content filter — the first (cheapest, stateless)
gate in the guardrail pipeline, run before rate limiting or intent
classification. Not a moderation/classification system: rejects a small,
explicit set of obvious prompt-injection phrasings and control-character/
null-byte content. See app/main.py for where this sits in the pipeline
(content filter -> rate limit -> context load -> intent classification ->
permission check -> agent routing/dispatch).

Reused for both `/chat` (the user's message) and
`POST /actions/{action_id}/confirm` (the `action_id` path parameter,
which is otherwise unvalidated user-supplied input) — same function,
different input.
"""
import re

# Word separator that also catches hyphen/underscore obfuscation of the
# same phrase (e.g. "ignore-all-previous-instructions"), not just spaces.
_WS = r"[\s_-]+"

_INJECTION_PATTERNS = (
    re.compile(rf"ignore{_WS}(all{_WS})?(previous|prior|above){_WS}instructions", re.IGNORECASE),
    re.compile(rf"disregard{_WS}(all{_WS})?(previous|prior|above){_WS}instructions", re.IGNORECASE),
    re.compile(rf"ignore{_WS}(all{_WS})?(previous|prior|above){_WS}prompts", re.IGNORECASE),
    re.compile(rf"forget{_WS}(all{_WS})?(your{_WS})?(previous{_WS}|prior{_WS})?instructions", re.IGNORECASE),
    re.compile(rf"you{_WS}are{_WS}now{_WS}(in{_WS})?(developer|dan|jailbreak){_WS}mode", re.IGNORECASE),
    re.compile(rf"reveal{_WS}your{_WS}(system{_WS})?prompt", re.IGNORECASE),
    re.compile(rf"override{_WS}(your{_WS}|the{_WS})?system{_WS}(prompt|instructions)", re.IGNORECASE),
)

# Control characters (excluding the common whitespace \t \n \r) and the
# null byte. A real message has no legitimate reason to contain these.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ContentFilterRejectedError(Exception):
    """Raised when input fails the content filter. Safe to surface as a
    generic 400 — this exception's own message is for server-side logs
    only; app/main.py uses a fixed, generic detail text for the caller,
    never explaining which rule matched or why."""


def check_content(text: str) -> None:
    """Raises ContentFilterRejectedError if `text` contains a control
    character/null byte or matches an obvious prompt-injection pattern.
    Returns None (no exception) otherwise.
    """
    if _CONTROL_CHAR_PATTERN.search(text):
        raise ContentFilterRejectedError("Content contains disallowed control characters.")
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise ContentFilterRejectedError("Content matches a disallowed prompt-injection pattern.")
