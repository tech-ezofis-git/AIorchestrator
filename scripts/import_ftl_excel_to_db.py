"""
Import FTL Wittur Excel Pricelist into ezofis_catalog_new database (public.ftl_catalog table)
with pgvector embeddings (1536-dim) for hybrid semantic + structured SQL search.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

import asyncpg
import numpy as np
import pandas as pd
from openai import OpenAI, AzureOpenAI

EXCEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "FTL_Wittur_2026_Pricelist.xlsx")
CATALOG_DB_URL = os.getenv("CATALOG_DATABASE_URL")
EMBEDDING_MODEL = (
    os.getenv("QUALIFIER_EMBEDDING_MODEL")
    or os.getenv("EMBEDDING_MODEL")
    or "text-embedding-3-small"
).strip()
EMBED_BATCH_SIZE = 32


def get_openai_client() -> Union[OpenAI, AzureOpenAI]:
    azure_ep = os.getenv("AZURE_OPENAI_ENDPOINT", "https://ezazopenai.openai.azure.com/")
    azure_key = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or os.getenv("AZURE_SOUTH_INDIA_API_KEY")
        or os.getenv("AZURE_EAST_US_API_KEY")
    )
    openai_key = os.getenv("OPENAI_API_KEY")

    if openai_key and openai_key.startswith("sk-"):
        return OpenAI(api_key=openai_key)
    elif azure_ep and (azure_key or (openai_key and not openai_key.startswith("sk-"))):
        api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        return AzureOpenAI(
            azure_endpoint=azure_ep,
            api_key=azure_key or openai_key,
            api_version=api_ver,
        )
    elif openai_key:
        return OpenAI(api_key=openai_key)
    elif azure_key:
        api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        return AzureOpenAI(
            azure_endpoint=azure_ep,
            api_key=azure_key,
            api_version=api_ver,
        )
    else:
        return OpenAI()


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    client = get_openai_client()
    out: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        out.extend([d.embedding for d in resp.data])
    return out


def clean_str(val: Any) -> Optional[str]:
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    s = re.sub(r'[\ufffd\x80-\xff]', '-', s)
    return s.strip()


def clean_num(val: Any) -> Optional[float]:
    if pd.isna(val) or val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").strip()
        return float(val)
    except (ValueError, TypeError):
        return None


async def run_import():
    if not os.path.exists(EXCEL_PATH):
        raise FileNotFoundError(f"Excel file not found at: {EXCEL_PATH}")
    if not CATALOG_DB_URL:
        raise ValueError("CATALOG_DATABASE_URL environment variable is missing in .env")

    print(f"Reading Excel: {EXCEL_PATH}")
    df = pd.read_excel(EXCEL_PATH, sheet_name="Pricelist")
    print(f"Loaded {len(df)} rows from Excel.")

    rows_data: List[Dict[str, Any]] = []
    texts_to_embed: List[str] = []

    for idx, row in df.iterrows():
        cat = clean_str(row.get("Product Category")) or ""
        typ = clean_str(row.get("Type")) or ""
        oem = clean_str(row.get("O.E.M.")) or ""
        pname = clean_str(row.get("Product Name")) or ""
        hand = clean_str(row.get("Door Hand")) or ""
        desc = clean_str(row.get("Description")) or ""
        width = clean_num(row.get("Door Width"))
        price = clean_num(row.get("Catalogue Price (CAD)")) or 0.0

        if not pname:
            pname = f"ITEM_ROW_{idx+1}"

        # Construct a rich text representation for semantic search
        parts = []
        if cat:
            parts.append(f"Category: {cat}")
        if pname:
            parts.append(f"Product Code: {pname}")
        if desc:
            parts.append(f"Description: {desc}")
        if typ:
            parts.append(f"Type: {typ}")
        if oem:
            parts.append(f"OEM: {oem}")
        if hand:
            parts.append(f"Hand: {hand}")
        if width:
            parts.append(f"Door Width: {int(width) if width.is_integer() else width} inch")
        if price:
            parts.append(f"Price: ${price:.2f} CAD")

        search_text = " | ".join(parts)
        texts_to_embed.append(search_text)

        rows_data.append({
            "product_code": pname,
            "category": cat,
            "type": typ,
            "oem": oem,
            "door_hand": hand,
            "door_width": width,
            "description": desc,
            "unit_price": price,
            "currency": "CAD",
            "search_text": search_text,
            "raw_metadata": json.dumps({
                "excel_row": idx + 2,
                "category": cat,
                "type": typ,
                "oem": oem,
                "door_hand": hand,
                "door_width": width,
                "catalogue_price_cad": price,
            })
        })

    print(f"Generating vector embeddings ({EMBEDDING_MODEL}) for {len(texts_to_embed)} items...")
    embeddings = embed_texts(texts_to_embed)
    print(f"Generated {len(embeddings)} embeddings (dim={len(embeddings[0]) if embeddings else 0}).")

    print(f"Connecting to ezofis_catalog_new database...")
    conn = await asyncpg.connect(CATALOG_DB_URL)
    try:
        db_name = await conn.fetchval("SELECT current_database()")
        schema_name = await conn.fetchval("SELECT current_schema()")
        print(f"Connected to DB: '{db_name}', Schema: '{schema_name}'")

        # 1. Enable pgvector extension
        print("Ensuring pgvector extension is enabled...")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")

        # 2. Create ftl_catalog table
        print("Creating table public.ftl_catalog...")
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS public.ftl_catalog (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_code TEXT NOT NULL,
            category TEXT,
            type TEXT,
            oem TEXT,
            door_hand TEXT,
            door_width NUMERIC,
            description TEXT,
            unit_price NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
            currency TEXT NOT NULL DEFAULT 'CAD',
            search_text TEXT,
            raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            embedding VECTOR(1536),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ftl_catalog_product_code_uq UNIQUE (product_code)
        );
        """
        await conn.execute(create_table_sql)

        # 3. Create indices
        print("Creating indices...")
        await conn.execute("CREATE INDEX IF NOT EXISTS ftl_catalog_product_code_idx ON public.ftl_catalog(product_code);")
        await conn.execute("CREATE INDEX IF NOT EXISTS ftl_catalog_category_idx ON public.ftl_catalog(category);")
        await conn.execute("CREATE INDEX IF NOT EXISTS ftl_catalog_embedding_hnsw_idx ON public.ftl_catalog USING hnsw (embedding vector_cosine_ops);")

        await conn.execute("TRUNCATE TABLE public.ftl_catalog;")

        # 4. Insert / Upsert rows
        print("Upserting rows with vector embeddings into public.ftl_catalog...")
        upsert_sql = """
        INSERT INTO public.ftl_catalog (
            product_code, category, type, oem, door_hand, door_width,
            description, unit_price, currency, search_text, raw_metadata,
            embedding, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11::jsonb,
            $12::vector, now()
        )
        ON CONFLICT (product_code) DO UPDATE SET
            category = EXCLUDED.category,
            type = EXCLUDED.type,
            oem = EXCLUDED.oem,
            door_hand = EXCLUDED.door_hand,
            door_width = EXCLUDED.door_width,
            description = EXCLUDED.description,
            unit_price = EXCLUDED.unit_price,
            currency = EXCLUDED.currency,
            search_text = EXCLUDED.search_text,
            raw_metadata = EXCLUDED.raw_metadata,
            embedding = EXCLUDED.embedding,
            updated_at = now();
        """

        for item, emb in zip(rows_data, embeddings):
            emb_str = f"[{','.join(f'{x:.8f}' for x in emb)}]"
            await conn.execute(
                upsert_sql,
                item["product_code"],
                item["category"],
                item["type"],
                item["oem"],
                item["door_hand"],
                item["door_width"],
                item["description"],
                item["unit_price"],
                item["currency"],
                item["search_text"],
                item["raw_metadata"],
                emb_str,
            )

        total_count = await conn.fetchval("SELECT count(*) FROM public.ftl_catalog")
        print(f"Successfully upserted into public.ftl_catalog! Total rows in table: {total_count}")

        # 5. Run test search query
        test_query = "42 inch center opening door operator"
        print(f"\n--- Testing Semantic Search for: '{test_query}' ---")
        q_emb = embed_texts([test_query])[0]
        q_emb_str = f"[{','.join(f'{x:.8f}' for x in q_emb)}]"

        search_sql = """
        SELECT 
            product_code, category, description, unit_price, currency,
            1 - (embedding <=> $1::vector) AS similarity_score
        FROM public.ftl_catalog
        ORDER BY embedding <=> $1::vector
        LIMIT 5;
        """
        results = await conn.fetch(search_sql, q_emb_str)
        for r in results:
            print(f"[{r['similarity_score']:.4f}] {r['product_code']} - {r['description']} | ${r['unit_price']:.2f} {r['currency']}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_import())
