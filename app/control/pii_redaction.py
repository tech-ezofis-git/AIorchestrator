"""Lightweight, pattern-based PII redaction (Phase 4b) — masks email
addresses, phone numbers, credit-card-like digit sequences, and SSN-like
patterns before a request/response snippet is ever persisted to the
durable audit_log table (see app/control/audit_store.py).

NOT exhaustive PII protection: this is a small set of regexes for common,
easily-recognized patterns, not a data-loss-prevention system. It won't
catch names, addresses, free-text medical/financial details, or anything
that doesn't match one of these specific shapes. Treat it as a
best-effort reduction of the most common accidental-PII-in-logs cases,
not a compliance guarantee.
"""
import re

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE_PATTERN = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def redact_pii(text: str) -> str:
    """Returns `text` with recognized PII patterns replaced by a fixed
    mask token. Order matters, most-specific first: email (has an
    unambiguous `@`) runs before any digit-based pattern so a digit
    sequence embedded in an address can't be partially consumed first;
    SSN (fixed 3-2-4 dash shape) before the broader credit-card digit-run
    pattern; credit card (13-19 digits) before phone (10-11 digits) so a
    long card number doesn't get mistaken for a shorter phone number.
    """
    redacted = _EMAIL_PATTERN.sub("[REDACTED-EMAIL]", text)
    redacted = _SSN_PATTERN.sub("[REDACTED-SSN]", redacted)
    redacted = _CREDIT_CARD_PATTERN.sub("[REDACTED-CARD]", redacted)
    redacted = _PHONE_PATTERN.sub("[REDACTED-PHONE]", redacted)
    return redacted


def redact_and_cap(text: str, *, max_length: int = 500) -> str:
    """Convenience for the audit write path: redact, then cap length.
    Truncation happens AFTER redaction so a mask token is never itself
    cut in half, and redaction always runs before anything is persisted —
    never store first, then redact."""
    redacted = redact_pii(text)
    if len(redacted) > max_length:
        return redacted[:max_length]
    return redacted
