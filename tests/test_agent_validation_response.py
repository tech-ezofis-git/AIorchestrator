"""Tests for apagentv6-shaped AIAGENTResponse builder."""
from app.ap_skills.agent_validation_response import build_aiagent_response


def test_aiagent_response_matches_apagent_shape():
    invoice = {
        "invoice_number": "INV-2026-3101",
        "po_number": "PO-31001",
        "vendor": "Nexus Industrial Solutions Ltd",
        "currency": "CAD",
        "terms": "Net One Month",
        "invoice_date": "2026-07-20",
        "total": 1582.0,
        "tax": 182.0,
        "buyer": "Atlas Manufacturing Solutions Ltd.",
        "vendor_address": "720 Enterprise Boulevard Mississauga, ON L5T 3M8 Canada",
        "ship_to_address": "725 Industrial Parkway Burlington, ON L7L 6B3 Canada",
        "line_items": [
            {
                "line_no": 1,
                "item_no": "NET-RTR-220",
                "description": "Network Edge Router",
                "quantity": "2",
                "uom": "EA",
                "rate": "250.00",
                "line_amount": "500.00",
            },
            {
                "line_no": 2,
                "item_no": "NET-SW-140",
                "description": "Managed Network Switch",
                "quantity": None,
                "uom": "EA",
                "rate": "300.00",
                "line_amount": "900.00",
            },
        ],
    }
    po = {
        "po_number": "PO-31001",
        "vendor": "Nexus Industrial Solutions Ltd.",
        "supplier_address": "720 Enterprise Boulevard, Mississauga, ON L5T 3M8, Canada",
        "ship_to_address": "Atlas Manufacturing Solutions Ltd., Warehouse Receiving Dock, 725 Industrial Parkway, Burlington, ON L7L 6B3, Canada",
        "po_date": "2026-06-30",
        "terms": "Net One Month",
        "buyer": "SURJIT",
        "total": 1582,
        "currency": "CAD",
        "itemId": 7,
        "lines": [
            {
                "id": "1",
                "description": "Network Edge Router",
                "qty": 2,
                "price": 250.0,
                "amount": 500,
                "Part Number": "NET-RTR-220",
                "UOM": "EA",
            },
            {
                "id": "2",
                "description": "Managed Network Switch",
                "qty": 3,
                "price": 300.0,
                "amount": 900,
                "Part Number": "NET-SW-140",
                "UOM": "EA",
            },
        ],
    }
    artifacts = {
        "po_match": {
            "decision": "MATCHED",
            "score": 98.0,
            "reason": "PO found. Vendor matches PO. Totals match within tolerance.",
            "po": po,
            "po_row": {"Terms": "Net One Month", "Buyer": "SURJIT"},
        },
        "po_lookup_sap": {"source": "sap", "po": {"source": "sap_sample"}},
        "vendor_validate": {
            "status": "ACTIVE",
            "vendor": "Nexus Industrial Solutions Ltd",
            "expected": "Nexus Industrial Solutions Ltd.",
            "reason": "Invoice vendor matches PO vendor.",
        },
        "backorder_detect": {
            "detected": False,
            "missing_qty_by_item": [],
            "recommendation": "NO_ACTION",
        },
        "duplicate_detect": {"is_duplicate_invoice": False},
    }

    out = build_aiagent_response(
        decision="MATCHED",
        reason="",
        artifacts=artifacts,
        invoice=invoice,
        document_job={"resource": "SAP"},
    )

    assert out["decision"] == "Matched"
    assert out["score"] == 98.0
    assert "approve for posting" in out["ai_insight"].lower()
    assert "overall matching score" in out["reason"].lower()
    assert out["source_type"] == "SAP"

    fields = out["debug"]["Side-by-side Field Matching"]
    assert any(r["Field"] == "Supplier" and r["Score"] >= 85 for r in fields)
    assert any(r["Field"] == "PO Number" and r["Score"] == 100 for r in fields)
    assert any(r["Field"] == "Total Due" and r["Score"] == 100 for r in fields)

    lines = out["debug"]["Side-by-side Line Item matching"]
    assert len(lines) == 2
    assert lines[0]["Line Score"] == 100
    assert lines[1]["Quantity"]["Score"] == 0  # invoice qty missing vs PO 3

    assert out["po_row"]["PO Number"] == "PO-31001"
    assert out["po_row"]["Supplier"].startswith("Nexus")
    assert out["po_row"]["PO Line Item Mapped"]
    assert out["po_row"]["PO Line Item Mapped"][0]["Material Description"] == "Network Edge Router"
    assert out["po_row"]["PO Line Item Mapped"][0]["description"] == "Network Edge Router"

    assert out["payment_terms"]["raw"] == "Net One Month"
    assert out["payment_terms"]["normalized"]["net_days"] == 30
    assert out["payment_terms"]["due_date"] == "2026-08-19"

    assert out["supplier_validation"]["status"] == "ACTIVE"
    assert out["invoice_errors"]["severity"] == "NONE"
    assert out["back_order"]["detected"] is False
    assert out["Extracted Invoice JSON"]["invoice_header"]["Invoice No"] == "INV-2026-3101"
    assert len(out["Extracted Invoice JSON"]["Line Item"]) == 2
    assert out["matter_validation"]["status"] == "NOT_PRESENT"


