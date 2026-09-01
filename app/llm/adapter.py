"""Thin async wrapper around LiteLLM.

LiteLLM gives us one call shape across OpenAI/Anthropic/Google/local models —
which provider is used is entirely determined by `settings.llm_model` (and
the matching API key in the environment). No code here is provider-specific.

Provider failures (timeouts, rate limits, network/auth errors, ...) are
caught here and re-raised as LLMAdapterError — a safe, generic error the
caller can show to API clients without leaking the provider's raw exception
(which can include request/response fragments) or a stack trace.

Custom endpoints + runtime reconfiguration: `api_base`/`api_key`/
`api_version` (from Settings at startup, or via `configure()` afterward)
route calls at a specific base URL with a specific key instead of a
standard provider's default routing — e.g. a self-hosted OpenAI-compatible
server, or classic Azure OpenAI (`azure/<deployment>` + `api_version` +
resource `api_base`). `configure()` exists specifically for the Test
Console (GET/POST /console/llm-config, app/main.py): every
agent/ResponseComposer already holds a reference to the ONE shared
LLMAdapter instance built in main.py's lifespan, so mutating it here
takes effect for every subsequent call with no rewiring needed and no
app restart.
"""
import asyncio
import logging
from typing import Optional

import litellm

from app.config import Settings, get_settings

logger = logging.getLogger("orchestrator.llm")


class LLMAdapterError(Exception):
    """Raised when the configured LLM provider call fails for any reason.
    Safe to surface to API callers as-is — never wraps provider internals."""


class LLMAdapter:
    def __init__(self, settings: Settings):
        self._model = settings.llm_model
        self._api_base = settings.llm_api_base or None
        self._api_key = settings.llm_api_key or None
        self._api_version: Optional[str] = None
        self._preset_id: Optional[str] = None

    def configure(
        self,
        *,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        preset_id: Optional[str] = None,
    ) -> None:
        """Runtime reconfiguration — see module docstring. Each argument
        only changes something if explicitly passed a non-None value;
        pass an empty string to explicitly CLEAR api_base/api_key/
        api_version/preset_id (e.g. switching back to a standard provider
        that reads its key from the environment). Never logs `api_key`'s
        value — only whether one is now set (see `describe()`)."""
        if model is not None and model != "":
            self._model = model
        if api_base is not None:
            self._api_base = api_base or None
        if api_key is not None:
            self._api_key = api_key or None
        if api_version is not None:
            self._api_version = api_version or None
        if preset_id is not None:
            self._preset_id = preset_id or None
        logger.info(
            "llm_adapter_reconfigured",
            extra={
                "model": self._model,
                "api_base": self._api_base,
                "api_version": self._api_version,
                "preset_id": self._preset_id,
                "has_api_key": bool(self._api_key),
            },
        )

    def snapshot_overrides(self) -> dict:
        """Current config as a `chat_completion(**overrides)`-shaped dict,
        INCLUDING the real `api_key` — internal server-side use only, never
        return this from an HTTP endpoint (use `describe()` for that).

        For a caller that isn't selecting a specific tenant/explicit model,
        this lets it freeze "whatever the adapter's process-wide default
        currently is" at the start of a request and pass it explicitly into
        every `chat_completion()` call that request makes, so those calls
        are immune to another concurrent request changing the shared
        adapter's default in between (see `chat_completion`'s docstring)."""
        return {
            "model": self._model,
            "api_base": self._api_base,
            "api_key": self._api_key,
            "api_version": self._api_version,
        }

    def describe(self) -> dict:
        """Current config, safe to return over the wire — never the
        `api_key` value itself, only whether one is set (used by GET
        /console/llm-config so the Test Console can show current state
        without ever echoing a secret back)."""
        return {
            "model": self._model,
            "api_base": self._api_base,
            "api_version": self._api_version,
            "preset_id": self._preset_id,
            "has_api_key": bool(self._api_key),
        }

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> dict:
        """Call the configured LLM with a list of {role, content} messages.

        `model`/`api_base`/`api_key`/`api_version` optionally override this
        adapter's configured defaults for THIS CALL ONLY — unlike
        `configure()`, they are never written to `self`. This is what lets
        per-tenant/per-request model selection (app/catalog/tenant_llm.py,
        app/ap_skills/*, app/agents/ocr_agent.py) work safely under
        concurrency: the one shared LLMAdapter instance's own state is never
        mutated mid-request, so two concurrent requests for two different
        tenants/presets can never see (or clobber) each other's model/key —
        each call resolves its own model/api_base/api_key/api_version
        independently from its own arguments, falling back to the adapter's
        process-wide default only when an override isn't passed. Pass ""
        (not None) for api_base/api_key/api_version to explicitly go
        keyless/base-less for this call even if the adapter default has one
        set (e.g. an Azure preset with no api_version).

        Returns {"content": str, "usage": dict | None}.
        Raises LLMAdapterError on any provider failure.
        """
        resolved_model = model if model is not None and model != "" else self._model
        resolved_api_base = api_base if api_base is not None else self._api_base
        resolved_api_key = api_key if api_key is not None else self._api_key
        resolved_api_version = api_version if api_version is not None else self._api_version

        # Classic Azure (`azure/...`) keeps its prefix. A custom api_base
        # with a bare model name (no "provider/" prefix) is routed through
        # LiteLLM's generic OpenAI-compatible client — exactly the wire
        # format a self-hosted server or Azure OpenAI's /openai/v1 surface
        # both speak (Bearer auth, model name in the body).
        model = resolved_model
        if resolved_api_base and "/" not in model:
            model = f"openai/{model}"

        kwargs = {"model": model, "messages": messages, "drop_params": True}
        if resolved_api_base:
            kwargs["api_base"] = resolved_api_base
        if resolved_api_key:
            kwargs["api_key"] = resolved_api_key
        if resolved_api_version:
            kwargs["api_version"] = resolved_api_version
        # GPT-5 / Azure reasoning deployments reject temperature and
        # max_tokens; LiteLLM maps completion tokens when this is set.
        if "gpt-5" in (model or "").lower():
            kwargs["max_completion_tokens"] = 4096

        timeout = get_settings().llm_request_timeout_seconds
        if timeout and timeout > 0:
            kwargs["timeout"] = timeout
            kwargs["request_timeout"] = timeout

        try:
            if timeout and timeout > 0:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=timeout,
                )
            else:
                response = await litellm.acompletion(**kwargs)
        except asyncio.TimeoutError as exc:
            logger.warning(
                "llm_call_timed_out",
                extra={"model": model, "api_base": resolved_api_base, "timeout_seconds": timeout},
            )
            raise LLMAdapterError(
                "The language model provider timed out. Try another model preset in the console."
            ) from exc
        except Exception as exc:
            # Log only the exception type + model — never the exception's
            # str()/args, which for auth errors can echo back request
            # headers or key fragments. api_base is safe to log (never a
            # secret); api_key never is and never appears here.
            logger.warning(
                "llm_call_failed",
                extra={"model": model, "api_base": resolved_api_base, "error_type": type(exc).__name__},
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
