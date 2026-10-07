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


@pytest.mark.asyncio
async def test_exact_10_kpis_and_10_charts_proposal():
    from app.dashboard.propose import propose_dashboard

    prompt = """
Create a comprehensive RFQ Operations dashboard.
Show these 10 KPI cards at the top:
1. Total RFQs
2. New RFQs
3. In Progress RFQs
4. Qualified RFQs
5. Disqualified RFQs
6. Quotes Generated
7. Quotes Sent
8. Won RFQs
9. Lost RFQs
10. Average Processing Time

Include these 10 dashboard sections:
1. RFQ Pipeline: Funnel chart showing conversion stages
2. Status Overview: Bar chart by status
3. RFQ Volume Trend: Line chart showing monthly trends
4. Disqualification Reasons: Bar chart of qualification breakdown
5. Quote Performance: Column chart of quotes by outcome
6. Processing Stage Durations: Horizontal bar chart of processing times
7. Top Customers: Bar chart of customer volume
8. Product Distribution: Donut chart of products
9. Regional Breakdown: Pie chart by region
10. User Workload: Bar chart of assigned users
"""
    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "RFQ Operations",
        "qualified_table": "test.rfq_items",
        "schema": "test",
        "table": "rfq_items",
    }
    columns = [
        "Id", "RFQNumber", "Customer", "Product", "Region",
        "Status", "QuoteAmount", "AssignedUser", "ProcessingHours", "SubmittedDate"
    ]

    proposal = await propose_dashboard(
        message=prompt,
        target=target,
        columns=columns,
        sample_rows=[],
    )

    kpis = proposal["kpis"]
    charts = proposal["charts"]

    assert len(kpis) == 10, f"Expected exactly 10 KPIs, got {len(kpis)}: {[k['label'] for k in kpis]}"
    assert len(charts) == 10, f"Expected exactly 10 Charts, got {len(charts)}: {[c['title'] for c in charts]}"


@pytest.mark.asyncio
async def test_exact_10_kpis_comma_separated():
    from app.dashboard.propose import propose_dashboard

    prompt = (
        "Create an operations dashboard with 10 KPIs: "
        "Total Orders, Pending Orders, Completed Orders, Cancelled Orders, Returned Orders, "
        "Total Revenue, Average Order Value, Customer Count, Delivery Rate, and Return Rate. "
        "Also include 10 charts: "
        "1. Order Volume Trend: Line chart\n"
        "2. Status Distribution: Donut chart\n"
        "3. Revenue by Category: Bar chart\n"
        "4. Top Products: Column chart\n"
        "5. Customer Segments: Pie chart\n"
        "6. Delivery Performance: Bar chart\n"
        "7. Return Reasons: Horizontal bar chart\n"
        "8. Regional Sales: Area chart\n"
        "9. Fulfillment Funnel: Funnel chart\n"
        "10. Agent Performance: Bar chart\n"
    )

    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "Operations",
        "qualified_table": "test.orders",
        "schema": "test",
        "table": "orders",
    }
    columns = ["Id", "Status", "Amount", "Category", "Customer", "Product", "Date", "Region"]

    proposal = await propose_dashboard(
        message=prompt,
        target=target,
        columns=columns,
        sample_rows=[],
    )

    assert len(proposal["kpis"]) == 10
    assert len(proposal["charts"]) == 10


