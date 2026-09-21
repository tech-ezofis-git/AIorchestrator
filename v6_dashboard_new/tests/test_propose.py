"""LLM proposer + generic fallback."""
import asyncio

from app.llm import LLMError
from app.propose import (
    _user_prompt,
    enrich_sparse_kpis,
    parse_color,
    propose_dashboard,
    propose_generic,
)
from app.render import render_dashboard_html


def test_propose_generic_hr_columns():
    kpis, charts = propose_generic(
        ["Id", "Department", "EmployeeName", "CreatedAtUtc", "FileType", "FileSize", "Status"],
        message="HR files dashboard by department",
    )
    kpi_ids = [row["id"] for row in kpis]
    chart_groups = [row["columns"]["group"] for row in charts]
    assert "record_count" in kpi_ids
    assert "total_value" in kpi_ids
    assert len(kpis) >= 4
    assert len(charts) >= 3
    assert "Department" in chart_groups
    assert all(row.get("agg") for row in kpis + charts)
    assert all(row.get("description") for row in kpis + charts)


def test_propose_generic_kpi_only_drops_charts():
    kpis, charts = propose_generic(
        ["Id", "Supplier", "InvoiceAmount", "DueDate", "MatchedStatus", "Status"],
        message="I need an AP dashboard only kpis",
    )
    assert kpis
    assert len(kpis) >= 4
    assert charts == []


def test_enrich_sparse_kpis_pads_overview_asks():
    thin = [
        {
            "id": "total_value",
            "label": "TOTAL",
            "enabled": True,
            "agg": "sum",
            "columns": {"value": "InvoiceAmount"},
        }
    ]
    enriched, _ = enrich_sparse_kpis(
        "I need an AP dashboard",
        thin,
        [],
        ["InvoiceAmount", "DueDate", "MatchedStatus", "Supplier"],
    )
    assert len(enriched) >= 4
    assert enriched[0]["id"] == "total_value"


def test_enrich_sparse_kpis_skips_singular_asks():
    thin = [
        {
            "id": "overdue",
            "label": "OVERDUE",
            "enabled": True,
            "agg": "overdue_sum",
            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
        }
    ]
    enriched, _ = enrich_sparse_kpis(
        "only overdue",
        thin,
        [],
        ["InvoiceAmount", "DueDate", "MatchedStatus"],
    )
    assert len(enriched) == 1


def test_propose_dashboard_uses_model_json(monkeypatch):
    async def fake_chat_json(**_kwargs):
        return {
            "kpis": [
                {
                    "id": "headcount",
                    "label": "HEADCOUNT",
                    "description": "Number of HR file records in the repository.",
                    "agg": "count",
                }
            ],
            "charts": [
                {
                    "id": "by_dept",
                    "title": "By department",
                    "description": "Donut of file count grouped by department.",
                    "type": "donut",
                    "group": "Department",
                    "agg": "count",
                }
            ],
        }

    monkeypatch.setattr("app.propose.chat_json", fake_chat_json)
    kpis, charts = asyncio.run(
        propose_dashboard(
            message="HR dashboard",
            target={"repository_name": "HR Files", "qualified_table": "repository.Items_x"},
            columns=["Id", "Department", "EmployeeName", "CreatedAtUtc"],
            sample_rows=[{"Department": "Ops", "EmployeeName": "Ada"}],
        )
    )
    assert kpis[0]["id"] == "headcount"
    assert kpis[0]["agg"] == "count"
    assert kpis[0]["description"] == "Number of HR file records in the repository."
    assert len(kpis) >= 4  # sparse LLM packs are enriched for overview asks
    assert charts[0]["columns"]["group"] == "Department"
    assert charts[0]["description"] == "Donut of file count grouped by department."


def test_propose_dashboard_falls_back_when_model_fails(monkeypatch):
    async def boom(**_kwargs):
        raise LLMError("no model")

    monkeypatch.setattr("app.propose.chat_json", boom)
    kpis, charts = asyncio.run(
        propose_dashboard(
            message="files by type",
            target={"repository_name": "Documents", "qualified_table": "repository.Items_x"},
            columns=["Id", "FileType", "FileSize", "CreatedAtUtc", "Status"],
            sample_rows=[],
        )
    )
    assert kpis
    assert charts
    assert kpis[0]["id"] == "record_count"
    assert kpis[0]["description"]
    assert charts[0]["description"]


def test_user_prompt_includes_all_rows_not_five():
    rows = [{"Department": f"D{i}", "Id": str(i)} for i in range(8)]
    text = _user_prompt(
        "HR dashboard",
        {"repository_name": "HR Files", "qualified_table": "repository.Items_x"},
        ["Id", "Department"],
        rows,
    )
    assert "Row count: 8" in text
    assert "D7" in text
    assert "Sample rows" not in text


