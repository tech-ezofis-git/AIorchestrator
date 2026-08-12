"""GET/POST /console/llm-config and POST /console/llm-test — runtime LLM
model/endpoint reconfiguration from the Test Console (app/llm/adapter.py's
configure()/describe()). No real network calls in any test here — the
underlying LLMAdapter.chat_completion is monkeypatched throughout, same
technique as the rest of this suite.
"""


def test_get_llm_config_reflects_startup_settings(client):
    response = client.get("/console/llm-config")

    assert response.status_code == 200
    body = response.json()
    # Lifespan applies the default Azure preset when LLM_API_BASE is unset.
    assert body["model"] == "azure/gpt-4.1-mini"
    assert body["api_base"] == "https://ezazopenai.openai.azure.com"
    assert body["api_version"] == "2025-01-01-preview"
    assert body["preset_id"] == "gpt-4.1-mini"
    assert body["has_api_key"] is True


def test_get_llm_presets_lists_hardcoded_models_without_keys(client):
    response = client.get("/console/llm-presets")

    assert response.status_code == 200
    body = response.json()
    ids = [p["id"] for p in body["presets"]]
    assert ids == ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini"]
    assert body["default_preset_id"] == "gpt-4.1-mini"
    assert all("api_key" not in p for p in body["presets"])
    assert "test-south-india-key" not in response.text
    assert "test-east-us-key" not in response.text


def test_post_llm_config_applies_preset(client):
    response = client.post("/console/llm-config", json={"preset_id": "gpt-4o-mini"})

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "azure/gpt-4o-mini"
    assert body["api_base"] == "https://api-4omin-ez.openai.azure.com"
    assert body["api_version"] == "2025-01-01-preview"
    assert body["preset_id"] == "gpt-4o-mini"
    assert body["has_api_key"] is True
    assert "test-east-us-key" not in response.text


def test_post_llm_config_unknown_preset_returns_400(client):
    response = client.post("/console/llm-config", json={"preset_id": "no-such-model"})

    assert response.status_code == 400


