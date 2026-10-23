"""Primary -> fallback -> 2nd fallback LLM retry chain."""
from app.catalog.tenant_llm import chat_completion_with_fallbacks, fallback_override_chain, job_fallback_chain
from app.llm.adapter import LLMAdapterError


def test_fallback_override_chain_skips_empty_and_duplicate_models():
    chain = fallback_override_chain(
        {"model": "a"},
        [{"model": "a"}, {"model": "b"}, {}],
        None,
        {"model": "B"},
        {"model": "c"},
    )
    assert [item["model"] for item in chain] == ["a", "b", "c"]


def test_job_fallback_chain_reads_stored_second_fallback():
    chain = job_fallback_chain(
        {
            "llm_fallback_overrides": {"model": "fb1"},
            "llm_fallback2_overrides": {"model": "fb2"},
        }
    )
    assert [item["model"] for item in chain] == ["fb1", "fb2"]


async def test_chat_completion_runs_third_model_when_second_fallback_fails():
    calls = []

    class FakeLlm:
        async def chat_completion(self, messages, **kwargs):
            model = kwargs.get("model") or "primary"
            calls.append(model)
            if model != "third":
                raise LLMAdapterError(f"{model} failed")
            return {"content": "ok", "usage": None}

    result = await chat_completion_with_fallbacks(
        FakeLlm(),
        [{"role": "user", "content": "hi"}],
        primary={"model": "primary"},
        fallbacks=[{"model": "second"}, {"model": "third"}],
    )
    assert result["content"] == "ok"
    assert calls == ["primary", "second", "third"]
