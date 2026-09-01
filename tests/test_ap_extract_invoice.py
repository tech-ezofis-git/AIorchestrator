"""Unit tests for app/ap_skills/extract_invoice.py's LLM/heuristic merge —
code-review finding #6 (hallucinated/ungrounded LLM field values must not
silently override the heuristic OCR-text-derived value) and finding #13
(invoice/due dates were never parsed or validated at all)."""
from app.ap_skills.extract_invoice import (
    _as_invoice,
    _coalesce_invoice,
    _guess_vendor,
    _header_from_column_layout,
    _is_grounded,
    _normalize_date,
    _shape_ok,
)

_OCR_TEXT = "Invoice No INV-2026-6001 Vendor: Acme Industrial Ltd PO Number PO-60001 Total 5203.65"


def test_grounded_llm_value_overrides_heuristic():
    fallback = {"invoice_number": "", "vendor": "", "po_number": "", "total": None, "currency": ""}
    primary = {"invoice_number": "INV-2026-6001", "vendor": "Acme Industrial Ltd"}
    merged, ungrounded = _coalesce_invoice(primary, fallback, _OCR_TEXT)
    assert merged["invoice_number"] == "INV-2026-6001"
    assert merged["vendor"] == "Acme Industrial Ltd"
    assert ungrounded == []


def test_ungrounded_llm_value_is_rejected_and_reported():
    fallback = {
        "invoice_number": "INV-2026-6001",
        "vendor": "Acme Industrial Ltd",
        "po_number": "PO-60001",
        "total": 5203.65,
        "currency": "",
    }
    # A hallucinated vendor and invoice number that never appear in the OCR text.
    primary = {"invoice_number": "INV-9999-FAKE", "vendor": "Totally Made Up Corp"}
    merged, ungrounded = _coalesce_invoice(primary, fallback, _OCR_TEXT)
    # Heuristic values win instead of the hallucinated ones.
    assert merged["invoice_number"] == "INV-2026-6001"
    assert merged["vendor"] == "Acme Industrial Ltd"
    assert set(ungrounded) == {"invoice_number", "vendor"}


def test_ungrounded_total_is_rejected():
    fallback = {"invoice_number": "INV-1", "vendor": "Acme", "po_number": "PO-1", "total": 5203.65, "currency": ""}
    primary = {"total": 999999.99}
    merged, ungrounded = _coalesce_invoice(primary, fallback, _OCR_TEXT)
    assert merged["total"] == 5203.65
    assert "total" in ungrounded


def test_non_groundable_fields_are_never_gated():
    """doc_type/line_items aren't in _GROUNDABLE_FIELDS — they pass
    through untouched regardless of OCR text content."""
    fallback = {"invoice_number": "", "vendor": "", "po_number": "", "total": None, "currency": ""}
    primary = {"doc_type": "invoice", "line_items": [{"description": "Widget", "qty": 1}]}
    merged, ungrounded = _coalesce_invoice(primary, fallback, _OCR_TEXT)
    assert merged["doc_type"] == "invoice"
    assert ungrounded == []


def test_is_grounded_tolerates_formatting_differences():
    assert _is_grounded("INV-2026-6001", "invoice no: inv 2026 6001 on file") is True
    assert _is_grounded("Acme Industrial Ltd", "vendor ACME INDUSTRIAL LTD") is True


def test_is_grounded_rejects_short_numeric_false_positives():
    # A too-short digit sequence must not trivially "match" anywhere.
    assert _is_grounded(42, "invoice total 4205203.65 due") is False


def test_is_grounded_false_for_empty_ocr_text():
    assert _is_grounded("Acme", "") is False


def test_normalize_date_accepts_common_formats():
    assert _normalize_date("05/20/26") == "2026-05-20"
    assert _normalize_date("2026-05-20") == "2026-05-20"
    assert _normalize_date("May 20, 2026") == "2026-05-20"
    assert _normalize_date("20-May-2026") == "2026-05-20"


def test_normalize_date_rejects_garbage():
    assert _normalize_date("not a date") is None
    assert _normalize_date("") is None
    assert _normalize_date(None) is None


def test_as_invoice_normalizes_dates_and_flags_unparsed():
    clean = _as_invoice({"invoice_date": "05/20/26", "due_date": "06/20/26"})
    assert clean["invoice_date"] == "2026-05-20"
    assert clean["due_date"] == "2026-06-20"
    assert "invoice_date_unparsed" not in clean
    assert "due_date_unparsed" not in clean

    dirty = _as_invoice({"invoice_date": "not-a-real-date"})
    # Raw text is preserved (never silently dropped or substituted)...
    assert dirty["invoice_date"] == "not-a-real-date"
    # ...but flagged so a caller can tell it wasn't validated.
    assert dirty["invoice_date_unparsed"] is True


def test_guess_vendor_skips_the_whole_bill_to_block():
    """Code-review finding #14: the buyer's company name (inside the "Bill
    To" address block, on the line(s) AFTER the label) must not be picked
    as the vendor even though it has an entity suffix too — the real
    vendor's letterhead, appearing after the block, should win instead."""
    text = """Bill To:
STERLING MANUFACTURING GROUP LTD.
123 Buyer Street
Springfield, ST 00000

APEX INDUSTRIAL COMPONENTS LTD
615 Enterprise Parkway
INVOICE
"""
    assert _guess_vendor(text) == "APEX INDUSTRIAL COMPONENTS LTD"


def test_guess_vendor_still_finds_vendor_before_bill_to_block():
    text = """APEX INDUSTRIAL COMPONENTS LTD
615 Enterprise Parkway
Bill To:
STERLING MANUFACTURING GROUP LTD.
INVOICE
"""
    assert _guess_vendor(text) == "APEX INDUSTRIAL COMPONENTS LTD"


def test_shape_ok_rejects_mismatched_label_value_pairs():
    assert _shape_ok("Invoice No", "Fed Ground") is False  # no digits at all
    assert _shape_ok("Invoice No", "INV-2026-6001") is True
    assert _shape_ok("Due Date", "Fed Ground") is False
    assert _shape_ok("Due Date", "06/20/26") is True
    assert _shape_ok("Currency", "31") is False
    assert _shape_ok("Currency", "CAD") is True
    assert _shape_ok("Terms", "anything at all") is True  # unchecked label


def test_column_layout_drops_a_shape_mismatched_pair_but_keeps_the_rest():
    """A block where the label/value lines are offset by one (a dropped
    OCR line) — Invoice No ends up paired with a non-numeric value and
    must be dropped, while the still-plausible pairs are kept."""
    text = """Invoice #
PO #
Due Date
Fed Ground
PO-60001
06/20/26
"""
    header = _header_from_column_layout(text)
    assert "Invoice No" not in header
    assert header["PO Number"] == "PO-60001"
    assert header["Due Date"] == "06/20/26"
