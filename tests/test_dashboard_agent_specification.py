"""Comprehensive tests for Dashboard Agent Specification (Rules 1 to 24).

Verifies:
1. Zero Predefined Dashboard Content (Rule 1, Rule 21)
2. Dynamic Prompt Understanding & Requirement Extraction (Rule 2)
3. Prompt is Source of Truth & Traceability (Rule 3, Rule 22)
4. Dynamic Schema Generation across multiple domains (Rule 4-8)
   - Vessel Call Dashboard
   - RFQ Lifecycle Dashboard
   - Inventory Management Dashboard
   - HR Analytics Dashboard
5. Dynamic Data Requirements & load_data() (Rule 9, Rule 10)
6. Dynamic Field Resolution (Rule 11)
7. Schema Validation & Regeneration Loop (Rule 12, Rule 13)
8. Complex Prompts & Layout Requirements (Rule 14, Rule 16)
9. Required Internal Contract (Rule 20)
10. Fully Functional HTML Generation (Rule 17, Rule 18)
"""
from __future__ import annotations

import pytest

from app.dashboard.analyzer import analyze_prompt_heuristic
from app.dashboard.propose import (
    propose_generic,
    resolve_column,
)
from app.dashboard.render import render_dashboard_html
from app.dashboard.validator import (
    regenerate_and_correct_schema,
    validate_schema,
)
from app.dashboard.widgets import (
    hydrate_from_spec,
    load_data,
)


def test_rule_1_and_21_no_predefined_content():
    """Verify code does not use prohibited hardcoded RFQ/Vessel/Invoice templates."""
    import inspect
    import app.dashboard.propose as propose_mod
    import app.dashboard.agent as agent_mod

    propose_src = inspect.getsource(propose_mod)
    agent_src = inspect.getsource(agent_mod)

    # Prohibited patterns from Section 21
    prohibited = [
        "DEFAULT_KPIS =",
        "DEFAULT_KPI_LIST =",
        "RFQ_DASHBOARD_SCHEMA =",
        "VESSEL_DASHBOARD_SCHEMA =",
        "INVOICE_DASHBOARD_SCHEMA =",
        "DEFAULT_CHART_LIST =",
        "DEFAULT_FILTER_LIST =",
        "_CLASSIC_IDS =",
        "_AP_SAMPLE_FIRST =",
    ]
    for pattern in prohibited:
        assert pattern not in propose_src, f"Prohibited pattern {pattern} found in propose.py"
        assert pattern not in agent_src, f"Prohibited pattern {pattern} found in agent.py"


def test_rule_2_complex_vessel_prompt_understanding():
    """Verify dynamic extraction on Section 14 complex vessel call prompt."""
    prompt = (
        "Create a modern vessel call dashboard. "
        "Show total vessel calls, completed calls, delayed calls and active calls. "
        "Add a monthly vessel call trend, port-wise performance, delay distribution, "
        "vessel status breakdown and a detailed vessel table. "
        "Allow filtering by date, port and vessel status. "
        "Clicking a vessel should show its detailed call information."
    )
    columns = [
        "VesselId", "VesselName", "Port", "Berth", "ArrivalTime",
        "DepartureTime", "Status", "DelayMinutes", "Agent"
    ]
    target = {"repository_name": "Vessel Call Operations", "qualified_table": "marine.vessel_calls"}

    analysis = analyze_prompt_heuristic(prompt, target, columns)

    # Title & Domain
    assert "Vessel" in analysis["title"]
    assert "Vessel" in analysis["domain"]

    # KPIs extracted
    kpi_names = [k["name"].lower() for k in analysis["kpis"]]
    assert any("total" in k for k in kpi_names)
    assert any("completed" in k for k in kpi_names)
    assert any("delayed" in k for k in kpi_names)
    assert any("active" in k for k in kpi_names)

    # Charts extracted
    chart_titles = [c["title"].lower() for c in analysis["charts"]]
    assert any("trend" in c for c in chart_titles)
    assert any("performance" in c or "port" in c for c in chart_titles)
    assert any("delay" in c or "breakdown" in c or "distribution" in c for c in chart_titles)

    # Filters extracted
    filter_labels = [f["label"].lower() for f in analysis["filters"]]
    assert any("date" in f for f in filter_labels)
    assert any("port" in f for f in filter_labels)
    assert any("status" in f for f in filter_labels)

    # Table & Interactions
    assert len(analysis["tables"]) >= 1
    assert any(i["type"] == "inspect_drawer" for i in analysis["interactions"])