def test_aiagent_po_row_includes_hana_sap_master_columns():
    po = {
        "po_number": "4500069456",
        "vendor": "EV Parts Inc.",
        "supplier_id": "USSU-VSF01",
        "currency": "USD",
        "po_date": "2026-09-11",
        "total": 368.94,
        "source": "hana_cloud",
        "match_items": [
            {
                "itemNumber": 10,
                "itemCategory": "Standard",
                "materialId": "MZ-RM-R100-02",
                "materialDescription": "BKR-100 Handle Bars",
                "materialGroup": "ZHANDLE",
                "plant": "1710",
                "orderQuantity": 129,
                "unitOfMeasure": "PC",
                "netPrice": 2.86,
                "priceUnit": 1,
                "netValue": 368.94,
            }
        ],
    }
    out = build_aiagent_response(
        decision="MATCHED",
        reason="PO found.",
        artifacts={
            "po_match": {"decision": "MATCHED", "score": 100, "po": po},
            "po_lookup_sap": {"source": "hana", "po": po},
        },
        invoice={
            "invoice_number": "56700989",
            "po_number": "4500069456",
            "vendor": "EV Parts Inc.",
            "total": 368.94,
            "currency": "USD",
        },
        document_job={"resource": "HANA"},
    )
    row = out["po_row"]
    assert row["PO Number"] == "4500069456"
    assert row["Supplier"] == "EV Parts Inc."
    assert row["Supplier Id"] == "USSU-VSF01"
    assert row["PO Date"] == "2026-09-11"
    assert row["Currency"] == "USD"
    assert row["PO Amount"] == 368.94
    line = row["PO Line Item Mapped"][0]
    assert line["Item Number"] == "10"
    assert line["Item Category"] == "Standard"
    assert line["Material Id"] == "MZ-RM-R100-02"
    assert line["Material Description"] == "BKR-100 Handle Bars"
    assert line["Material Group"] == "ZHANDLE"
    assert line["Plant"] == "1710"
    assert line["Order Quantity"] == 129
    assert line["Unit of Measure"] == "PC"
    assert line["Net Price"] == 2.86
    assert line["Price Unit"] == 1
    assert line["Net Value"] == 368.94
    assert line["Part Number"] == "MZ-RM-R100-02"


def test_aiagent_response_vendor_mismatch_debug():
    out = build_aiagent_response(
        decision="NOT_MATCHED",
        reason="PO found. Vendor differs.",
        artifacts={
            "po_match": {
                "score": 78,
                "po": {"po_number": "4500066847", "vendor": "Steel & More Inc.", "total": 100, "source": "hana_cloud"},
                "reason": "PO found. Vendor differs.",
            },
            "po_lookup_sap": {"source": "hana", "po": {"source": "hana_cloud"}},
            "vendor_validate": {
                "status": "MISMATCH",
                "vendor": "Velotics Inc.",
                "expected": "Steel & More Inc.",
                "reason": "Invoice vendor 'Velotics Inc.' does not match PO vendor 'Steel & More Inc.'.",
            },
        },
        invoice={
            "invoice_number": "5105665738/2026",
            "po_number": "4500066847",
            "vendor": "Velotics Inc.",
            "total": 100,
            "currency": "USD",
            "line_items": [{"description": "Item", "qty": 1, "price": 100, "amount": 100}],
        },
    )
    assert out["decision"] == "Not Matched"
    assert out["source_type"] == "HANA Cloud"
    assert out["supplier_validation"]["mismatch"]
    assert out["debug"]["Side-by-side Field Matching"]
