"""Tests for payment terms + due-date derivation."""
from app.ap_skills.extract_invoice import _as_invoice, _heuristic_from_text
from app.ap_skills.payment_terms import due_date_from_terms, normalize_payment_terms


def test_normalize_net_one_month():
    assert normalize_payment_terms("Net One Month") == {
        "basis": "NET",
        "net_days": 30,
        "discount": None,
    }


def test_due_date_from_net_30():
    assert due_date_from_terms(invoice_date="2026-07-20", terms="Net 30") == "2026-08-19"


def test_extract_terms_and_computes_due_date():
    text = """
Invoice #
PO #
Terms
INV-2026-3101
PO-31001
Net One Month
Invoice Date
2026-07-20
Vendor Name
Nexus Industrial Solutions Ltd
Currency
CAD
Invoice Total
1582.00
"""
    inv = _heuristic_from_text(text)
    assert inv["terms"] == "Net One Month"
    assert inv["invoice_date"] == "2026-07-20"
    assert inv["due_date"] == "2026-08-19"
    assert inv["invoice_header"]["Terms"] == "Net One Month"
    assert inv["invoice_header"]["Due Date"] == "2026-08-19"


def test_as_invoice_overwrites_due_date_from_terms():
    inv = _as_invoice(
        {
            "invoice_number": "INV-1",
            "invoice_date": "2026-07-01",
            "due_date": "2026-07-05",
            "terms": "Net 30",
            "vendor": "Acme",
            "total": 100,
        }
    )
    assert inv["terms"] == "Net 30"
    assert inv["due_date"] == "2026-07-31"
