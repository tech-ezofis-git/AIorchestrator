"""Thin async wrapper around LiteLLM's embedding call.

Same pattern as app/llm/adapter.py (the Chat LLM adapter): the embedding
provider is selected purely via EMBEDDING_MODEL — no provider-specific
branching here or anywhere else in the app. LiteLLM reads the matching
provider API key (already set for LLM_MODEL) directly from the environment.

Provider failures are caught and re-raised as EmbeddingAdapterError, same
discipline as LLMAdapterError: safe to show a generic message to callers,
never leaks the provider's raw exception or a stack trace.
"""
import logging

import litellm

from app.config import Settings

logger = logging.getLogger("orchestrator.embeddings")


class EmbeddingAdapterError(Exception):
    """Raised when the configured embedding provider call fails for any
    reason. Safe to surface to API callers as-is — never wraps provider
    internals."""


class EmbeddingAdapter:
    def __init__(self, settings: Settings):
        self._model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one embedding vector per input,
        in the same order. Raises EmbeddingAdapterError on any provider
        failure. Never logs the input texts or the resulting vectors —
        only counts (see call sites in app/knowledge/)."""
        if not texts:
            return []
        try:
            response = await litellm.aembedding(model=self._model, input=texts)
        except Exception as exc:
            logger.warning(
                "embedding_call_failed",
                extra={"model": self._model, "error_type": type(exc).__name__, "input_count": len(texts)},
            )
            raise EmbeddingAdapterError("The embedding provider is currently unavailable.") from exc

        return [item["embedding"] for item in response.data]
