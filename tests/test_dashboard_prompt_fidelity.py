"""Tests for dashboard prompt fidelity and feature preservation."""
from __future__ import annotations

import pytest

from app.dashboard.propose import (
    _normalize_charts,
    _normalize_kpis,
    _should_enrich_kpis,
    overlay_layout_from_message,
    resolve_column,
    wants_kpis_only,
)
from app.dashboard.widgets import hydrate_from_spec, repair_live_spec


def test_resolve_column_aliases_and_substring():
    columns = ["Id", "InvoiceAmount", "Supplier", "PaymentDueDate", "Status", "FileName"]

    # Exact and normalized
    assert resolve_column(columns, "InvoiceAmount") == "InvoiceAmount"
    assert resolve_column(columns, "invoiceamount") == "InvoiceAmount"

    # Common semantic aliases
    assert resolve_column(columns, "amount") == "InvoiceAmount"
    assert resolve_column(columns, "total") == "InvoiceAmount"
    assert resolve_column(columns, "vendor") == "Supplier"
    assert resolve_column(columns, "supplier_name") == "Supplier"
    assert resolve_column(columns, "due") == "PaymentDueDate"
    assert resolve_column(columns, "duedate") == "PaymentDueDate"

    # Substring matching
    assert resolve_column(columns, "file") == "FileName"

    # Case-insensitive fuzzy
    assert resolve_column(columns, "invoic_amount") == "InvoiceAmount"


def test_normalize_kpis_synonyms_and_min_max():
    columns = ["Id", "InvoiceAmount", "Supplier", "PaymentDueDate"]

    raw_kpis = [
        {"id": "kpi_total", "label": "Total Spend", "agg": "total", "column": "amount"},
        {"id": "kpi_avg", "label": "Average Spend", "agg": "average", "column": "amount"},
        {"id": "kpi_min", "label": "Minimum Amount", "agg": "min", "column": "amount"},
        {"id": "kpi_max", "label": "Maximum Amount", "agg": "max", "column": "amount"},
        {"id": "kpi_vendors", "label": "Vendors", "agg": "unique", "column": "vendor"},
    ]

    normalized = _normalize_kpis(raw_kpis, columns)
    assert len(normalized) == 5

    by_id = {k["id"]: k for k in normalized}
    assert by_id["kpi_total"]["agg"] == "sum"
    assert by_id["kpi_total"]["columns"]["value"] == "InvoiceAmount"

    assert by_id["kpi_avg"]["agg"] == "avg"
    assert by_id["kpi_min"]["agg"] == "min"
    assert by_id["kpi_max"]["agg"] == "max"
    assert by_id["kpi_vendors"]["agg"] == "distinct"
    assert by_id["kpi_vendors"]["columns"]["value"] == "Supplier"


def test_normalize_charts_synonyms_and_filename_group():
    columns = ["Id", "InvoiceAmount", "FileName", "Status"]

    raw_charts = [
        {"id": "chart_bar", "title": "By File", "type": "bar_chart", "group": "filename", "column": "amount"},
        {"id": "chart_pie", "title": "By Status", "type": "pie_chart", "group": "status", "agg": "count"},
    ]

    normalized = _normalize_charts(raw_charts, columns)
    assert len(normalized) == 2

    by_id = {c["id"]: c for c in normalized}
    assert by_id["chart_bar"]["type"] == "bar"
    assert by_id["chart_bar"]["columns"]["group"] == "FileName"
    assert by_id["chart_bar"]["columns"]["value"] == "InvoiceAmount"

    assert by_id["chart_pie"]["type"] == "pie"
    assert by_id["chart_pie"]["columns"]["group"] == "Status"


def test_should_enrich_kpis_respects_specific_user_asks():
    kpis = [
        {"id": "spend", "label": "Total Spend", "agg": "sum"},
        {"id": "count", "label": "Orders", "agg": "count"},
    ]

    # Specific count or explicit instruction should NOT enrich with filler
    assert not _should_enrich_kpis("only 2 KPIs: total spend and orders", kpis)
    assert not _should_enrich_kpis("Show two KPIs only", kpis)
    assert not _should_enrich_kpis("total spend and order count dashboard", kpis)

    # Broad/generic dashboard ask CAN enrich
    assert _should_enrich_kpis("build a dashboard", kpis)
    assert _should_enrich_kpis("overview dashboard", kpis)
    assert _should_enrich_kpis("", kpis)


