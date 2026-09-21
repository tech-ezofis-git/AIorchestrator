"""Widget unit tests — same contract as the orchestrator dashboard agent."""
from datetime import date

import pytest

from app.store import quote_ident, split_table
from app.widgets import (
    fields_from_extract,
    hydrate_data,
    hydrate_from_spec,
    overlay_extract_artifacts,
    propose_widgets,
)


def test_quote_ident_brackets():
    assert quote_ident("InvoiceAmount") == "[InvoiceAmount]"


def test_quote_ident_unicode_column():
    assert quote_ident("نوعالمستند") == "[نوعالمستند]"


def test_quote_ident_rejects_injection():
    with pytest.raises(ValueError):
        quote_ident("Id]; DROP TABLE x;--")
    with pytest.raises(ValueError):
        quote_ident("")


def test_split_table_sqlserver_fallback():
    schema, table = split_table("", "38b1b6dd-854b-489f-aa44-ac6d4dd691e8")
    assert schema == "repository"
    assert table == "Items_38b1b6dd"


def test_split_table_qualified():
    schema, table = split_table("repository.Items_38b1b6dd", "x")
    assert schema == "repository"
    assert table == "Items_38b1b6dd"


def test_propose_widgets_from_ap_columns():
    kpis, charts, bound = propose_widgets(
        ["Supplier", "InvoiceAmount", "DueDate", "MatchedStatus", "InvoiceDate", "Currency", "Status"]
    )
    kpi_ids = [row["id"] for row in kpis]
    chart_ids = [row["id"] for row in charts]
    assert kpi_ids == [
        "total_ap",
        "overdue",
        "open_invoices",
        "average_invoice",
        "overdue_count",
        "overdue_pct",
        "current_ap",
        "due_in_30",
        "supplier_count",
        "unmatched_count",
    ]
    assert chart_ids == [
        "supplier_risk",
        "match_status",
        "profit_vs_ap",
        "ap_aging",
        "top_suppliers",
        "overdue_by_supplier",
        "invoices_by_status",
        "invoice_count_by_month",
        "currency_mix",
        "matched_vs_unmatched",
    ]
    assert "dpo" not in kpi_ids
    assert bound["amount"] == "InvoiceAmount"
    assert bound["supplier"] == "Supplier"


