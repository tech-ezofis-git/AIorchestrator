"""
The Qualifier's knowledge base — directly backed by PostgreSQL (ezofis_catalog_new.public.ftl_catalog)
using pgvector cosine similarity search and structured catalog columns.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI, AzureOpenAI

logger = logging.getLogger("orchestrator.ftl.qualifier.pricelist_store")

CATALOG_DB_URL = os.getenv("CATALOG_DATABASE_URL")
EMBEDDING_MODEL = (
    os.getenv("QUALIFIER_EMBEDDING_MODEL")
    or os.getenv("EMBEDDING_MODEL")
    or "text-embedding-3-small"
).strip()
EMBED_BATCH_SIZE = 64

_client: Optional[Union[OpenAI, AzureOpenAI]] = None


def _openai_client() -> Union[OpenAI, AzureOpenAI]:
    global _client
    if _client is None:
        azure_ep = os.getenv("AZURE_OPENAI_ENDPOINT", "https://ezazopenai.openai.azure.com/")
        azure_key = (
            os.getenv("AZURE_OPENAI_API_KEY")
            or os.getenv("AZURE_SOUTH_INDIA_API_KEY")
            or os.getenv("AZURE_EAST_US_API_KEY")
        )
        openai_key = os.getenv("OPENAI_API_KEY")

        if openai_key and openai_key.startswith("sk-"):
            _client = OpenAI(api_key=openai_key)
        elif azure_ep and (azure_key or (openai_key and not openai_key.startswith("sk-"))):
            api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
            _client = AzureOpenAI(
                azure_endpoint=azure_ep,
                api_key=azure_key or openai_key,
                api_version=api_ver,
            )
        elif openai_key:
            _client = OpenAI(api_key=openai_key)
        elif azure_key:
            api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
            _client = AzureOpenAI(
                azure_endpoint=azure_ep,
                api_key=azure_key,
                api_version=api_ver,
            )
        else:
            _client = OpenAI()
    return _client


def _get_db_conn():
    if not CATALOG_DB_URL:
        raise ValueError("CATALOG_DATABASE_URL environment variable is missing in .env")
    return psycopg2.connect(CATALOG_DB_URL)


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    client = _openai_client()
    out: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        out.extend([d.embedding for d in resp.data])
    return out


def search_pricelist(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    """Search ezofis_catalog_new.public.ftl_catalog using pgvector cosine similarity."""
    query = (query or "").strip()
    if not query:
        return []

    try:
        query_embs = embed_texts([query])
        if not query_embs:
            return []
        query_emb = query_embs[0]
        emb_str = "[" + ",".join(str(x) for x in query_emb) + "]"
    except Exception as exc:
        logger.exception("Failed to embed search query: %s", exc)
        return []

    sql = """
        SELECT 
            product_code,
            category,
            type,
            oem,
            door_hand,
            door_width,
            description,
            unit_price,
            currency,
            search_text,
            1 - (embedding <=> %s::vector) AS score
        FROM public.ftl_catalog
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    try:
        with _get_db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (emb_str, emb_str, top_k))
                rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            pcode = str(r.get("product_code") or "")
            desc = str(r.get("description") or "")
            cat = str(r.get("category") or "")
            typ = str(r.get("type") or "")
            oem = str(r.get("oem") or "")
            hand = str(r.get("door_hand") or "")
            width_raw = r.get("door_width")
            width: Optional[float] = float(width_raw) if width_raw is not None else None
            price: float = float(r.get("unit_price") or 0.0)
            curr = str(r.get("currency") or "CAD")
            score: float = float(r.get("score") or 0.0)

            # Build a human & model readable block matching catalog format
            specs = []
            if typ:
                specs.append(f"Type: {typ}")
            if oem:
                specs.append(f"OEM: {oem}")
            if hand:
                specs.append(f"Hand: {hand}")
            if width is not None:
                specs.append(f"Width: {int(width) if width.is_integer() else width}\"")
            specs_str = " | ".join(specs)

            formatted_text = f"Product Code: {pcode}\nDescription: {desc}\nCategory: {cat}"
            if specs_str:
                formatted_text += f"\nSpecs: {specs_str}"
            formatted_text += f"\nCatalogue Price: ${price:,.2f} {curr}"

            results.append({
                "text": formatted_text,
                "product_code": pcode,
                "description": desc,
                "category": cat,
                "type": typ,
                "oem": oem,
                "door_hand": hand,
                "door_width": width,
                "unit_price": price,
                "currency": curr,
                "page": 1,
                "score": score,
            })

        return results
    except Exception as exc:
        logger.exception("Failed to search ftl_catalog table: %s", exc)
        return []


def status() -> Dict[str, Any]:
    """Return the status of public.ftl_catalog in ezofis_catalog_new."""
    try:
        with _get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM public.ftl_catalog;")
                row = cur.fetchone()
                total_chunks = row[0] if row else 0
        return {
            "source_name": "ezofis_catalog_new.public.ftl_catalog",
            "pages_indexed": 1,
            "total_chunks": total_chunks,
            "indexed": total_chunks > 0,
        }
    except Exception as exc:
        logger.exception("Failed to get ftl_catalog status: %s", exc)
        return {
            "source_name": "ezofis_catalog_new.public.ftl_catalog",
            "pages_indexed": 0,
            "total_chunks": 0,
            "indexed": False,
        }


def reindex_pricelist(pages: Any = None, source_name: str = "wittur-pricelist") -> Dict[str, Any]:
    """Compatibility hook for reindexing. Returns current database status."""
    return status()