def test_overlay_layout_applies_user_requested_chart_type():
    kpis = [{"id": "k1", "position": "top"}]
    charts = [{"id": "c1", "type": "donut", "columns": {"group": "Supplier"}}]

    overlay_layout_from_message("show me a bar chart of spending by supplier", kpis, charts)
    assert charts[0]["type"] == "bar"

    overlay_layout_from_message("show charts on top", kpis, charts)
    assert kpis[0]["position"] == "bottom"
    assert charts[0]["position"] == "top"


def test_hydrate_from_spec_supports_min_and_max():
    rows = [
        {"InvoiceAmount": 100.0, "Supplier": "Vendor A"},
        {"InvoiceAmount": 25.0, "Supplier": "Vendor B"},
        {"InvoiceAmount": 250.0, "Supplier": "Vendor A"},
    ]
    kpis = [
        {"id": "min_val", "agg": "min", "columns": {"value": "InvoiceAmount"}},
        {"id": "max_val", "agg": "max", "columns": {"value": "InvoiceAmount"}},
    ]
    charts = [
        {"id": "min_by_vendor", "agg": "min", "type": "bar", "columns": {"group": "Supplier", "value": "InvoiceAmount"}},
        {"id": "max_by_vendor", "agg": "max", "type": "bar", "columns": {"group": "Supplier", "value": "InvoiceAmount"}},
    ]

    data = hydrate_from_spec(rows=rows, kpis=kpis, charts=charts)
    assert data["kpis"]["min_val"]["value"] == 25.0
    assert data["max_val"]["value"] == 250.0 if "max_val" in data else data["kpis"]["max_val"]["value"] == 250.0


def test_dynamic_quantity_preserves_ten_kpis_and_charts():
    columns = ["Id", "Status", "Amount", "Supplier", "DueDate"]
    raw_kpis = [{"id": f"kpi_{i}", "label": f"KPI {i}", "agg": "count"} for i in range(10)]
    raw_charts = [{"id": f"chart_{i}", "title": f"Chart {i}", "type": "column", "group": "Status"} for i in range(10)]

    normalized_kpis = _normalize_kpis(raw_kpis, columns)
    normalized_charts = _normalize_charts(raw_charts, columns)

    # Must preserve all 10 without truncating at 8
    assert len(normalized_kpis) == 10
    assert len(normalized_charts) == 10


def test_render_dashboard_html_new_structure():
    from app.dashboard.render import render_dashboard_html

    dashboard = {
        "repository_name": "RFQ Process — FTL Distribution",
        "message": "RFQ Lifecycle Dashboard",
        "kpis": [
            {"id": "total_rfqs", "label": "Total RFQs", "value": 1420},
            {"id": "new_rfqs", "label": "New RFQs", "value": 112},
            {"id": "in_progress", "label": "In Progress", "value": 340},
            {"id": "won_rfqs", "label": "Won RFQs", "value": 520},
        ],
        "charts": [
            {"id": "status_overview", "title": "RFQ Status Overview", "type": "bar"},
            {"id": "pipeline_funnel", "title": "RFQ Pipeline / Funnel", "type": "funnel"},
        ],
        "insights": ["Conversion rate improved by 14% this month."],
        "data": {
            "kpis": {
                "total_rfqs": {"value": 1420, "trend": "+12%"},
                "won_rfqs": {"value": 520, "trend": "+18%"},
            },
            "charts": {
                "status_overview": {"categories": ["Won", "Lost"], "values": [520, 80]},
            },
        },
    }
    html = render_dashboard_html(dashboard, message="RFQ Process")
    assert html.lstrip().startswith("<style>")
    assert "ez-dash" in html
    assert "RFQ Process — FTL Distribution" in html
    assert "Total RFQs" in html
    assert "Won RFQs" in html
    assert "command" in html
    assert "register" in html
    assert "side" in html
    assert "exportCSV" in html