def test_hydrate_new_kpis_and_aging():
    kpis = [
        {"id": "average_invoice", "enabled": True},
        {"id": "overdue_count", "enabled": True},
        {"id": "overdue_pct", "enabled": True},
        {"id": "current_ap", "enabled": True},
        {"id": "due_in_30", "enabled": True},
        {"id": "supplier_count", "enabled": True},
        {"id": "unmatched_count", "enabled": True},
    ]
    charts = [
        {"id": "ap_aging", "enabled": True},
        {"id": "matched_vs_unmatched", "enabled": True},
        {"id": "top_suppliers", "enabled": True},
    ]
    bound = {
        "amount": "InvoiceAmount",
        "due": "DueDate",
        "supplier": "Supplier",
        "match": "MatchedStatus",
    }
    data = hydrate_data(
        rows=[
            {
                "InvoiceAmount": "6200",
                "DueDate": date(2020, 1, 1),
                "Supplier": "Acme",
                "MatchedStatus": "Not Matched",
            },
            {
                "InvoiceAmount": "5300",
                "DueDate": date(2026, 9, 10),
                "Supplier": "Beta",
                "MatchedStatus": "Matched",
            },
        ],
        bound=bound,
        kpis=kpis,
        charts=charts,
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["average_invoice"]["value"] == 5750
    assert data["kpis"]["overdue_count"]["value"] == 1
    assert data["kpis"]["overdue_pct"]["value"] == 53.9
    assert data["kpis"]["current_ap"]["value"] == 5300
    assert data["kpis"]["due_in_30"]["value"] == 5300
    assert data["kpis"]["supplier_count"]["value"] == 2
    assert data["kpis"]["unmatched_count"]["value"] == 1
    assert data["charts"]["ap_aging"]["type"] == "heatmap"
    assert data["charts"]["ap_aging"]["bars"][0] == 5300
    assert data["charts"]["ap_aging"]["bars"][-1] == 6200
    assert data["charts"]["matched_vs_unmatched"]["type"] == "gauge"
    assert data["charts"]["matched_vs_unmatched"]["series"][0]["name"] == "Matched"


def test_hydrate_sums_overdue_and_supplier():
    kpis = [
        {"id": "total_ap", "enabled": True},
        {"id": "overdue", "enabled": True},
        {"id": "open_invoices", "enabled": True},
    ]
    charts = [{"id": "supplier_risk", "enabled": True}]
    bound = {"amount": "InvoiceAmount", "due": "DueDate", "supplier": "Supplier"}
    data = hydrate_data(
        rows=[
            {"InvoiceAmount": "6200", "DueDate": date(2020, 1, 1), "Supplier": "Acme"},
            {"InvoiceAmount": "5300", "DueDate": date(2020, 1, 1), "Supplier": "Acme"},
        ],
        bound=bound,
        kpis=kpis,
        charts=charts,
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["total_ap"]["value"] == 11500
    assert data["kpis"]["overdue"]["value"] == 11500
    assert data["kpis"]["open_invoices"]["value"] == 2
    assert data["charts"]["supplier_risk"]["type"] == "radar"
    assert data["charts"]["supplier_risk"]["series"][0]["name"] == "Acme"


def test_extract_fields_from_ocr_invoice_total():
    fields = fields_from_extract(
        {
            "invoice": {"total": None, "vendor": "", "due_date": "", "currency": "USD"},
            "ocr_text": (
                "APEX INDUSTRIAL COMPONENTS LTD\n"
                "Due Date\nINV-2026-6001\n05/20/26\n06/20/26\n"
                "Invoice Total\n5203.65\n"
            ),
        }
    )
    assert fields["amount"] == 5203.65
    assert "APEX" in (fields["supplier"] or "")
    assert fields["due"] == date(2026, 6, 20)
    assert fields["invoice_date"] == date(2026, 5, 20)


def test_overlay_fills_empty_item_columns():
    rows, used = overlay_extract_artifacts(
        rows=[{"id": "item-1", "InvoiceAmount": None, "Supplier": None, "DueDate": None}],
        bound={"amount": "InvoiceAmount", "supplier": "Supplier", "due": "DueDate"},
        artifacts_by_item={
            "item-1": {
                "extract_invoice": {
                    "invoice": {"total": None, "vendor": ""},
                    "ocr_text": "Vendor: Acme Ltd\nInvoice Total 1200.00\n",
                }
            }
        },
    )
    assert used is True
    assert rows[0]["InvoiceAmount"] == 1200.0
    assert rows[0]["Supplier"] == "Acme Ltd"


def test_hydrate_from_spec_sum_and_group():
    data = hydrate_from_spec(
        rows=[
            {"Department": "Ops", "FileSize": 1200, "DueDate": date(2020, 1, 1)},
            {"Department": "Ops", "FileSize": 800, "DueDate": date(2020, 1, 1)},
            {"Department": "HR", "FileSize": 400, "DueDate": date(2026, 9, 1)},
        ],
        kpis=[
            {"id": "record_count", "enabled": True, "agg": "count", "columns": {}},
            {"id": "total_value", "enabled": True, "agg": "sum", "columns": {"value": "FileSize"}},
            {
                "id": "overdue",
                "enabled": True,
                "agg": "overdue_sum",
                "columns": {"value": "FileSize", "date": "DueDate"},
            },
        ],
        charts=[
            {
                "id": "by_dept",
                "enabled": True,
                "type": "donut",
                "agg": "count",
                "columns": {"group": "Department"},
            }
        ],
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["record_count"]["value"] == 3
    assert data["kpis"]["total_value"]["value"] == 2400
    assert data["kpis"]["overdue"]["value"] == 2000
    series = {item["name"]: item["value"] for item in data["charts"]["by_dept"]["series"]}
    assert series["Ops"] == 2
    assert series["HR"] == 1


def test_attach_kpi_trends_skips_tiny_samples():
    from app.widgets import attach_kpi_trends, hydrate_from_spec

    kpis = [{"id": "record_count", "enabled": True, "agg": "count", "columns": {}}]
    rows = [
        {"Id": "1", "InvoiceDate": date(2026, 7, 10)},
        {"Id": "2", "InvoiceDate": date(2026, 7, 20)},
        {"Id": "3", "InvoiceDate": date(2026, 8, 5)},
    ]
    data = hydrate_from_spec(rows=rows, kpis=kpis, charts=[], today=date(2026, 8, 28))
    attach_kpi_trends(rows=rows, kpis=kpis, kpi_data=data["kpis"], today=date(2026, 8, 28))
    assert "trend_pct" not in data["kpis"]["record_count"]


def test_attach_kpi_trends_compares_previous_month():
    from app.widgets import attach_kpi_trends, hydrate_from_spec

    kpis = [{"id": "record_count", "enabled": True, "agg": "count", "columns": {}}]
    rows = [{"Id": str(index), "InvoiceDate": date(2026, 7, 10)} for index in range(6)]
    rows += [{"Id": str(index + 6), "InvoiceDate": date(2026, 8, 5)} for index in range(4)]
    data = hydrate_from_spec(rows=rows, kpis=kpis, charts=[], today=date(2026, 8, 28))
    attach_kpi_trends(rows=rows, kpis=kpis, kpi_data=data["kpis"], today=date(2026, 8, 28))
    assert data["kpis"]["record_count"]["trend_pct"] == -33.3
    assert "vs last month" in data["kpis"]["record_count"]["subtext"]


def test_hydrate_status_chart_uses_matched_status_when_status_empty():
    data = hydrate_from_spec(
        rows=[
            {"Status": None, "MatchedStatus": "Matched", "InvoiceAmount": 100},
            {"Status": "", "MatchedStatus": "Not Matched", "InvoiceAmount": 50},
            {"Status": None, "MatchedStatus": "Matched", "InvoiceAmount": 25},
        ],
        kpis=[],
        charts=[
            {
                "id": "invoice_status",
                "title": "Invoice Status",
                "enabled": True,
                "type": "donut",
                "agg": "count",
                "columns": {"group": "Status"},
            },
            {
                "id": "payment_status",
                "title": "Payment Status",
                "enabled": True,
                "type": "donut",
                "agg": "count",
                "columns": {"group": "Status"},
            },
        ],
    )
    invoice = {item["name"]: item["value"] for item in data["charts"]["invoice_status"]["series"]}
    payment = {item["name"]: item["value"] for item in data["charts"]["payment_status"]["series"]}
    assert invoice == {"Matched": 2, "Not Matched": 1}
    assert payment == {"Matched": 2, "Not Matched": 1}
    assert "Unknown" not in invoice
    assert "Unknown" not in payment


def test_hydrate_does_not_invent_unknown_when_group_is_empty():
    data = hydrate_from_spec(
        rows=[{"Status": None}, {"Status": ""}],
        kpis=[],
        charts=[
            {
                "id": "by_status",
                "title": "By status",
                "enabled": True,
                "type": "donut",
                "agg": "count",
                "columns": {"group": "Status"},
            }
        ],
    )
    series = data["charts"]["by_status"].get("series") or []
    assert series == []


def test_hydrate_paid_versus_outstanding():
    data = hydrate_from_spec(
        rows=[
            {
                "InvoiceAmount": 100,
                "MatchedStatus": "Matched",
                "DueDate": date(2020, 1, 1),
                "Supplier": "Acme",
            },
            {
                "InvoiceAmount": 40,
                "MatchedStatus": "Not Matched",
                "DueDate": date(2020, 1, 1),
                "Supplier": "Beta",
            },
            {
                "InvoiceAmount": 10,
                "MatchedStatus": "Partially Matched",
                "DueDate": date(2026, 9, 1),
                "Supplier": "Acme",
            },
            {
                "InvoiceAmount": 50,
                "MatchedStatus": "Approved",
                "DueDate": date(2026, 9, 1),
                "Supplier": "Acme",
            },
        ],
        kpis=[
            {
                "id": "paid_amount",
                "enabled": True,
                "agg": "paid_sum",
                "columns": {"value": "InvoiceAmount", "match": "MatchedStatus"},
            },
            {
                "id": "outstanding_amount",
                "enabled": True,
                "agg": "outstanding_sum",
                "columns": {"value": "InvoiceAmount", "match": "MatchedStatus"},
            },
            {
                "id": "overdue_amount",
                "enabled": True,
                "agg": "overdue_sum",
                "columns": {"value": "InvoiceAmount", "date": "DueDate", "match": "MatchedStatus"},
            },
        ],
        charts=[
            {
                "id": "payment_status",
                "enabled": True,
                "type": "donut",
                "agg": "sum",
                "grain": "payment",
                "columns": {"group": "MatchedStatus", "value": "InvoiceAmount", "match": "MatchedStatus"},
            },
            {
                "id": "vendor_outstanding",
                "enabled": True,
                "type": "lollipop",
                "agg": "outstanding_sum",
                "columns": {"group": "Supplier", "value": "InvoiceAmount", "match": "MatchedStatus"},
            },
        ],
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["paid_amount"]["value"] == 150
    assert data["kpis"]["outstanding_amount"]["value"] == 50
    assert data["kpis"]["overdue_amount"]["value"] == 40
    payment = {item["name"]: item["value"] for item in data["charts"]["payment_status"]["series"]}
    assert payment == {"Paid": 150, "Outstanding": 50}
    vendor_chart = data["charts"]["vendor_outstanding"]
    vendors = dict(zip(vendor_chart["categories"], vendor_chart["bars"]))
    assert vendors["Beta"] == 40
    assert vendors["Acme"] == 10


def test_overdue_reads_matched_status_from_row_when_spec_omits_it():
    data = hydrate_from_spec(
        rows=[
            {"InvoiceAmount": 100, "MatchedStatus": "Matched", "DueDate": date(2020, 1, 1)},
            {"InvoiceAmount": 40, "MatchedStatus": "Not Matched", "DueDate": date(2020, 1, 1)},
        ],
        kpis=[
            {
                "id": "overdue_amount",
                "enabled": True,
                "agg": "overdue_sum",
                "columns": {"value": "InvoiceAmount", "date": "DueDate"},
            }
        ],
        charts=[],
        today=date(2026, 8, 28),
    )
    assert data["kpis"]["overdue_amount"]["value"] == 40


def test_repair_replaces_fake_aging_kpis_with_due_date_chart():
    from app.widgets import repair_live_spec

    kpis = [
        {
            "id": "d30",
            "label": "Invoices 1-30 Days Overdue",
            "agg": "overdue_sum",
            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
        },
        {
            "id": "d60",
            "label": "Invoices 31-60 Days Overdue",
            "agg": "overdue_sum",
            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
        },
        {
            "id": "overdue_amount",
            "label": "Overdue Amount",
            "agg": "overdue_sum",
            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
        },
    ]
    charts = [
        {
            "id": "payment_status",
            "title": "Payment Status",
            "type": "donut",
            "agg": "count",
            "columns": {"group": "Status"},
        }
    ]
    repair_live_spec(
        kpis,
        charts,
        ["InvoiceAmount", "DueDate", "MatchedStatus", "Status", "Supplier"],
    )
    assert [item["id"] for item in kpis] == ["overdue_amount"]
    assert kpis[0]["columns"]["match"] == "MatchedStatus"
    assert charts[0]["grain"] == "payment"
    aging = next(item for item in charts if item.get("grain") == "aging")
    assert aging["columns"]["group"] == "DueDate"
    data = hydrate_from_spec(
        rows=[
            {"InvoiceAmount": 10, "DueDate": date(2026, 8, 20), "MatchedStatus": "Not Matched"},
            {"InvoiceAmount": 90, "DueDate": date(2026, 1, 1), "MatchedStatus": "Not Matched"},
            {"InvoiceAmount": 50, "DueDate": date(2026, 1, 1), "MatchedStatus": "Matched"},
        ],
        kpis=kpis,
        charts=[aging],
        today=date(2026, 8, 28),
    )
    chart = data["charts"][aging["id"]]
    buckets = dict(zip(chart["categories"], chart["bars"]))
    assert buckets["1-30"] == 10
    assert buckets["90+"] == 90
    assert buckets["31-60"] == 0
    assert data["kpis"]["overdue_amount"]["value"] == 100


def test_repair_forces_supplier_radar_to_outstanding_amounts():
    from app.widgets import repair_live_spec

    kpis: list = []
    charts = [
        {
            "id": "supplier_risk_assessment",
            "title": "Supplier Risk Assessment",
            "type": "column",
            "agg": "sum",
            "columns": {"group": "Supplier", "value": "InvoiceAmount"},
        }
    ]
    repair_live_spec(
        kpis,
        charts,
        ["InvoiceAmount", "DueDate", "MatchedStatus", "Supplier"],
    )
    assert charts[0]["type"] == "radar"
    assert charts[0]["agg"] == "outstanding_sum"
    assert charts[0]["columns"]["match"] == "MatchedStatus"
    data = hydrate_from_spec(
        rows=[
            {"Supplier": "Starlight", "InvoiceAmount": 100, "MatchedStatus": "Not Matched"},
            {"Supplier": "Pioneer", "InvoiceAmount": 50, "MatchedStatus": "Matched"},
        ],
        kpis=[],
        charts=charts,
        today=date(2026, 8, 28),
    )
    series = {item["name"]: item["value"] for item in data["charts"]["supplier_risk_assessment"]["series"]}
    assert series["Starlight"] == 100
    assert "Pioneer" not in series