def test_post_llm_config_updates_model_and_never_echoes_the_key(client):
    response = client.post(
        "/console/llm-config",
        json={"model": "openai/qwen3.5-9b", "api_base": "https://example-endpoint/v1", "api_key": "sk-super-secret-marker"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "openai/qwen3.5-9b"
    assert body["api_base"] == "https://example-endpoint/v1"
    assert body["has_api_key"] is True
    assert "sk-super-secret-marker" not in response.text

    # A follow-up GET reflects the same, still without ever returning the key.
    follow_up = client.get("/console/llm-config")
    assert follow_up.json()["model"] == "openai/qwen3.5-9b"
    assert "sk-super-secret-marker" not in follow_up.text


def test_post_llm_config_partial_update_leaves_other_fields_alone(client):
    client.post("/console/llm-config", json={"model": "custom-model", "api_base": "https://example/v1"})

    # Only sending `model` this time — api_base must be unchanged.
    response = client.post("/console/llm-config", json={"model": "another-model"})

    body = response.json()
    assert body["model"] == "another-model"
    assert body["api_base"] == "https://example/v1"
    assert body["has_api_key"] is True  # default preset key still set from lifespan


def test_post_llm_config_empty_string_clears_api_base_and_key(client):
    client.post("/console/llm-config", json={"api_base": "https://example/v1", "api_key": "sk-marker"})

    response = client.post("/console/llm-config", json={"api_base": "", "api_key": ""})

    assert response.json()["api_base"] is None
    assert response.json()["has_api_key"] is False


def test_llm_config_endpoints_are_exempt_from_rate_limiting(client, monkeypatch):
    import app.main as main_module
    from app.control.rate_limiter import RateLimiter

    monkeypatch.setattr(
        main_module.app.state,
        "rate_limiter",
        RateLimiter(main_module.app.state.rate_limiter._redis, max_requests=1, window_seconds=60),
    )

    for _ in range(3):
        assert client.get("/console/llm-config").status_code == 200
        assert client.post("/console/llm-config", json={}).status_code == 200


def test_llm_test_endpoint_reports_success_without_touching_chat_pipeline(client, monkeypatch):
    calls = []

    async def fake_chat_completion(self, messages):
        calls.append(messages)
        return {"content": "api-ok", "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post("/console/llm-test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reply"] == "api-ok"
    assert body["usage"]["total_tokens"] == 7
    assert len(calls) == 1
    # Doesn't go through /chat's guardrails/session machinery — a single,
    # direct call with no session_id involved.
    assert calls[0] == [{"role": "user", "content": "Reply with exactly: api-ok"}]


def test_llm_test_endpoint_reports_failure_as_data_not_502(client, monkeypatch):
    from app.llm.adapter import LLMAdapterError

    async def broken_chat_completion(self, messages):
        raise LLMAdapterError("simulated auth failure")

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", broken_chat_completion)

    response = client.post("/console/llm-test")

    assert response.status_code == 200  # not 502 — this endpoint reports failure as data
    body = response.json()
    assert body["ok"] is False
    assert "unavailable" in body["error"].lower() or "failure" in body["error"].lower()


# --- LLMAdapter unit tests -------------------------------------------------


def test_llm_adapter_configure_and_describe_never_expose_the_key():
    from app.config import Settings
    from app.llm.adapter import LLMAdapter

    adapter = LLMAdapter(Settings())
    adapter.configure(model="custom-model", api_base="https://example/v1", api_key="sk-marker-value")

    described = adapter.describe()
    assert described == {
        "model": "custom-model",
        "api_base": "https://example/v1",
        "api_version": None,
        "preset_id": None,
        "has_api_key": True,
    }
    assert "sk-marker-value" not in str(described)


async def test_llm_adapter_auto_prefixes_bare_model_when_api_base_is_set(monkeypatch):
    from app.config import Settings
    from app.llm.adapter import LLMAdapter

    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Choice:
            class message:
                content = "ok"
        class _Response:
            choices = [_Choice()]
            usage = None
        return _Response()

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    adapter = LLMAdapter(Settings())
    adapter.configure(model="qwen3.5-9b", api_base="https://example/v1", api_key="sk-marker")
    await adapter.chat_completion([{"role": "user", "content": "hi"}])

    assert captured["model"] == "openai/qwen3.5-9b"
    assert captured["api_base"] == "https://example/v1"
    assert captured["api_key"] == "sk-marker"


async def test_llm_adapter_passes_api_version_for_azure(monkeypatch):
    from app.config import Settings
    from app.llm.adapter import LLMAdapter

    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Choice:
            class message:
                content = "ok"
        class _Response:
            choices = [_Choice()]
            usage = None
        return _Response()

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    adapter = LLMAdapter(Settings())
    adapter.configure(
        model="azure/gpt-4.1-nano",
        api_base="https://ezazopenai.openai.azure.com",
        api_key="sk-marker",
        api_version="2025-01-01-preview",
    )
    await adapter.chat_completion([{"role": "user", "content": "hi"}])

    assert captured["model"] == "azure/gpt-4.1-nano"
    assert captured["api_version"] == "2025-01-01-preview"


async def test_llm_adapter_does_not_double_prefix_an_already_prefixed_model(monkeypatch):
    from app.config import Settings
    from app.llm.adapter import LLMAdapter

    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Choice:
            class message:
                content = "ok"
        class _Response:
            choices = [_Choice()]
            usage = None
        return _Response()

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)

    adapter = LLMAdapter(Settings())
    adapter.configure(model="azure/gpt-4.1-mini", api_base="https://example/v1")
    await adapter.chat_completion([{"role": "user", "content": "hi"}])

    assert captured["model"] == "azure/gpt-4.1-mini"

