"""Redis-backed, session-keyed rate limiter — the second gate in the
guardrail pipeline (after the free, stateless content filter; before the
existing pipeline's context load).

Algorithm: fixed window counter. Each session gets one counter key per
window (session_id + the window's start timestamp); the counter is
incremented via Redis INCR (atomic — the "one Redis check" this gate
needs to decide allow/deny) and compared against the configured
threshold, with an EXPIRE set only the first time a window's key is
created. A fixed window is simpler than a token bucket or a sliding-window
log; the accepted tradeoff is it allows up to ~2x the threshold for
requests clustered right around a window boundary — a documented
limitation for this phase, not a bug.

The clock is injectable (defaults to time.time) specifically so tests can
control window boundaries deterministically instead of sleeping across
real wall-clock time — see tests/test_rate_limiter.py.
"""
import logging
import time
from typing import Any, Callable, Protocol

logger = logging.getLogger("orchestrator.rate_limiter")

_KEY_PREFIX = "orchestrator:ratelimit"


class RateLimitExceededError(Exception):
    """Raised when a session has exceeded its request threshold for the
    current window. Carries `retry_after_seconds` so the caller can
    surface a Retry-After hint."""

    def __init__(self, retry_after_seconds: int):
        super().__init__("Rate limit exceeded.")
        self.retry_after_seconds = retry_after_seconds


class RateLimiterStoreUnavailableError(Exception):
    """Raised when Redis can't be reached. Same discipline as every other
    Redis-backed component in this app: fail explicit (503), never
    silently allow or silently block a request because the store was
    unreachable."""


class RedisLike(Protocol):
    async def incr(self, key: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> Any: ...


class RateLimiter:
    def __init__(
        self,
        redis_client: RedisLike,
        *,
        max_requests: int,
        window_seconds: int,
        clock: Callable[[], float] = time.time,
    ):
        self._redis = redis_client
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clock = clock

    def _window_start(self) -> int:
        return int(self._clock() // self._window_seconds) * self._window_seconds

    def _key(self, session_id: str) -> str:
        return f"{_KEY_PREFIX}:{session_id}:{self._window_start()}"

    async def check(self, session_id: str) -> None:
        """Raises RateLimitExceededError if `session_id` has exceeded its
        threshold for the current window. Returns None otherwise."""
        key = self._key(session_id)
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window_seconds)
        except Exception as exc:
            logger.warning("rate_limiter_store_failed", extra={"session_id": session_id})
            raise RateLimiterStoreUnavailableError("Rate limiter store is currently unavailable.") from exc

        if count > self._max_requests:
            retry_after = max(1, int(self._window_start() + self._window_seconds - self._clock()))
            logger.info(
                "rate_limit_exceeded",
                extra={"session_id": session_id, "count": count, "max_requests": self._max_requests},
            )
            raise RateLimitExceededError(retry_after_seconds=retry_after)
