"""Redis-backed, TTL-bound, model-aware response cache (Phase 5b) — used by
Search (query embedding + full result) and Forecast (narration only). See
app/agents/search_agent.py / app/agents/forecast_agent.py for how each
capability wires this in.

Deliberately NOT used anywhere else. AP/Summary/Insight are excluded on
purpose, not by oversight: their underlying data is only "stable" today
because it's mocked (app/integrations/ezofis_client.py) — caching them now
would quietly become a staleness bug the moment a real EZOFIS integration
replaces the mocks with genuinely time-varying data, and nothing about the
call site would look wrong. Chat/OCR/Mail are excluded because their input
shapes have low exact-match cache-hit likelihood in the first place (free-
form conversation, per-scan OCR references, per-request email drafts) —
caching them would mostly just spend Redis calls for near-zero hit rate.

Failure discipline: caching is a pure performance optimization here, never
a correctness dependency (unlike Phase 5a's memory store, which has a
genuine fail-loud write). BOTH get() and set() fail soft — a Redis outage
during a lookup or a write is logged as a warning and treated as a
miss/no-op. Caching must never be the reason a request fails (rule 7).

TTL is checked twice, deliberately:
  - Redis's own key expiry (`ex=ttl_seconds`) is the eventual-cleanup
    backstop, in real wall-clock time.
  - An `expires_at` timestamp is also stored inside the cached envelope,
    computed from an *injectable* clock (defaults to time.time, same
    pattern as app/control/rate_limiter.py) and checked on every get().
    This second check is what actually governs hit-vs-miss — it's what
    makes TTL expiry deterministically testable (advance a fake clock, no
    real sleeping) without depending on Redis's own real-time expiry,
    which a test can't control precisely.

No cache-invalidation-on-ingestion mechanism exists in this phase (rule
8) — a short TTL on the result/narration caches is the entire staleness
mitigation, an accepted tradeoff, not an oversight. The embedding cache's
TTL is long instead, since embedding a given text under a given model is a
pure function — there's nothing about it that becomes stale over time.

Phase 5d: every `get()` outcome (hit, or any flavor of miss — never
cached, corrupt, expired, or a lookup failure) is also recorded to
app/control/metrics.py, right alongside the log line that already existed
for it — see that module's docstring for the full instrumentation-point
reasoning. This is additive; none of get()/set()'s behavior changes.
"""
import hashlib
import json
import logging
import time
from typing import Any, Callable, Optional, Protocol

from app.control.metrics import record_cache_event

logger = logging.getLogger("orchestrator.response_cache")

_KEY_PREFIX = "orchestrator:cache"


class RedisLike(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, ex: Optional[int] = None) -> Any: ...


def _cache_key(prefix: str, model: str, input_text: str) -> str:
    """A namespaced hash of prefix + model + input text (rule 5 — the
    model identifier is folded into the key itself, not just stored
    alongside it, so a model swap via env var naturally misses old
    entries instead of ever risking a stale cross-model hit)."""
    digest = hashlib.sha256(f"{model}:{input_text}".encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}:{prefix}:{digest}"


class ResponseCache:
    """Generic get/set over Redis, namespaced by an entry-kind `prefix`
    (e.g. "embedding", "search_result", "forecast_narration") — exact-match
    only, no semantic/fuzzy matching (explicitly out of scope this phase).
    """

    def __init__(self, redis_client: RedisLike, *, clock: Callable[[], float] = time.time):
        self._redis = redis_client
        self._clock = clock

    async def get(self, *, prefix: str, model: str, input_text: str) -> Optional[Any]:
        """Returns the cached value, or None on a miss — never cached,
        expired, corrupt, or (soft-fail) a Redis outage. Never raises."""
        key = _cache_key(prefix, model, input_text)
        try:
            raw = await self._redis.get(key)
        except Exception as exc:
            logger.warning("cache_lookup_failed", extra={"prefix": prefix, "error_type": type(exc).__name__})
            record_cache_event(cache_kind=prefix, hit=False)
            return None

        if raw is None:
            logger.info("cache_miss", extra={"prefix": prefix})
            record_cache_event(cache_kind=prefix, hit=False)
            return None

        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("cache_entry_corrupt", extra={"prefix": prefix})
            record_cache_event(cache_kind=prefix, hit=False)
            return None

        if envelope.get("expires_at", 0) <= self._clock():
            logger.info("cache_miss", extra={"prefix": prefix, "reason": "expired"})
            record_cache_event(cache_kind=prefix, hit=False)
            return None

        logger.info("cache_hit", extra={"prefix": prefix})
        record_cache_event(cache_kind=prefix, hit=True)
        return envelope.get("value")

    async def set(self, *, prefix: str, model: str, input_text: str, value: Any, ttl_seconds: int) -> None:
        """Best-effort write. A failure is logged and swallowed, never
        raised — losing a cache write only costs a future recompute, never
        correctness (rule 7 applies to writes too, not just lookups)."""
        key = _cache_key(prefix, model, input_text)
        envelope = {"value": value, "expires_at": self._clock() + ttl_seconds}
        try:
            await self._redis.set(key, json.dumps(envelope), ex=ttl_seconds)
        except Exception as exc:
            logger.warning("cache_write_failed", extra={"prefix": prefix, "error_type": type(exc).__name__})
