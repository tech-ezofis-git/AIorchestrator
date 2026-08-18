"""GET /console — the manual test console page (not a phase deliverable,
a dev convenience). Just proves the route serves the themed HTML page and
its static logo asset, and that it's exempt from the guardrail pipeline
the same way /health and /metrics are.
"""


def test_console_serves_html(client):
    response = client.get("/console")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AI Orchestrator" in response.text
    assert "/chat" in response.text  # the page's own fetch() call target
    assert "AP document" in response.text
    assert 'id="apPanel"' in response.text
    assert "Run AP" in response.text
    assert "invoice_json" in response.text
    assert 'id="summaryFields"' in response.text
    assert 'id="docPanel"' in response.text
    assert 'id="summaryOcrText"' in response.text
    assert ".docx" in response.text
    assert "summary-card" in response.text
    assert "summary-code" in response.text
    assert "cURL" in response.text
    assert "intent: 'summary'" in response.text
    assert "intent: 'ocr'" in response.text
    assert "intent: 'ap'" in response.text
    assert "buildChatCurl" in response.text
    assert "function renderOcrResult" in response.text
    assert "function renderApResult" in response.text
    assert 'id="summaryPackInspector"' in response.text
    assert 'data-pack-tab="tenant"' in response.text
    assert "tenant-item-edit" in response.text
    assert "custom-skills" in response.text
    assert "/console/summary-skills/defaults" in response.text
    assert "summaryRuleSaveBtn" in response.text
    assert "Enable" in response.text


def test_console_static_logo_is_served(client):
    response = client.get("/static/ezofis-logo-mark.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_console_is_exempt_from_rate_limiting(client, monkeypatch):
    import app.main as main_module
    from app.control.rate_limiter import RateLimiter

    monkeypatch.setattr(
        main_module.app.state,
        "rate_limiter",
        RateLimiter(main_module.app.state.rate_limiter._redis, max_requests=1, window_seconds=60),
    )

    for _ in range(5):
        response = client.get("/console")
        assert response.status_code == 200
