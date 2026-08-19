"""Normalize CATALOG_DATABASE_URL for asyncpg (Azure SSL, ADO.NET extras)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def catalog_pool_kwargs(url: str) -> dict:
    """ssl=True for Azure / sslmode=require. asyncpg does not honor ADO.NET flags."""
    lowered = url.lower()
    if "sslmode=require" in lowered or "azure.com" in lowered or "ssl=true" in lowered:
        return {"ssl": True}
    return {}


def normalize_catalog_url(url: str) -> str:
    """Drop query keys asyncpg does not understand (sslmode is applied via ssl=)."""
    parsed = urlparse(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in {"sslmode", "pooling", "command timeout", "command_timeout"}
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))