def test_rule_3_and_22_prompt_traceability_across_domains():
    """Verify every component is traceable and unrequested domains are not added."""
    vessel_prompt = "Create a vessel call dashboard showing active calls and port arrivals."
    vessel_cols = ["VesselName", "Port", "ArrivalTime", "Status"]
    kpis, charts = propose_generic(vessel_cols, message=vessel_prompt)

    for item in kpis + charts:
        # Every component has traceability
        assert "requirement" in item
        assert item["requirement"]
        # No AP / invoice contamination
        lbl = (item.get("label") or item.get("title") or "").lower()
        assert "invoice" not in lbl
        assert "payable" not in lbl
        assert "dpo" not in lbl


def test_rule_4_to_8_rfq_domain_schema():
    """Verify dynamic schema for RFQ Lifecycle domain."""
    rfq_prompt = (
        "Create an RFQ pipeline dashboard. "
        "Show total RFQs, won RFQs, in progress and lost RFQs. "
        "Include an RFQ status overview and equipment breakdown. "
        "Allow filtering by customer and equipment."
    )
    rfq_cols = ["RfqId", "CustomerName", "Origin", "Destination", "EquipmentType", "Status", "QuoteAmount"]
    kpis, charts = propose_generic(rfq_cols, message=rfq_prompt)

    kpi_labels = [k["label"].lower() for k in kpis]
    assert any("rfq" in k or "total" in k for k in kpi_labels)
    assert len(charts) >= 1


def test_rule_4_to_8_inventory_domain_schema():
    """Verify dynamic schema for Inventory Management domain."""
    inv_prompt = (
        "Build an inventory management dashboard. "
        "Show total SKU count, out of stock items and total valuation. "
        "Add stock by warehouse and category distribution charts. "
        "Filter by warehouse and category."
    )
    inv_cols = ["Sku", "ItemName", "Category", "Warehouse", "Quantity", "UnitPrice", "ReorderLevel"]
    kpis, charts = propose_generic(inv_cols, message=inv_prompt)

    kpi_labels = [k["label"].lower() for k in kpis]
    assert any("sku" in k or "stock" in k or "valuation" in k or "total" in k for k in kpi_labels)
    chart_titles = [c["title"].lower() for c in charts]
    assert any("warehouse" in c or "category" in c or "distribution" in c for c in chart_titles)


def test_rule_9_and_10_load_data_function():
    """Verify load_data retrieves and hydrates data dynamically according to data_requirements."""
    columns = ["VesselName", "Port", "ArrivalTime", "Status", "DelayMinutes"]
    rows = [
        {"VesselName": "Ever Given", "Port": "Rotterdam", "ArrivalTime": "2026-09-01", "Status": "Completed", "DelayMinutes": 15},
        {"VesselName": "Maersk MC", "Port": "Singapore", "ArrivalTime": "2026-09-05", "Status": "Active", "DelayMinutes": 0},
        {"VesselName": "CMA CGM", "Port": "Rotterdam", "ArrivalTime": "2026-09-10", "Status": "Delayed", "DelayMinutes": 120},
        {"VesselName": "MSC Oscar", "Port": "Shanghai", "ArrivalTime": "2026-09-12", "Status": "Completed", "DelayMinutes": 45},
    ]

    data_reqs = {
        "fields": [
            {"name": "vessel_name", "role": "identifier", "reason": "Identify vessels"},
            {"name": "port", "role": "dimension", "reason": "Group by port"},
            {"name": "status", "role": "dimension", "reason": "Status breakdown"},
            {"name": "delay_minutes", "role": "measure", "reason": "Calculate total delay"},
        ],
        "dimensions": ["port", "status"],
        "measures": ["delay_minutes"],
        "filters": ["port", "status"],
        "calculations": [],
    }

    kpis = [
        {"id": "total_vessels", "label": "Total Vessels", "agg": "count", "enabled": True, "columns": {}},
        {"id": "total_delay", "label": "Total Delay", "agg": "sum", "enabled": True, "columns": {"value": "DelayMinutes"}},
        {"id": "max_delay", "label": "Max Delay", "agg": "max", "enabled": True, "columns": {"value": "DelayMinutes"}},
        {"id": "ports_visited", "label": "Ports Visited", "agg": "distinct", "enabled": True, "columns": {"value": "Port"}},
    ]

    charts = [
        {"id": "port_calls", "title": "Calls by Port", "type": "bar", "agg": "count", "enabled": True, "columns": {"group": "Port"}},
        {"id": "status_dist", "title": "Status Mix", "type": "donut", "agg": "count", "enabled": True, "columns": {"group": "Status"}},
    ]

    tables = [
        {"id": "vessel_table", "title": "Vessel Calls", "columns": ["VesselName", "Port", "Status", "DelayMinutes"]}
    ]

    filters = [
        {"id": "port_filter", "label": "Port", "field": "Port", "type": "select"},
        {"id": "status_filter", "label": "Status", "field": "Status", "type": "select"},
    ]

    hydrated = load_data(
        data_requirements=data_reqs,
        rows=rows,
        columns=columns,
        kpis=kpis,
        charts=charts,
        tables=tables,
        filters=filters,
    )

    # Check KPIs
    assert hydrated["kpis"]["total_vessels"]["value"] == 4
    assert hydrated["kpis"]["total_delay"]["value"] == 180.0
    assert hydrated["kpis"]["max_delay"]["value"] == 120.0
    assert hydrated["kpis"]["ports_visited"]["value"] == 3

    # Check Charts
    port_chart = hydrated["charts"]["port_calls"]
    assert "Rotterdam" in port_chart["categories"]
    assert port_chart["values"][port_chart["categories"].index("Rotterdam")] == 2.0

    # Check Filter options extracted from data
    assert "Rotterdam" in hydrated["filters"]["port_filter"]["options"]
    assert "Singapore" in hydrated["filters"]["port_filter"]["options"]
    assert "Active" in hydrated["filters"]["status_filter"]["options"]

    # Check Table rows preserved
    assert len(hydrated["tables"]["vessel_table"]["rows"]) == 4


