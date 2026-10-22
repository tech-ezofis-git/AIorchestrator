"""Name-only embeddings for AP vendor and product comparisons.

Quantity, price, and amount stay on the numeric scorers. Vectors are kept
in memory for the run and are not written to the knowledge store.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from app.ap_skills.types import name_similarity, normalize_name

logger = logging.getLogger("orchestrator.ap.name_embedder")


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_l = 0.0
    norm_r = 0.0
    for a, b in zip(left, right):
        dot += a * b
        norm_l += a * a
        norm_r += b * b
    if norm_l <= 0 or norm_r <= 0:
        return 0.0
    value = dot / (math.sqrt(norm_l) * math.sqrt(norm_r))
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


class NameEmbedder:
    """Batch-embed name strings, then score pairs without another API call."""

    def __init__(self, adapter: Any = None):
        self._adapter = adapter
        self._vectors: dict[str, list[float]] = {}

    async def warm(self, texts: list[str]) -> None:
        if self._adapter is None:
            return
        missing: list[str] = []
        seen: set[str] = set()
        for raw in texts:
            text = str(raw or "").strip()
            if not text or text in self._vectors or text in seen:
                continue
            seen.add(text)
            missing.append(text)
        if not missing:
            return
        try:
            vectors = await self._adapter.embed(missing)
        except Exception as exc:
            logger.warning(
                "ap_name_embed_failed",
                extra={"error_type": type(exc).__name__, "count": len(missing)},
            )
            return
        if not isinstance(vectors, list) or len(vectors) != len(missing):
            logger.warning("ap_name_embed_short", extra={"count": len(missing)})
            return
        for text, vector in zip(missing, vectors):
            if isinstance(vector, list) and vector:
                self._vectors[text] = [float(v) for v in vector]

    def score(self, left: str, right: str) -> float:
        """0..1. Exact normalized names are 1. Otherwise cosine, else letters."""
        a = str(left or "").strip()
        b = str(right or "").strip()
        if not a or not b:
            return 0.0
        if normalize_name(a) == normalize_name(b):
            return 1.0
        left_vec = self._vectors.get(a)
        right_vec = self._vectors.get(b)
        if left_vec and right_vec:
            return _cosine(left_vec, right_vec)
        return name_similarity(a, b)


def embedder_from(ctx: Any) -> Optional[NameEmbedder]:
    embedder = getattr(ctx, "name_embedder", None)
    if isinstance(embedder, NameEmbedder):
        return embedder
    return None
