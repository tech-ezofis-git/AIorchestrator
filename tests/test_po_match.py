from app.ap_skills.po_match import build_po_row


def test_build_po_row_keeps_po_only_fields():
    invoice = {
        "invoice_number": "INV-2026-3101",
        "po_number": "PO-31001",
        "vendor": "Nexus Industrial Solutions Ltd",
        "total": 1582,
    }
    po = {
        "po_number": "PO-31001",
        "vendor": "Nexus Industrial Solutions Ltd.",
        "total": 1582,
        "currency": "CAD",
        "terms": "31 Days",
        "buyer": "Purchasing Team",
        "supplier_address": "410 Commerce Park Drive",
        "ship_to_address": "955 Industrial Boulevard",
        "po_date": "2026-05-20",
        "lines": [
            {"id": "1", "description": "Network Edge Router", "qty": 2, "price": 250, "amount": 500}
        ],
    }
    row = build_po_row(invoice, po)
    assert row["Currency"] == "CAD"
    assert row["Terms"] == "31 Days"
    assert row["Buyer"] == "Purchasing Team"
    assert row["Supplier Address"] == "410 Commerce Park Drive"
    assert row["Ship To Address"] == "955 Industrial Boulevard"
    assert row["PO Date"] == "2026-05-20"
    assert str(row["PO Amount"]) == "1582"
    assert "Vendor" not in row
    assert row["PO Line Item Mapped"][0]["Description"] == "Network Edge Router"


def test_build_po_row_skips_fields_already_on_invoice():
    invoice = {
        "currency": "USD",
        "terms": "Net 15",
        "buyer": "Ada",
        "supplier_address": "Invoice addr",
        "PO Amount": "99",
    }
    po = {
        "currency": "CAD",
        "terms": "31 Days",
        "buyer": "Purchasing Team",
        "supplier_address": "PO addr",
        "total": 500,
    }
    assert build_po_row(invoice, po) == {}


def test_build_po_row_skips_mock_po():
    assert build_po_row({}, {"vendor": "ACME Supplies", "mock": True, "terms": "Net 30"}) == {}