def test_parse_color_named_and_hex():
    assert "error-main" in parse_color("red") or parse_color("red").endswith("#e5484d)")
    assert parse_color("#2563eb") == "#2563eb"


def test_layout_from_user_hints(monkeypatch):
    async def fake_chat_json(**_kwargs):
        return {
            "kpis": [
                {
                    "id": "overdue",
                    "label": "OVERDUE",
                    "agg": "overdue_sum",
                    "column": "InvoiceAmount",
                    "date_column": "DueDate",
                    "order": 1,
                    "position": "top",
                    "color": "red",
                }
            ],
            "charts": [
                {
                    "id": "by_status",
                    "title": "By status",
                    "type": "donut",
                    "group": "Status",
                    "agg": "count",
                    "position": "left",
                    "color": "blue",
                }
            ],
        }

    monkeypatch.setattr("app.propose.chat_json", fake_chat_json)
    kpis, charts = asyncio.run(
        propose_dashboard(
            message="Put overdue first in red. Donut on the left in blue.",
            target={"repository_name": "AP", "qualified_table": "repository.Items_x"},
            columns=["InvoiceAmount", "DueDate", "Status"],
            sample_rows=[],
        )
    )
    assert "error-main" in kpis[0]["color"] or kpis[0]["color"].endswith("#e5484d)")
    assert kpis[0]["order"] == 1
    assert charts[0]["position"] == "left"
    assert "info-main" in charts[0]["color"] or charts[0]["color"].endswith("#0090ff)")
    assert charts[0]["span"] == 1


def test_overlay_layout_puts_kpis_at_bottom():
    from app.propose import overlay_layout_from_message

    kpis = [{"id": "a", "position": "top"}]
    charts = [{"id": "c", "position": "left"}]
    overlay_layout_from_message("I need KPIs at the bottom and charts at the top", kpis, charts)
    assert kpis[0]["position"] == "bottom"
    assert charts[0]["position"] == "left"


def test_render_html_charts_before_kpis_when_bottom():
    html = render_dashboard_html(
        {
            "repository_name": "AP",
            "kpis": [{"id": "total", "label": "TOTAL", "enabled": True, "position": "bottom"}],
            "charts": [{"id": "by_status", "title": "By status", "type": "donut", "enabled": True, "position": "top"}],
            "data": {
                "kpis": {"total": {"value": 12}},
                "charts": {"by_status": {"type": "donut", "series": [{"name": "Open", "value": 3}]}},
            },
        }
    )
    assert "ez-acc-btn" in html
    assert 'class="ez-kpis"' in html
    assert "TOTAL" in html
    assert "By status" in html
    assert "AP Command Center" in html
    assert "Key metrics" not in html
    assert html.find("By status") < html.find("AP Command Center")
    assert html.count('class="ez-acc is-open"') == 1
    assert 'aria-expanded="true"' in html


def test_render_html_uses_layout():
    html = render_dashboard_html(
        {
            "repository_name": "AP",
            "table": "repository.Items_x",
            "kpis": [{"id": "total", "label": "TOTAL", "enabled": True, "color": "#dc2626", "order": 1}],
            "charts": [
                {
                    "id": "by_status",
                    "title": "By status",
                    "description": "Status mix for invoices.",
                    "type": "donut",
                    "enabled": True,
                    "position": "left",
                    "span": 1,
                }
            ],
            "data": {
                "kpis": {"total": {"value": 12, "trend_pct": 14.2, "subtext": "+14.2% vs last month"}},
                "charts": {"by_status": {"type": "donut", "series": [{"name": "Open", "value": 3}]}},
            },
            "insights": ["Open items are concentrated in one status."],
        }
    )
    assert "ez-dash" in html
    assert "--ez-bg" in html
    assert "TOTAL" in html
    assert 'class="ez-kpi"' in html
    assert "error-main" in html or "#e5484d" in html or "#dc2626" in html
    assert "By status" in html
    assert "Status mix for invoices." in html
    assert "Open" in html
    assert "+14.2%" in html
    assert "vs last month" in html
    assert "repository.Items_x" not in html
    assert "ez-section" not in html
    assert "Key metrics" not in html
    assert "AP Command Center" in html
    assert 'class="ez-acc is-open"' in html
    assert html.count('class="ez-acc is-open"') == 1
    assert "ez-acc.is-open" in html
    assert "ez-acc-btn" in html
    assert "Donut" in html or "MATCHED" in html or "ez-acc-metric" in html
    assert html.find("AP Command Center") < html.find("By status")
    assert html.find("AP Command Center") < html.find("AI-generated insights")
    assert html.find('class="ez-kpis"') < html.find("AI-generated insights")
    assert "ez-blink" in html
    assert "Auto-updates with your filters" in html
    assert "AI-generated insights" in html
    assert "data-tip=" in html
    # Overview (open) + 1 chart accordion
    assert html.count('class="ez-acc-btn"') == 2