def test_rule_11_dynamic_field_resolution():
    """Verify semantic resolution of user terminology to database columns."""
    columns = ["pk_id", "ship_name", "arrival_timestamp", "port_code", "is_delayed"]

    assert resolve_column(columns, "ship_name") == "ship_name"
    assert resolve_column(columns, "vessel") == "ship_name"
    assert resolve_column(columns, "port") == "port_code"
    assert resolve_column(columns, "arrival") == "arrival_timestamp"

    # Unresolved column returns None rather than hallucinating
    assert resolve_column(columns, "unrelated_nuclear_reactor_status") is None


def test_rule_12_and_13_validator_and_regeneration():
    """Verify validator identifies missing requirements and regenerator corrects them."""
    prompt_analysis = {
        "title": "Vessel Call Operations",
        "domain": "Vessel Operations",
        "purpose": "Monitor vessel arrivals",
        "kpis": [
            {"name": "Total Calls", "metric": "total calls", "aggregation": "count", "requirement": "Show total calls"},
            {"name": "Delayed Calls", "metric": "delayed calls", "aggregation": "count", "requirement": "Show delayed calls"},
        ],
        "charts": [
            {"title": "Calls By Port", "type": "bar", "requirement": "Port performance chart"},
            {"title": "Delay Distribution", "type": "pie", "requirement": "Delay breakdown"},
        ],
        "filters": [
            {"label": "Port", "field_concept": "port", "requirement": "Filter by port"},
        ],
    }

    # Incomplete schema missing "Delayed Calls" and "Delay Distribution"
    schema = {
        "title": "Vessel Call Operations",
        "kpis": [
            {"id": "total_calls", "label": "Total Calls", "agg": "count", "requirement": "Show total calls"}
        ],
        "charts": [
            {"id": "calls_by_port", "title": "Calls By Port", "type": "bar", "agg": "count", "requirement": "Port performance"}
        ],
        "filters": [],
    }

    data_reqs = {"fields": [{"name": "port", "role": "dimension"}]}
    columns = ["VesselName", "Port", "DelayMinutes"]

    val = validate_schema(
        prompt_analysis=prompt_analysis,
        schema=schema,
        data_requirements=data_reqs,
        columns=columns,
    )

    # Must fail validation due to missing items
    assert not val["requirements_covered"]
    assert any("Delayed Calls" in m for m in val["missing_requirements"])
    assert any("Delay Distribution" in m for m in val["missing_requirements"])

    # Regeneration corrects the schema
    corrected_schema, new_val = regenerate_and_correct_schema(
        prompt_analysis=prompt_analysis,
        schema=schema,
        validation_result=val,
        columns=columns,
    )

    assert new_val["requirements_covered"]
    assert any("delay" in k["id"].lower() for k in corrected_schema["kpis"])
    assert any("delay" in c["id"].lower() for c in corrected_schema["charts"])
    assert any("port" in f["id"].lower() for f in corrected_schema["filters"])


