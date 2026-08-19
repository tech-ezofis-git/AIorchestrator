"""Ezofis catalog (agents, LLM models, per-tenant model selection)."""

from app.catalog.defaults import BUILTIN_AGENTS, RESERVED_SLUGS
from app.catalog.store import CatalogStore, CatalogStoreUnavailableError

__all__ = [
    "BUILTIN_AGENTS",
    "RESERVED_SLUGS",
    "CatalogStore",
    "CatalogStoreUnavailableError",
]
