"""FTL uses the same preset key path as the other agents."""
from app.ftl.llm import resolve_llm_config


def _fresh_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings


def test_resolve_llm_config_uses_default_preset_key(monkeypatch):
    get_settings = _fresh_settings(
        monkeypatch,
        QWEN_MAC_API_KEY="gpu-test-key",
        QWEN_MAC_API_BASE="http://gpu-box.test:8080/v1",
    )
    try:
        config = resolve_llm_config(None)
    finally:
        get_settings.cache_clear()
    assert config["model"] == "openai/qwen3.5-9b"
    assert config["api_key"] == "gpu-test-key"
    assert config["api_base"] == "http://gpu-box.test:8080/v1"


def test_explicit_overrides_win():
    config = resolve_llm_config(
        {
            "model": "azure/gpt-4.1-mini",
            "api_base": "https://example.openai.azure.com",
            "api_key": "tenant-key",
            "api_version": "2025-01-01-preview",
        }
    )
    assert config["model"] == "azure/gpt-4.1-mini"
    assert config["api_key"] == "tenant-key"


def test_chosen_preset_uses_its_own_endpoint_and_key(monkeypatch):
    get_settings = _fresh_settings(
        monkeypatch,
        QWEN_MAC_API_KEY="gpu-test-key",
        QWEN_MAC_API_BASE="http://gpu-box.test:8080/v1",
        AZURE_SOUTH_INDIA_API_KEY="south-india-test-key",
    )
    try:
        # The console sends the preset id as `model` on top of the frozen default snapshot.
        config = resolve_llm_config(
            {
                "model": "ezofis-gpu-box",
                "api_base": "https://ezazopenai.openai.azure.com",
                "api_key": "south-india-test-key",
                "api_version": "2025-01-01-preview",
            }
        )
        azure = resolve_llm_config({"model": "gpt-4.1-mini"})
    finally:
        get_settings.cache_clear()
    assert config["model"] == "openai/qwen3.5-9b"
    assert config["api_base"] == "http://gpu-box.test:8080/v1"
    assert config["api_key"] == "gpu-test-key"
    assert azure["model"] == "azure/gpt-4.1-mini"
    assert azure["api_key"] == "south-india-test-key"
    assert "ezazopenai" in azure["api_base"]


async def test_adapter_resolves_a_preset_id_model(monkeypatch):
    get_settings = _fresh_settings(
        monkeypatch,
        QWEN_MAC_API_KEY="gpu-test-key",
        QWEN_MAC_API_BASE="http://gpu-box.test:8080/v1",
    )
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
    try:
        adapter = LLMAdapter(Settings())
        adapter.configure(model="azure/gpt-5-nano", api_base="https://default/v1", api_key="default-key")
        await adapter.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="ezofis-gpu-box",
            api_base="https://default/v1",
            api_key="default-key",
        )
    finally:
        get_settings.cache_clear()
    assert captured["model"] == "openai/qwen3.5-9b"
    assert captured["api_base"] == "http://gpu-box.test:8080/v1"
    assert captured["api_key"] == "gpu-test-key"