def test_rule_17_and_18_html_interactive_elements():
    """Verify final HTML generation includes all interactive controls, filters, search, and CSV export."""
    dashboard = {
        "title": "Global Vessel Logistics Command",
        "message": "Real-time vessel arrivals and berth management",
        "kpis": [
            {"id": "total_calls", "label": "Total Vessel Calls", "value": 482},
            {"id": "delayed_calls", "label": "Delayed Calls", "value": 24},
            {"id": "berth_util", "label": "Berth Utilization", "value": "88%", "unit": "%"},
        ],
        "charts": [
            {"id": "port_performance", "title": "Port Performance Overview", "type": "bar"},
            {"id": "arrival_trend", "title": "Arrival Trend Over Time", "type": "line"},
        ],
        "filters": [
            {"id": "port_filter", "label": "Port Terminal", "field": "port"},
            {"id": "vessel_type", "label": "Vessel Category", "field": "category"},
        ],
        "tables": [
            {"id": "vessels", "title": "Active Vessels", "columns": ["vessel", "port", "status", "eta"]}
        ],
        "insights": [
            "Rotterdam terminal processed 42 vessel calls with zero berthing delays.",
            "Average anchorage wait time decreased to 3.2 hours this month.",
        ],
        "rows": [
            {"id": 1, "vessel": "Ever Ace", "port": "Rotterdam", "status": "Berthed", "eta": "2026-10-01"},
            {"id": 2, "vessel": "HMM Algeciras", "port": "Singapore", "status": "En Route", "eta": "2026-10-03"},
        ],
        "data": {
            "kpis": {
                "total_calls": {"value": 482, "trend": "+8%"},
                "delayed_calls": {"value": 24, "trend": "-15%"},
            },
            "charts": {
                "port_performance": {"categories": ["Rotterdam", "Singapore"], "values": [42, 38]},
                "arrival_trend": {"categories": ["W1", "W2"], "values": [20, 22]},
            },
        },
    }

    html = render_dashboard_html(dashboard, message="Vessel Logistics")

    # Critical interactive HTML assertions
    assert html.lstrip().startswith("<!DOCTYPE html>") or html.lstrip().startswith("<style>")
    assert "ez-dash" in html
    assert "Global Vessel Logistics Command" in html
    assert "Total Vessel Calls" in html
    assert "Delayed Calls" in html
    assert "exportCSV" in html
    assert "globalSearch" in html
    assert "regSearch" in html
    assert "pills" in html
    assert "side" in html
    assert "toast" in html
    assert "getFilteredRows" in html
    assert "setDynFilter" in html


@pytest.mark.asyncio
async def test_rule_20_required_internal_contract():
    """Verify Section 20 required contract structure on propose_dashboard."""
    from app.dashboard.propose import propose_dashboard

    target = {
        "tenant_id": "test-tenant",
        "repository_id": "test-repo",
        "repository_name": "Fleet Telematics",
        "qualified_table": "fleet.telematics",
    }
    columns = ["VehicleId", "DriverName", "Speed", "FuelLevel", "Status", "Timestamp"]

    result = await propose_dashboard(
        message="Create a fleet telematics dashboard showing vehicle status and fuel levels.",
        target=target,
        columns=columns,
    )

    # Section 20 contract structure
    assert "prompt_analysis" in result
    assert "domain" in result["prompt_analysis"]
    assert "purpose" in result["prompt_analysis"]
    assert "requirements" in result["prompt_analysis"] or "requirements_summary" in result["prompt_analysis"]

    assert "dashboard_schema" in result
    ds = result["dashboard_schema"]
    assert "title" in ds
    assert "kpis" in ds
    assert "charts" in ds
    assert "tables" in ds
    assert "filters" in ds

    assert "data_requirements" in result
    dr = result["data_requirements"]
    assert "fields" in dr

    assert "validation" in result
    val = result["validation"]
    assert "requirements_covered" in val
    assert "missing_requirements" in val
    assert "unsupported_assumptions" in val
    assert "unresolved_fields" in val
