"""
The Qualifier's knowledge base — the Wittur pricelist PDF, chunked and embedded, stored as one
local JSON file (data/pricelist_index.json). No database: brute-force cosine similarity over an
in-memory numpy array is plenty fast for a pricelist-sized document, and this is a standalone
single-user tool, not a multi-tenant service.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
load_dotenv()

import numpy as np
from openai import OpenAI, AzureOpenAI

logger = logging.getLogger("orchestrator.ftl.qualifier.pricelist_store")

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
            _client = OpenAI(api_key=openai_key, timeout=8.0, max_retries=1)
        elif azure_ep and (azure_key or (openai_key and not openai_key.startswith("sk-"))):
            api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
            _client = AzureOpenAI(
                azure_endpoint=azure_ep,
                api_key=azure_key or openai_key,
                api_version=api_ver,
                timeout=8.0,
                max_retries=1,
            )
        elif openai_key:
            _client = OpenAI(api_key=openai_key, timeout=8.0, max_retries=1)
        elif azure_key:
            api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
            _client = AzureOpenAI(
                azure_endpoint=azure_ep,
                api_key=azure_key,
                api_version=api_ver,
                timeout=8.0,
                max_retries=1,
            )
        else:
            _client = OpenAI(timeout=8.0, max_retries=1)
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


def reindex_from_price_book(docx_path: str, write_overlay: bool = True) -> Dict[str, Any]:
    """Index FTL's Contractor Price Book (.docx). Parses catalogue tables, writes the price overlay
    the shared rulebook applies, then re-embeds the merged catalogue (new book plus legacy sections)."""
    from app.ftl.qualifier import price_book

    book = price_book.parse_price_book(docx_path)
    pages = price_book.build_pages(book)
    result = reindex_pricelist(pages, source_name=f"ftl-price-book {book['revision']}")
    if write_overlay:
        price_book.write_overlay(book)
    result["price_book"] = price_book.price_book_summary(book)
    return result


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def keyword_search(query: str, all_chunks: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    """Score = share of the query's distinct tokens found in the chunk, so it stays on the same
    0..1 scale the callers' low-confidence thresholds expect."""
    q_tokens = set(_TOKEN_RE.findall(query.lower()))
    if not q_tokens:
        return []
    scored = []
    for c in all_chunks:
        c_tokens = set(_TOKEN_RE.findall((c.get("text") or "").lower()))
        score = len(q_tokens & c_tokens) / len(q_tokens)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda sc: -sc[0])
    return [
        {"text": c["text"], "page": c["page"], "score": float(score)}
        for score, c in scored[:top_k]
    ]


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
    except Exception as exc:
        logger.warning("Pricelist embedding failed, using keyword search instead: %s", exc)
        return keyword_search(query, all_chunks, top_k)

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


_PRICE_LINE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2})")
_PANEL_ROW_TYPE_RE = re.compile(r"^(1S|2C|2T)$")
_WIDTH_TOKEN_RE = re.compile(r"^(\d+)$")


def car_door_panel_prices() -> List[Dict[str, Any]]:
    """Universal car door panel rows (1S/2C/2T x width) from the searchable index.

    `code` is the catalogue row text. 2C/2T set prices are halved into `unit_price`; 1S prices
    are already per car.
    """
    index = load_index()
    seen: Dict[str, Dict[str, Any]] = {}
    for page in index.get("pages", []):
        for chunk in page.get("chunks", []):
            words = (chunk.get("text", "") or "").split()
            for i, word in enumerate(words):
                match = _PANEL_ROW_TYPE_RE.match(word)
                if not match:
                    continue
                door_type = match.group(1)
                if i + 1 >= len(words):
                    continue
                nxt = words[i + 1].upper()
                if not nxt.startswith(door_type + "_") or "UNIVERSAL_CAR_DOOR" not in nxt:
                    continue
                width = None
                for j in range(i + 1, min(i + 6, len(words) - 2)):
                    if words[j] == "-" and words[j + 2].upper() == "X" and _WIDTH_TOKEN_RE.match(words[j + 1]):
                        width = words[j + 1]
                        break
                if width is None:
                    continue
                mod_idx = None
                for j in range(i, min(i + 45, len(words))):
                    if words[j].upper() == "(MOD)":
                        mod_idx = j
                        break
                if mod_idx is None:
                    continue
                price = None
                for j in range(mod_idx + 1, min(mod_idx + 4, len(words))):
                    price_match = _PRICE_LINE_RE.fullmatch(words[j])
                    if price_match:
                        price = float(price_match.group(1).replace(",", ""))
                        break
                if price is None:
                    continue
                is_set = any("(2-DOOR" in words[k].upper() for k in range(i, mod_idx))
                raw_code = " ".join(words[i + 1 : mod_idx + 1])
                seen[f"{door_type}_{width}"] = {
                    "door_type": door_type,
                    "width": width,
                    "code": raw_code,
                    "raw_price": price,
                    "is_set": is_set,
                    "unit_price": round(price / 2, 2) if is_set else price,
                }
    return list(seen.values())


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
