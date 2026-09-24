"""FTL uses the same preset key path as the other agents."""
from app.ftl.llm import resolve_llm_config


def test_resolve_llm_config_uses_default_preset_key(monkeypatch):
    monkeypatch.setenv("AZURE_SOUTH_INDIA_API_KEY", "south-india-test-key")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        config = resolve_llm_config(None)
    finally:
        get_settings.cache_clear()
    assert config["model"] == "azure/gpt-5-nano"
    assert config["api_key"] == "south-india-test-key"
    assert "ezazopenai" in config["api_base"]


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