def test_render_html_kpi_only_uses_overview():
    html = render_dashboard_html(
        {
            "kpis": [{"id": "total", "label": "TOTAL", "enabled": True}],
            "charts": [],
            "data": {"kpis": {"total": {"value": 9}}, "charts": {}},
        }
    )
    assert 'class="ez-acc is-open"' in html
    assert "AP Command Center" in html
    assert "Key metrics" not in html
    assert 'class="ez-kpis"' in html
    assert "TOTAL" in html
    assert html.count('class="ez-acc-btn"') == 1


def test_render_html_groups_charts_into_accordion_sections():
    html = render_dashboard_html(
        {
            "kpis": [
                {"id": "a", "label": "MARGIN", "enabled": True, "order": 1},
                {"id": "b", "label": "CASH", "enabled": True, "order": 2},
            ],
            "charts": [
                {"id": "c1", "title": "Profit vs AP", "type": "column", "enabled": True, "order": 1},
                {"id": "c2", "title": "Payment trend", "type": "line", "enabled": True, "order": 2},
            ],
            "data": {
                "kpis": {"a": {"value": 0}, "b": {"value": 0}},
                "charts": {
                    "c1": {"type": "column", "categories": ["May"], "values": [10]},
                    "c2": {"type": "line", "categories": ["May"], "values": [0]},
                },
            },
        }
    )
    # Overview + 2 chart accordion buttons; only overview starts open
    assert html.count('class="ez-acc-btn"') == 3
    assert html.count('class="ez-acc is-open"') == 1
    assert 'class="ez-kpis"' in html
    assert "AP Command Center" in html
    assert "Key metrics" not in html
    assert "MARGIN" in html
    assert "CASH" in html
    assert "Profit vs AP" in html
    assert "Payment trend" in html
    assert "ez-acc-metric" in html
    assert "MAY" in html or "TOTAL" in html or "MAX" in html
    assert html.find("AP Command Center") < html.find("Profit vs AP")


def test_ap_message_goes_to_the_model_not_a_fixed_template(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_chat_json(*, system, user, **_kwargs):
        seen["system"] = system
        seen["user"] = user
        return {
            "kpis": [
                {
                    "id": "overdue_amount",
                    "label": "OVERDUE AMOUNT",
                    "agg": "overdue_sum",
                    "column": "InvoiceAmount",
                    "date_column": "DueDate",
                    "match_column": "MatchedStatus",
                }
            ],
            "charts": [
                {
                    "id": "vendor_outstanding",
                    "title": "Vendor-wise Outstanding",
                    "type": "lollipop",
                    "group": "Supplier",
                    "value": "InvoiceAmount",
                    "agg": "outstanding_sum",
                    "match_column": "MatchedStatus",
                }
            ],
        }

    monkeypatch.setattr("app.propose.chat_json", fake_chat_json)
    kpis, charts = asyncio.run(
        propose_dashboard(
            message="Highlight overdue amount and which vendors still have outstanding invoices.",
            target={"repository_name": "Accounts Payable", "qualified_table": "repository.Items_x"},
            columns=[
                "Id",
                "FileSize",
                "FileType",
                "InvoiceAmount",
                "DueDate",
                "MatchedStatus",
                "Supplier",
                "InvoiceDate",
                "Status",
            ],
            sample_rows=[
                {
                    "FileSize": 1000,
                    "FileType": "application/pdf",
                    "InvoiceAmount": None,
                    "MatchedStatus": "Matched",
                    "Supplier": "Acme",
                    "Status": None,
                }
            ],
        )
    )
    assert kpis[0]["id"] == "overdue_amount"
    assert len(kpis) >= 1
    assert [row["id"] for row in charts] == ["vendor_outstanding"]
    assert kpis[0]["agg"] == "overdue_sum"
    assert kpis[0]["columns"]["match"] == "MatchedStatus"
    assert charts[0]["agg"] == "outstanding_sum"
    assert "FileSize" not in str(kpis) + str(charts)
    assert "Highlight overdue" in seen["user"]
    assert "canned Accounts Payable pack" in seen["system"]
    assert "Column occupancy" in seen["user"]

