"""Unit tests for the PII redaction helper — pattern-based, not
exhaustive (see module docstring in app/control/pii_redaction.py)."""
from app.control.pii_redaction import redact_and_cap, redact_pii


def test_redacts_email_addresses():
    assert redact_pii("contact jane@example.com please") == "contact [REDACTED-EMAIL] please"


def test_redacts_phone_numbers():
    result = redact_pii("call me at 555-123-4567 today")
    assert "555-123-4567" not in result
    assert "[REDACTED-PHONE]" in result


def test_redacts_ssn_like_patterns():
    result = redact_pii("my SSN is 123-45-6789")
    assert "123-45-6789" not in result
    assert "[REDACTED-SSN]" in result


def test_redacts_credit_card_like_digit_sequences():
    result = redact_pii("card number 4111111111111111 expires soon")
    assert "4111111111111111" not in result
    assert "[REDACTED-CARD]" in result


def test_redacts_multiple_patterns_in_one_string():
    result = redact_pii("email jane@example.com or call 555-123-4567")
    assert "jane@example.com" not in result
    assert "555-123-4567" not in result
    assert "[REDACTED-EMAIL]" in result
    assert "[REDACTED-PHONE]" in result


def test_leaves_ordinary_text_unchanged():
    text = "What's the status of invoice INV-1234?"
    assert redact_pii(text) == text


def test_redact_and_cap_truncates_after_redaction():
    long_text = "hello " * 200  # far over any reasonable cap
    result = redact_and_cap(long_text, max_length=50)
    assert len(result) <= 50


def test_redact_and_cap_does_not_truncate_short_text():
    text = "short message"
    assert redact_and_cap(text, max_length=500) == text


def test_redact_and_cap_default_length_is_500():
    long_text = "a" * 1000
    assert len(redact_and_cap(long_text)) == 500