@pytest.mark.asyncio
async def test_preserve_10_charts_with_complex_titles():
    from app.dashboard.propose import propose_dashboard

    prompt = """
Build a Logistics Operations Dashboard.
KPI cards (10 metrics to show):
1. Total Shipments
2. On-Time Deliveries
3. Delayed Shipments
4. Average Transit Days
5. Total Freight Spend
6. Active Carriers
7. Exception Rate
8. Return Volume
9. Customer CSAT
10. Carbon Footprint

Include these 10 dashboard sections:
1. Delivery Schedule Adherence: Bar chart by carrier
2. Port Berthing Schedule: Horizontal bar chart by port
3. Operational Insights: Line chart of weekly throughput
4. Sortable Carrier Performance: Column chart of on-time rates
5. Searchable Terminal Status: Donut chart of terminal utilization
6. Regional Volume Mix: Donut chart by destination
7. Freight Cost Breakdown: Area chart over time
8. Exception Reasons: Bar chart of exception codes
9. Logistics Fulfillment Pipeline: Funnel chart of milestone stages
10. Vessel Turnaround Performance: Gauge chart of port turnaround
"""
    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "Logistics",
        "qualified_table": "test.shipments",
        "schema": "test",
        "table": "shipments",
    }
    columns = ["Id", "Carrier", "Port", "Status", "Spend", "TransitDays", "ExceptionCode", "Destination", "Date"]

    proposal = await propose_dashboard(
        message=prompt,
        target=target,
        columns=columns,
        sample_rows=[],
    )

    assert len(proposal["kpis"]) == 10, f"Expected 10 KPIs, got {len(proposal['kpis'])}"
    assert len(proposal["charts"]) == 10, f"Expected 10 Charts, got {len(proposal['charts'])}"


@pytest.mark.asyncio
async def test_llm_truncation_recovery(monkeypatch):
    from unittest.mock import AsyncMock
    from app.dashboard import propose

    # Mock chat_json returning only 6 generic charts and 4 generic KPIs
    mock_llm_schema = {
        "dashboard_schema": {
            "title": "Operations Dashboard",
            "kpis": [
                {"id": "k1", "label": "Total Shipments", "type": "count"},
                {"id": "k2", "label": "Delayed Shipments", "type": "count"},
            ],
            "charts": [
                {"id": "c1", "title": "Status Distribution", "type": "donut"},
                {"id": "c2", "title": "Regional Mix", "type": "donut"},
                {"id": "c3", "title": "Volume Trend", "type": "line"},
                {"id": "c4", "title": "Carrier Performance", "type": "bar"},
                {"id": "c5", "title": "Cost Breakdown", "type": "bar"},
                {"id": "c6", "title": "Fulfillment Funnel", "type": "funnel"},
            ],
        }
    }
    monkeypatch.setattr(propose, "chat_json", AsyncMock(return_value=mock_llm_schema))

    prompt = """
Build a Logistics Operations Dashboard.
KPI cards (10 metrics to show):
1. Total Shipments
2. On-Time Deliveries
3. Delayed Shipments
4. Average Transit Days
5. Total Freight Spend
6. Active Carriers
7. Exception Rate
8. Return Volume
9. Customer CSAT
10. Carbon Footprint

Include these 10 dashboard sections:
1. Delivery Schedule Adherence: Bar chart by carrier
2. Port Berthing Schedule: Horizontal bar chart by port
3. Operational Insights: Line chart of weekly throughput
4. Sortable Carrier Performance: Column chart of on-time rates
5. Searchable Terminal Status: Donut chart of terminal utilization
6. Regional Volume Mix: Donut chart by destination
7. Freight Cost Breakdown: Area chart over time
8. Exception Reasons: Bar chart of exception codes
9. Logistics Fulfillment Pipeline: Funnel chart of milestone stages
10. Vessel Turnaround Performance: Gauge chart of port turnaround
"""
    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "Logistics",
        "qualified_table": "test.shipments",
        "schema": "test",
        "table": "shipments",
    }
    columns = ["Id", "Carrier", "Port", "Status", "Spend", "TransitDays", "ExceptionCode", "Destination", "Date"]

    proposal = await propose.propose_dashboard(
        message=prompt,
        target=target,
        columns=columns,
        sample_rows=[],
    )

    assert len(proposal["kpis"]) == 10, f"Expected 10 KPIs, got {len(proposal['kpis'])}: {[k['label'] for k in proposal['kpis']]}"
    assert len(proposal["charts"]) == 10, f"Expected 10 Charts, got {len(proposal['charts'])}: {[c['title'] for c in proposal['charts']]}"


