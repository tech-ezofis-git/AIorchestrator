"""
The Qualifier's knowledge base — the Wittur pricelist PDF, chunked and embedded, stored as one
local JSON file (data/pricelist_index.json). No database: brute-force cosine similarity over an
in-memory numpy array is plenty fast for a pricelist-sized document, and this is a standalone
single-user tool, not a multi-tenant service.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

import numpy as np
from openai import OpenAI, AzureOpenAI

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INDEX_PATH = os.path.join(DATA_DIR, "pricelist_index.json")

CHUNK_WORDS = 280
CHUNK_OVERLAP_WORDS = 40
EMBED_BATCH_SIZE = 64
EMBEDDING_MODEL = (
    os.getenv("QUALIFIER_EMBEDDING_MODEL")
    or os.getenv("EMBEDDING_MODEL")
    or "text-embedding-3-small"
).strip()

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


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def chunk_text(text: str, max_words: int = CHUNK_WORDS, overlap_words: int = CHUNK_OVERLAP_WORDS) -> List[str]:
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    chunks: List[str] = []
    start = 0
    step = max(1, max_words - overlap_words)
    while start < len(words):
        chunks.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
        start += step
    return chunks


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


def load_index() -> Dict[str, Any]:
    if not os.path.exists(INDEX_PATH):
        return {"source_name": "", "pages": []}  # pages: [{page, hash, chunks: [{text, embedding}]}]
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {"source_name": "", "pages": []}
    except Exception:
        return {"source_name": "", "pages": []}


def save_index(index: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f)


def reindex_pricelist(pages: List[str], source_name: str = "wittur-pricelist") -> Dict[str, Any]:
    """(Re)index the pricelist, per-PDF-page — mirrors the page-granular approach that worked well
    in testing (pages are already self-contained catalog tables). Skips unchanged pages by hash."""
    index = load_index()
    old_by_page = {p["page"]: p for p in index.get("pages", [])}
    new_pages: List[Dict[str, Any]] = []
    processed = 0

    for i, page_text in enumerate(pages):
        page_text = (page_text or "").strip()
        if not page_text:
            continue
        h = content_hash(page_text)
        existing = old_by_page.get(i + 1)
        if existing and existing.get("hash") == h:
            new_pages.append(existing)
            continue
        chunks = chunk_text(page_text)
        embeddings = embed_texts(chunks)
        new_pages.append(
            {
                "page": i + 1,
                "hash": h,
                "chunks": [{"text": c, "embedding": e} for c, e in zip(chunks, embeddings)],
            }
        )
        processed += 1

    index = {"source_name": source_name, "pages": new_pages}
    save_index(index)
    total_chunks = sum(len(p["chunks"]) for p in new_pages)
    return {"ok": True, "pages_indexed": len(new_pages), "pages_reembedded": processed, "total_chunks": total_chunks}


def search_pricelist(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    index = load_index()
    all_chunks: List[Dict[str, Any]] = []
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            all_chunks.append({"text": c["text"], "embedding": c["embedding"], "page": page["page"]})
    if not all_chunks:
        return []

    try:
        query_emb = embed_texts([query])[0]
    except Exception:
        return []

    mat = np.array([c["embedding"] for c in all_chunks], dtype=np.float32)
    q = np.array(query_emb, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []
    mat_norms = np.linalg.norm(mat, axis=1)
    sims = (mat @ q) / (mat_norms * q_norm + 1e-8)

    k = min(top_k, len(all_chunks))
    top_idx = np.argsort(-sims)[:k]
    return [
        {"text": all_chunks[i]["text"], "page": all_chunks[i]["page"], "score": float(sims[i])}
        for i in top_idx
    ]


def status() -> Dict[str, Any]:
    index = load_index()
    pages = index.get("pages", [])
    total_chunks = sum(len(p.get("chunks", [])) for p in pages)
    return {
        "source_name": index.get("source_name", ""),
        "pages_indexed": len(pages),
        "total_chunks": total_chunks,
        "indexed": bool(pages),
    }
