"""Catalog console APIs: agents, models (URL/key), per-tenant model mapping."""


def test_list_catalog_agents_seeds_builtins(client):
    response = client.get("/console/catalog/agents")

    assert response.status_code == 200
    slugs = [row["slug"] for row in response.json()["agents"]]
    assert slugs == ["ap", "chat", "forecast", "insight", "mail", "ocr", "prompt", "search", "summary"]
    assert all(row["kind"] == "builtin" for row in response.json()["agents"])


def test_create_custom_agent_and_chat_with_intent(client, monkeypatch):
    captured = []

    async def fake_chat_completion(self, messages):
        captured.append(messages)
        return {"content": "policy-ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    created = client.post(
        "/console/catalog/agents",
        json={
            "slug": "hr-policy",
            "name": "HR Policy",
            "description": "HR questions",
            "system_prompt": "You are the HR policy agent. Reply with exactly: policy-ok",
            "trigger_phrases": ["leave policy", "pto"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["slug"] == "hr-policy"
    assert body["kind"] == "custom"
    assert "system_prompt" in body

    listed = client.get("/console/catalog/agents")
    slugs = [row["slug"] for row in listed.json()["agents"]]
    assert "hr-policy" in slugs

    chat = client.post(
        "/chat",
        json={"session_id": "s-catalog-custom", "message": "What is PTO?", "intent": "hr-policy"},
    )
    assert chat.status_code == 200
    assert chat.json()["reply"] == "policy-ok"
    assert captured
    assert captured[0][0]["role"] == "system"
    assert "HR policy agent" in captured[0][0]["content"]


def test_custom_agent_trigger_phrase_routes_without_explicit_intent(client, monkeypatch):
    async def fake_chat_completion(self, messages):
        return {"content": "triggered", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    client.post(
        "/console/catalog/agents",
        json={
            "slug": "vendor-faq",
            "name": "Vendor FAQ",
            "system_prompt": "Answer vendor questions.",
            "trigger_phrases": ["vendor onboarding"],
        },
    )

    chat = client.post(
        "/chat",
        json={"session_id": "s-catalog-trigger", "message": "Tell me about vendor onboarding please"},
    )
    assert chat.status_code == 200
    assert chat.json()["reply"] == "triggered"


def test_cannot_create_agent_with_builtin_slug(client):
    response = client.post(
        "/console/catalog/agents",
        json={"slug": "ocr", "name": "Fake OCR", "system_prompt": "nope"},
    )
    assert response.status_code == 400


def test_cannot_delete_builtin_agent(client):
    agents = client.get("/console/catalog/agents").json()["agents"]
    chat = next(row for row in agents if row["slug"] == "chat")
    response = client.delete(f"/console/catalog/agents/{chat['id']}")
    assert response.status_code == 404


def test_catalog_models_seeded_without_leaking_keys(client):
    response = client.get("/console/catalog/models")

    assert response.status_code == 200
    models = response.json()["models"]
    slugs = [row["slug"] for row in models]
    assert slugs == ["ezofis-gpu-box", "gpt-4.1-nano", "gpt-5-nano", "gpt-4.1-mini", "gpt-4o-mini"]
    assert all("api_key" not in row or row.get("api_key") in (None, "") for row in models)
    assert "test-south-india-key" not in response.text
    assert "test-qwen-mac-key" not in response.text
    assert models[0]["has_api_key"] is True


def test_create_and_patch_model_never_returns_key(client):
    created = client.post(
        "/console/catalog/models",
        json={
            "slug": "local-qwen",
            "label": "local-qwen",
            "model": "openai/qwen3.5-9b",
            "api_base": "http://127.0.0.1:8080/v1",
            "api_key": "sk-catalog-secret-marker",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["slug"] == "local-qwen"
    assert body["has_api_key"] is True
    assert body["api_key_last4"] == "rker"
    assert "sk-catalog-secret-marker" not in created.text

    patched = client.patch(
        f"/console/catalog/models/{body['id']}",
        json={"label": "Local Qwen", "api_key": ""},
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "Local Qwen"
    assert patched.json()["has_api_key"] is True
    assert "sk-catalog-secret-marker" not in patched.text


def test_duplicate_model_slug_conflict(client):
    response = client.post(
        "/console/catalog/models",
        json={"slug": "gpt-4o-mini", "label": "dup", "model": "azure/gpt-4o-mini"},
    )
    assert response.status_code == 409


def test_tenant_model_upsert_is_admin_only_not_used_by_chat(client, monkeypatch):
    models = client.get("/console/catalog/models").json()["models"]
    default_id = models[0]["id"]
    fallback_id = models[1]["id"]

    saved = client.put(
        "/console/catalog/tenant-models",
        json={
            "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
            "default_model_id": default_id,
            "fallback_model_id": fallback_id,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["tenant_id"] == "2e3b7b37-38a3-4f94-878e-a006dad93230"
    assert saved.json()["default_model_id"] == default_id

    listed = client.get("/console/catalog/tenant-models")
    assert listed.status_code == 200
    assert listed.json()["tenant_models"][0]["default_slug"] == models[0]["slug"]

    # Chat with a tenant_id still uses the process-global adapter (admin mapping only).
    captured = []

    async def fake_chat_completion(self, messages):
        captured.append(getattr(self, "_preset_id", None))
        return {"content": "ok", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    chat = client.post(
        "/chat",
        json={
            "session_id": "s-tenant-models",
            "message": "hello",
            "payload": {"tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230"},
        },
    )
    assert chat.status_code == 200
    # Global console default is still ezofis-gpu-box — tenant mapping is storage only.
    assert captured == ["ezofis-gpu-box"]


def test_unknown_intent_still_400_when_not_a_catalog_agent(client):
    response = client.post(
        "/chat",
        json={"session_id": "s-unknown-intent", "message": "hi", "intent": "not-a-real-agent"},
    )
    assert response.status_code == 400
    assert "Unknown intent" in response.json()["detail"]