@pytest.mark.asyncio
async def test_vessel_call_10_kpis_and_10_charts_exact_prompt():
    from app.dashboard.propose import propose_dashboard

    prompt = """
Create a professional, modern Vessel Call dashboard for a Shipping Agency to monitor the complete vessel call lifecycle from scheduling and arrival to port operations and departure. The dashboard must provide a clear operational overview of vessel calls, upcoming arrivals, vessels currently in port, departures, delays, port stay duration, turnaround performance, and document processing.

Include these KPI cards at the top:
- Total Vessel Calls
- Scheduled Arrivals
- Arrived Vessels
- Vessels Currently in Port
- Departed Vessels
- Delayed Vessel Calls
- Completed Vessel Calls
- Average Port Stay
- Average Turnaround Time
- Pending Documents

Include these dashboard sections:
1. Vessel Call Status Overview Show the number of vessel calls by status: Scheduled, Arriving, Arrived, In Port, Operations in Progress, Ready for Departure, Departed, Completed, Delayed, and Cancelled.
2. Vessel Call Pipeline Show the vessel lifecycle as: Scheduled → Arrival → Port Entry → Berthing → Port Operations → Departure → Completed.
3. Arrival & Departure Schedule Display upcoming and recent vessel movements with: Vessel Name, IMO Number, Voyage Number, Port, ETA, ETD, Actual Arrival, Actual Departure, Berth, and Status.
4. Vessel Call Trend Show daily, weekly, and monthly trends for: Vessel Calls, Arrivals, Departures, and Completed Calls.
5. Port Stay & Turnaround Performance Show: Average Port Stay, Average Berthing Time, Average Operation Time, Average Turnaround Time, Longest Port Stay, and Delayed Calls.
6. Delay Analysis Show the main causes of delays: Weather, Port Congestion, Berth Unavailability, Cargo Operations, Documentation Delay, Customs Clearance, Technical Issues, Late Arrival, and Other.
7. Document Processing Show: Total Documents, Processed Documents, Pending Documents, Missing Documents, and Rejected Documents.
8. Recent Vessel Calls Show the latest vessel calls with: Vessel Name, Voyage Number, Port, ETA, ETD, Status, Delay, and Next Action.
9. Vessel Call Details Create a searchable and sortable table containing: Vessel Call ID, Vessel Name, IMO Number, Voyage Number, Vessel Type, Port, Terminal, Berth, ETA, ETD, Actual Arrival, Actual Departure, Port Stay, Current Status, Delay, and Document Status.
10. AI Operational Insights Display actionable insights about: - Delayed vessels - Upcoming vessel calls requiring attention - Vessels at risk of missing departure schedules - Ports or berths causing delays - Unusually long port stays - Pending or missing documents - Operational bottlenecks - Expected vessel workload for the next 7 days

Add filters for: Date/Timeframe, Port, Terminal, Vessel, Vessel Type, Voyage, Call Status, Berth, Document Status, and Delay Status.
"""
    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "Vessel Calls",
        "qualified_table": "test.vessel_calls",
        "schema": "test",
        "table": "vessel_calls",
    }
    columns = [
        "VesselCallID", "VesselName", "IMONumber", "VoyageNumber", "VesselType",
        "Port", "Terminal", "Berth", "ETA", "ETD", "ActualArrival", "ActualDeparture",
        "PortStay", "CurrentStatus", "Delay", "DocumentStatus"
    ]

    proposal = await propose_dashboard(
        message=prompt,
        target=target,
        columns=columns,
        sample_rows=[],
    )

    kpis = proposal["kpis"]
    charts = proposal["charts"]
    tables = proposal["tables"]
    filters = proposal["filters"]

    assert len(kpis) == 10, f"Expected 10 KPIs, got {len(kpis)}: {[k['label'] for k in kpis]}"
    assert len(charts) == 10, f"Expected 10 Charts, got {len(charts)}: {[c['title'] for c in charts]}"
    assert len(tables) >= 1, "Expected at least 1 table"
    assert len(filters) >= 8, f"Expected filters, got {len(filters)}"




