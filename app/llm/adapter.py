"""Thin async wrapper around LiteLLM.

LiteLLM gives us one call shape across OpenAI/Anthropic/Google/local models —
which provider is used is entirely determined by `settings.llm_model` (and
the matching API key in the environment). No code here is provider-specific.

Provider failures (timeouts, rate limits, network/auth errors, ...) are
caught here and re-raised as LLMAdapterError — a safe, generic error the
caller can show to API clients without leaking the provider's raw exception
(which can include request/response fragments) or a stack trace.

Custom endpoints + runtime reconfiguration: `api_base`/`api_key` (from
Settings at startup, or via `configure()` afterward) route calls at a
specific base URL with a specific key instead of a standard provider's
default routing — e.g. a self-hosted OpenAI-compatible server, or an
Azure OpenAI resource exposing its `/openai/v1` surface (Bearer auth,
`model` in the body — not the classic `api-key` header + `?api-version=`
Azure protocol). `configure()` exists specifically for the Test Console
(GET/POST /console/llm-config, app/main.py): every agent/ResponseComposer
already holds a reference to the ONE shared LLMAdapter instance built in
main.py's lifespan, so mutating it here takes effect for every subsequent
call with no rewiring needed and no app restart.
"""
import logging
from typing import Optional

import litellm

from app.config import Settings

logger = logging.getLogger("orchestrator.llm")


class LLMAdapterError(Exception):
    """Raised when the configured LLM provider call fails for any reason.
    Safe to surface to API callers as-is — never wraps provider internals."""


class LLMAdapter:
    def __init__(self, settings: Settings):
        self._model = settings.llm_model
        self._api_base = settings.llm_api_base or None
        self._api_key = settings.llm_api_key or None

    def configure(
        self, *, model: Optional[str] = None, api_base: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        """Runtime reconfiguration — see module docstring. Each argument
        only changes something if explicitly passed a non-None value;
        pass an empty string to explicitly CLEAR api_base/api_key (e.g.
        switching back to a standard provider that reads its key from the
        environment). Never logs `api_key`'s value — only whether one is
        now set (see `describe()`)."""
        if model is not None and model != "":
            self._model = model
        if api_base is not None:
            self._api_base = api_base or None
        if api_key is not None:
            self._api_key = api_key or None
        logger.info(
            "llm_adapter_reconfigured",
            extra={"model": self._model, "api_base": self._api_base, "has_api_key": bool(self._api_key)},
        )

    def describe(self) -> dict:
        """Current config, safe to return over the wire — never the
        `api_key` value itself, only whether one is set (used by GET
        /console/llm-config so the Test Console can show current state
        without ever echoing a secret back)."""
        return {"model": self._model, "api_base": self._api_base, "has_api_key": bool(self._api_key)}

    async def chat_completion(self, messages: list[dict[str, str]]) -> dict:
        """Call the configured LLM with a list of {role, content} messages.

        Returns {"content": str, "usage": dict | None}.
        Raises LLMAdapterError on any provider failure.
        """
        model = self._model
        # A custom api_base with a bare model name (no "provider/" prefix)
        # is routed through LiteLLM's generic OpenAI-compatible client —
        # exactly the wire format a self-hosted server or Azure OpenAI's
        # /openai/v1 surface both speak (Bearer auth, model name in the
        # body). A model the caller already prefixed (e.g. "azure/...",
        # "anthropic/...") is left alone.
        if self._api_base and "/" not in model:
            model = f"openai/{model}"

        kwargs = {"model": model, "messages": messages}
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._api_key:
            kwargs["api_key"] = self._api_key

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            # Log only the exception type + model — never the exception's
            # str()/args, which for auth errors can echo back request
            # headers or key fragments. api_base is safe to log (never a
            # secret); api_key never is and never appears here.
            logger.warning(
                "llm_call_failed",
                extra={"model": model, "api_base": self._api_base, "error_type": type(exc).__name__},
            )
            raise LLMAdapterError("The language model provider is currently unavailable.") from exc

        content = response.choices[0].message.content or ""

        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = {
                "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
                "completion_tokens": getattr(raw_usage, "completion_tokens", None),
                "total_tokens": getattr(raw_usage, "total_tokens", None),
            }

        return {"content": content, "usage": usage}
