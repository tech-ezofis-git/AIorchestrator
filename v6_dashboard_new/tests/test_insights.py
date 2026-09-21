"""Insights follow the user message instead of a canned pack."""
import asyncio

from app.insights import fallback_insights, generate_insights, user_ask


def _ap_kpis():
    return [
        {"id": "total_ap", "label": "TOTAL AP", "enabled": True, "description": "Sum of invoice amounts."},
        {"id": "overdue", "label": "OVERDUE", "enabled": True, "description": "Unpaid invoices past DueDate."},
        {"id": "open_invoices", "label": "OPEN INVOICES", "enabled": True, "description": "Count of invoices."},
    ]


def _ap_charts():
    return [
        {
            "id": "supplier_risk",
            "title": "Supplier Risk Radar",
            "enabled": True,
            "type": "radar",
            "description": "Outstanding amount by supplier.",
        },
        {
            "id": "match_status",
            "title": "Match status",
            "enabled": True,
            "type": "donut",
            "description": "Count grouped by MatchedStatus.",
        },
    ]


def _ap_data():
    return {
        "kpis": {
            "total_ap": {"value": 25776},
            "overdue": {"value": 15276},
            "open_invoices": {"value": 5},
        },
        "charts": {
            "supplier_risk": {
                "type": "radar",
                "series": [
                    {"name": "Acme", "value": 9000},
                    {"name": "Nexus", "value": 1600},
                ],
            },
            "match_status": {
                "type": "donut",
                "series": [
                    {"name": "Matched", "value": 3},
                    {"name": "Not Matched", "value": 2},
                ],
            },
        },
    }


def test_fallback_overdue_ask_leads_with_overdue_not_canned_filler():
    lines = fallback_insights(
        repository_name="Accounts Payable",
        kpis=_ap_kpis(),
        charts=_ap_charts(),
        data=_ap_data(),
        message="Show only overdue and vendor risk",
    )
    blob = " ".join(lines).lower()
    assert "due" in blob
    assert "acme" in blob
    assert "live values are computed" not in blob


def test_fallback_match_ask_talks_about_match_not_overdue_first():
    lines = fallback_insights(
        repository_name="Accounts Payable",
        kpis=_ap_kpis(),
        charts=_ap_charts(),
        data=_ap_data(),
        message="Match status only",
    )
    blob = " ".join(lines).lower()
    assert "match" in lines[0].lower()
    assert "matched" in blob
    assert "total ap" not in blob
    assert "sum of invoice amounts" not in blob


def test_user_ask_uses_widget_copy_when_data_message_is_apply():
    ask = user_ask("apply", _ap_kpis(), _ap_charts())
    assert "overdue" in ask.lower() or "supplier" in ask.lower() or "match" in ask.lower()
    assert ask.lower() != "apply"


def test_generate_insights_sends_user_message_to_model(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_chat_json(*, system, user, **_kwargs):
        seen["system"] = system
        seen["user"] = user
        return {"insights": ["Acme holds the most outstanding amount.", "Overdue is 15,276."]}

    monkeypatch.setattr("app.insights.chat_json", fake_chat_json)
    lines = asyncio.run(
        generate_insights(
            repository_name="Accounts Payable",
            kpis=_ap_kpis(),
            charts=_ap_charts(),
            data=_ap_data(),
            message="ap dashboard only with supplier risk radar",
        )
    )
    assert "supplier risk" in seen["user"].lower()
    assert "Do not reuse a default insight list" in seen["user"]
    assert lines[0] == "Acme holds the most outstanding amount."


def test_kpi_only_ask_returns_no_insights():
    lines = fallback_insights(
        repository_name="Accounts Payable",
        kpis=_ap_kpis(),
        charts=_ap_charts(),
        data=_ap_data(),
        message="I need an AP dashboard only kpis",
    )
    assert lines == []
