"""Normalize CATALOG_DATABASE_URL for asyncpg (Azure SSL, ADO.NET extras)."""
from __future__ import annotations

from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from app.data_import.ident import parse_connection_kv


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


def asyncpg_url_from_connection_string(conn_str: str) -> str:
    """ADO.NET or postgresql URL → asyncpg DSN (sslmode kept for catalog_pool_kwargs)."""
    raw = (conn_str or "").strip()
    if not raw:
        raise ValueError("Empty connection string")
    if "://" in raw:
        if raw.startswith("postgresql+psycopg2://"):
            raw = "postgresql://" + raw[len("postgresql+psycopg2://") :]
        elif raw.startswith("postgresql+asyncpg://"):
            raw = "postgresql://" + raw[len("postgresql+asyncpg://") :]
        elif raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://") :]
        return raw
    kv = parse_connection_kv(raw)
    host = kv.get("host") or kv.get("server") or kv.get("data source")
    port = kv.get("port") or "5432"
    database = kv.get("database") or kv.get("initial catalog")
    user = kv.get("username") or kv.get("user id") or kv.get("uid")
    password = kv.get("password") or kv.get("pwd") or ""
    if not all([host, database, user]):
        raise ValueError("Incomplete PostgreSQL connection string")
    sslmode = kv.get("sslmode") or kv.get("ssl mode")
    query = ""
    if sslmode:
        query = f"?sslmode={quote_plus(str(sslmode).strip())}"
    elif host and "postgres.database.azure.com" in host.lower():
        query = "?sslmode=require"
    return (
        f"postgresql://{quote_plus(str(user))}:{quote_plus(str(password))}"
        f"@{host}:{port}/{quote_plus(str(database))}{query}"
    )
