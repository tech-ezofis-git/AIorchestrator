import pytest
from app.dashboard.render import render_dashboard_html


def test_repository_dropdown_and_enterprise_scoping():
    dashboard = {
        "title": "RFQ Documents",
        "repository_name": "RFQ Documents",
        "message": "Complete lifecycle monitoring",
        "kpis": [
            {"id": "total_rfqs", "label": "Total RFQs", "value": 30},
            {"id": "won_rfqs", "label": "Won RFQs", "value": 10},
        ],
        "charts": [
            {"id": "status_overview", "title": "Status Overview", "type": "bar"},
        ],
        "tables": [
            {"columns": ["rfq_no", "customer", "status", "quote_value"]},
        ],
        "filters": [
            {"id": "equipment", "label": "Equipment", "field": "equipment"},
        ],
        "data": {
            "kpis": {"total_rfqs": {"value": 30}},
            "charts": {"status_overview": {"categories": ["Won", "Lost"], "values": [10, 5]}},
        },
    }

    html = render_dashboard_html(dashboard, message="RFQ Process")

    # 1. Check repository navigation elements
    assert "RFQ Documents" in html
    assert "All Enterprise Repositories" in html
    assert "sel-repo" in html
    assert "data-act=\"setFilter\" data-f=\"repo\"" in html

    # 2. Check repository tagging in dataset
    assert '"repository": "RFQ Documents"' in html
    assert "All Enterprise Repositories" in html
    assert "RFQ Documents - Operations" in html or "RFQ Documents - Archive" in html

    # 3. Check filter capabilities in JavaScript
    assert "getRowVal" in html
    assert "getFilteredRows" in html
    assert "resetAllFilters" in html
    assert "Timeframe:" in html
    assert "Status:" in html
    assert "clearFilter" in html
    assert "clearDynFilter" in html

    # 4. Check that dynamic selection logic doesn't hardcode static checkmarks
    assert "state.repo === opt" in html
    assert "circle-check" in html


def test_vessel_repository_and_filters():
    dashboard = {
        "title": "Vessel Call Operations",
        "repository_name": "Vessel Call Operations",
        "message": "Port operations intelligence",
        "kpis": [
            {"id": "total_calls", "label": "Total Vessel Calls", "value": 24},
        ],
        "charts": [
            {"id": "port_distribution", "title": "Port Distribution", "type": "bar"},
        ],
    }

    html = render_dashboard_html(dashboard, message="Vessel Calls")

    assert "Vessel Call Operations" in html
    assert "All Enterprise Repositories" in html
    assert "Vessel Call Operations - Operations" in html or "Vessel Call Operations - Archive" in html
    assert "regSearch" in html
    assert "globalSearch" in html
