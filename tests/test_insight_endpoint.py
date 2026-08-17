"""Insight agent: legacy report path + locked insight_result document jobs."""
from app.insight_skills.lock import parse_insight_json_content


def _install_fake_llm(monkeypatch, content=None):
    payload = content or '{"insights":["Overdue share is elevated.","90+ aging needs attention."]}'

    async def fake_chat_completion(self, messages):
        return {
            "content": payload,
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_insight_intent_returns_insights_with_cited_data_points(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content="Overdue invoices are a small share of open invoices.",
    )

    response = client.post(
        "/chat",
        json={"session_id": "s-insight", "message": "give me insights on report RPT-456"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cited_data_points"] == [
        "Open Invoices",
        "Overdue Invoices",
        "Total Outstanding ($)",
        "Avg Days to Payment",
    ]
    assert body["reply"] == "Overdue invoices are a small share of open invoices."
    assert body["token_usage"]["total_tokens"] == 18
    assert body["chunk_ids"] is None
    assert body["document_id"] is None
    assert body.get("insight_result") is None


def test_insight_tool_failure_returns_502(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_fetch_report_data(self, report_id):
        raise RuntimeError("simulated EZOFIS outage, token=should-never-leak")

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.fetch_report_data",
        broken_fetch_report_data,
    )

    response = client.post(
        "/chat",
        json={"session_id": "s-insight-fail", "message": "insights on report RPT-999"},
    )

    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert "should-never-leak" not in response.text
    assert "detail" in response.json()


def test_insight_from_arbitrary_json_returns_locked_insights(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    dashboard = {
        "title": "AP Aging",
        "open_invoices": 120,
        "overdue_invoices": 18,
        "total_outstanding": 245000,
        "buckets": {"0_30": 80000, "31_60": 90000, "61_90": 45000, "90_plus": 30000},
    }
    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-json",
            "intent": "insight",
            "payload": {"insight_json": dashboard},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Insights generated successfully."
    result = body["insight_result"]
    assert set(result.keys()) >= {"insights", "source_reference", "insights_count"}
    assert result["source_reference"] == "insight_json"
    assert result["insights_count"] == 4
    assert result["insights"] == [
        "Overdue share is elevated.",
        "90+ aging needs attention.",
    ]
    assert body["cited_data_points"] is None
    assert body["token_usage"]["total_tokens"] == 18


def test_insight_from_ocr_text_skips_paddle(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    from app.core.dispatcher import Dispatcher

    original = Dispatcher.dispatch

    async def guarded(self, tool_name, args):
        if tool_name == "run_ocr":
            raise AssertionError("run_ocr must not run when ocr_text is supplied")
        return await original(self, tool_name, args)

    monkeypatch.setattr(Dispatcher, "dispatch", guarded)

    supplied = "Open invoices: 50\nOverdue: 12\nOutstanding: $90,000"
    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-ocr",
            "intent": "insight",
            "payload": {"ocr_text": supplied},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["insight_result"]["source_reference"] == "ocr_text"
    assert len(body["insight_result"]["insights"]) == 2


def test_insight_json_wins_over_ocr_text(client, monkeypatch):
    seen = {}

    async def fake_chat_completion(self, messages):
        seen["user"] = messages[1]["content"]
        return {
            "content": '{"insights":["From JSON path."]}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-precedence",
            "intent": "insight",
            "payload": {
                "insight_json": {"kpi": "A"},
                "ocr_text": "THIS SHOULD NOT APPEAR",
            },
        },
    )
    assert response.status_code == 200
    assert "THIS SHOULD NOT APPEAR" not in seen["user"]
    assert '"kpi": "A"' in seen["user"] or '"kpi":"A"' in seen["user"].replace(" ", "")
    assert body_insight_source(response) == "insight_json"


def body_insight_source(response):
    return response.json()["insight_result"]["source_reference"]


def test_parse_insight_json_content_locks_shape():
    locked = parse_insight_json_content(
        '```json\n{"insights":["One.","Two."],"extra":true}\n```'
    )
    assert locked["insights"] == ["One.", "Two."]
    assert locked["insights_count"] == 4

    locked_list = parse_insight_json_content('["Alpha","Beta"]')
    assert locked_list["insights"] == ["Alpha", "Beta"]
    assert locked_list["insights_count"] == 4


def test_insight_insights_count_truncates(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content='{"insights":["One.","Two.","Three.","Four.","Five.","Six."]}',
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-count",
            "intent": "insight",
            "payload": {
                "insight_json": {"open_invoices": 10, "overdue": 2},
                "insights_count": 3,
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["insight_result"]
    assert len(result["insights"]) == 3
    assert result["insights_count"] == 3


def test_insight_area_in_request_and_response(client, monkeypatch):
    seen = {}

    async def fake_chat_completion(self, messages):
        seen["user"] = messages[1]["content"]
        return {
            "content": '{"insights":["AP aging is concentrated in 90+ days."]}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-area",
            "intent": "insight",
            "payload": {
                "insight_area": "AP Aging Dashboard",
                "insight_json": {"open_invoices": 120, "overdue_invoices": 18},
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["insight_result"]
    assert result["insight_area"] == "AP Aging Dashboard"
    assert "AP Aging Dashboard" in seen["user"]


def test_insight_json_no_and_dashboard_aliases(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content='{"insights":["A.","B.","C.","D.","E."]}',
    )
    response = client.post(
        "/chat",
        json={
            "session_id": "s-insight-no",
            "intent": "insight",
            "payload": {
                "insight_json": {
                    "no": 2,
                    "dashboard": "Cash Flow",
                    "total": 50000,
                },
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["insight_result"]
    assert len(result["insights"]) == 2
    assert result["insights_count"] == 2
    assert result["insight_area"] == "Cash Flow"
