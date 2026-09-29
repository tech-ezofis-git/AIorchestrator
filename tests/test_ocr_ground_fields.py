"""Null OCR fields are filled only from labels present in the OCR text."""
from app.ocr_skills.ground_fields import fill_null_fields

_BRAND_INVOICE = """Giggling 
Platypus co.
INVOICE
Invoice Number: #1234
Date: june 13, 2021
BILL TO:
Payment Method
Term and Conditions:
Samira Hadid
Due Date: June 16, 2021
123 Anywhere st., Any City
ITEM DESRIPTION
Branding Design
Total 
$4800
Tax
Sub Total
$1000
"""


def _nulls(*names: tuple[str, str]) -> list[dict]:
    return [{"name": name, "value": None, "type": typ} for name, typ in names]


def test_brand_invoice_fills_labeled_fields_and_leaves_missing_ones_null():
    fields = fill_null_fields(
        _nulls(
            ("Supplier", "SHORT_TEXT"),
            ("DocumentType", "SHORT_TEXT"),
            ("PONumber", "SHORT_TEXT"),
            ("InvoiceNo", "SHORT_TEXT"),
            ("InvoiceDate", "DATE"),
            ("InvoiceAmount", "SHORT_TEXT"),
            ("Currency", "SHORT_TEXT"),
            ("DueDate", "DATE"),
            ("MatchedStatus", "SHORT_TEXT"),
        ),
        _BRAND_INVOICE,
    )
    by_name = {row["name"]: row["value"] for row in fields}
    assert by_name["Supplier"] == "Giggling Platypus co."
    assert by_name["DocumentType"] == "INVOICE"
    assert by_name["InvoiceNo"] == "#1234"
    assert by_name["InvoiceDate"] == "2021-06-13"
    assert by_name["DueDate"] == "2021-06-16"
    assert by_name["InvoiceAmount"] == "$4800"
    assert by_name["PONumber"] is None
    assert by_name["Currency"] is None
    assert by_name["MatchedStatus"] is None


def test_grounding_does_not_replace_a_model_value():
    fields = fill_null_fields(
        [{"name": "InvoiceNo", "value": "KEEP", "type": "SHORT_TEXT"}],
        _BRAND_INVOICE,
    )
    assert fields[0]["value"] == "KEEP"
