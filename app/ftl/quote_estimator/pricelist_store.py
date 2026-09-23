"""
The Quote Estimator's knowledge base — the Wittur pricelist PDF, chunked and embedded, stored as
one local JSON file (data/pricelist_index.json). No database: brute-force cosine similarity over
an in-memory numpy array is plenty fast for a pricelist-sized document, and this is a standalone
single-user tool, not a multi-tenant service. Identical implementation to the Qualifier project's
pricelist_store.py by design — same pattern, separate copy, own data/ directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Union

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
    os.getenv("QUOTE_EMBEDDING_MODEL")
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


_PANEL_SET_RE = re.compile(r"\((\d+)[- ]DOOR/SET\)", re.IGNORECASE)
_PRICE_LINE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2})")


def panel_set_prices() -> List[Dict[str, Any]]:
    """Every "(N-DOOR/SET)" universal car door panel row found in the indexed pricelist text, with
    its set price and derived per-panel price — used as a deterministic correction backstop in
    agent.py's _sanitize_quote.

    IMPORTANT — this must be WORD-token based, not line based. An earlier version split chunk text
    on "\\n" and did a line-windowed lookahead for the price, which worked against a raw PyMuPDF
    text dump used to test it — but chunk_text() (the function that actually builds the production
    index) does `text.split()` / `" ".join(...)`, which strips every newline, collapsing an entire
    table into ONE line per chunk. Against real indexed data that made the line-window degenerate
    to "grab the first price-looking substring anywhere in the whole chunk" — confirmed in a real
    run: it matched a 1S panel's own (un-set, never-halved) $1,635.00 price as if it were a "(2-
    DOOR/SET)" set price, halved it to a wrong $817.50, and — because the type/width regex also
    expected a line boundary and found none — fell back to using the ENTIRE multi-row chunk text
    as `product_code`, corrupting that field with ~1500 characters of raw pricelist dump. Operating
    on whitespace-split word tokens instead is robust regardless of whether newlines survived
    chunking, since chunk_text() always preserves word order. A generous but bounded forward
    window (25 words — verified against the real ~15-word gap between a "(N-DOOR/SET)" marker and
    its own price) finds the row's own price without spilling into a neighboring row's. If the
    type/width can't be cleanly derived from the tokens immediately before the marker, this SKIPS
    the entry rather than falling back to a large raw-text code — better to miss a correction than
    to ever repeat the corrupted-product_code bug."""
    index = load_index()
    seen: Dict[str, Dict[str, Any]] = {}
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            words = (c.get("text", "") or "").split()
            for i, w in enumerate(words):
                m = _PANEL_SET_RE.fullmatch(w)
                if not m:
                    continue
                doors = int(m.group(1))
                if doors <= 0:
                    continue

                price = None
                for j in range(i + 1, min(i + 26, len(words))):
                    pm = _PRICE_LINE_RE.fullmatch(words[j])
                    if pm:
                        price = float(pm.group(1).replace(",", ""))
                        break
                if price is None:
                    continue

                # Expected token shape immediately before the marker: ... <TYPE>_UNIVERSAL_CAR_DOOR_PANEL - <WIDTH> X 84 (N-DOOR/SET)
                code = None
                if i >= 5 and words[i - 1] == "84" and words[i - 2].upper() == "X" and words[i - 4] == "-":
                    width = words[i - 3]
                    tm = re.match(r"^(\w+)_UNIVERSAL_CAR_DOOR_PANEL$", words[i - 5], re.IGNORECASE)
                    if tm and width.isdigit():
                        code = f"{tm.group(1).upper()}_UNIVERSAL_CAR_DOOR_PANEL_{width}X84"
                if code is None:
                    continue  # can't confirm a clean code — skip rather than risk a garbage product_code

                seen[code] = {
                    "code": code,
                    "doors_per_set": doors,
                    "set_price": price,
                    "per_panel_price": round(price / doors, 2),
                }
    return list(seen.values())


_GOVERNOR_CODE_RE = re.compile(r"^WG_OL(\d+)_GOVERNOR_(\w+)$", re.IGNORECASE)


def governor_prices() -> List[Dict[str, Any]]:
    """Every WG_OL<size>_GOVERNOR_<variant> row in the indexed pricelist (e.g.
    WG_OL35_GOVERNOR_RC, WG_OL35_GOVERNOR_MT, WG_OL35_GOVERNOR_RE), with its real code and price —
    parsed the same deterministic, no-embedding way as door_operator_prices() above. Used by
    agent.py's governor-completeness backstop: when a project's Equipment scope table confirms
    governors are new scope but the model's own quote has ZERO governor lines at all, this gives a
    real, current code+price to auto-add a baseline line with, rather than leaving the category
    silently empty or having code guess/hardcode a price that could go stale. Returns an empty list
    (never raises) if no such rows are indexed — callers must handle that by not auto-adding
    anything, since inventing a product this function can't find would be worse than leaving the gap
    visible via the existing governor-count warning.

    Forward search window is 25 words, not the original 15 — confirmed necessary once the OL100-RE
    row (added Sep 2026 as a temporary business-provided price, see the pricelist reindex) was
    indexed: its description ("REMOTE+ENCODER GOVENOR (CAR ONLY) INCLUDES VERTICAL TENSION WEIGHT
    INCLUDES FILLER WEIGHTS (X6) & FIXINGS") runs 14 words before its price, one word past the old
    15-word cap, which silently dropped this exact row. 25 matches the same window
    panel_set_prices() above already uses for the same reason (long descriptions between a marker
    and its own price)."""
    index = load_index()
    seen: Dict[str, Dict[str, Any]] = {}
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            words = (c.get("text", "") or "").split()
            for i, w in enumerate(words):
                m = _GOVERNOR_CODE_RE.match(w)
                if not m:
                    continue
                price = None
                for j in range(i + 1, min(i + 26, len(words))):
                    pm = _PRICE_LINE_RE.fullmatch(words[j])
                    if pm:
                        price = float(pm.group(1).replace(",", ""))
                        break
                if price is None:
                    continue
                seen[w.upper()] = {
                    "code": w,
                    "size": m.group(1),
                    "variant": m.group(2).upper(),
                    "price": price,
                }
    return list(seen.values())


_DOOR_OP_CODE_RE = re.compile(r"^SGV2_DOOR_OP_(1S|2C|2T)(\d+)_(LH|RH)$", re.IGNORECASE)


def door_operator_prices() -> List[Dict[str, Any]]:
    """Every SGV2_DOOR_OP_<type><width>_<hand> row in the indexed pricelist (door type x width x
    hand), with its real code and price — confirmed directly against the live pricelist, which lists
    them as one clean word token each (e.g. "SGV2_DOOR_OP_1S42_LH"), unlike car door panels. Used by
    agent.py's _sanitize_quote to auto-correct the recurring SSSO->1S override bug: when the model's
    own note says entrance type SSSO (meaning 1S) but it submitted a 2C/2T operator code anyway, this
    gives a real 1S code+price for the same width/hand to swap in, instead of just flagging it."""
    index = load_index()
    seen: Dict[str, Dict[str, Any]] = {}
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            words = (c.get("text", "") or "").split()
            for i, w in enumerate(words):
                m = _DOOR_OP_CODE_RE.match(w)
                if not m:
                    continue
                price = None
                for j in range(i + 1, min(i + 20, len(words))):
                    pm = _PRICE_LINE_RE.fullmatch(words[j])
                    if pm:
                        price = float(pm.group(1).replace(",", ""))
                        break
                if price is None:
                    continue
                seen[w.upper()] = {
                    "code": w,
                    "door_type": m.group(1).upper(),
                    "width": m.group(2),
                    "hand": m.group(3).upper(),
                    "price": price,
                }
    return list(seen.values())


_PANEL_ROW_TYPE_RE = re.compile(r"^(1S|2C|2T)$")
_WIDTH_TOKEN_RE = re.compile(r"^(\d+)$")


def car_door_panel_prices() -> List[Dict[str, Any]]:
    """Every universal car door panel pricelist row (door type x width — 1S/2C/2T x 36/42/48),
    parsed by finding the repeated TYPE token that starts each row and using the row's own trailing
    "(MOD)" marker (common to all three door types' description text) to locate its price — unlike
    panel_set_prices() above, this also covers 1S rows, which have no "(N-DOOR/SET)" marker to key
    off of. Returns each row's REAL, FULL product-name text verbatim as `code` (never a shortened/
    synthetic code — confirmed directly against the live pricelist that this ~130-150 char string
    really is the only "code" this catalogue section has) plus a ready-to-use `unit_price`:
      - 2C/2T rows carry a "(2-DOOR/SET)" marker — `raw_price` is the full set price, halved into
        `unit_price` for per-panel invoicing (see the halving convention elsewhere in this file).
      - 1S rows have no such marker — `raw_price` IS `unit_price`, used as-is per car, never halved.
    Used by agent.py's _sanitize_quote to auto-correct the SSSO->1S override bug on the car door
    panel line to match a corrected door operator line."""
    index = load_index()
    seen: Dict[str, Dict[str, Any]] = {}
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            words = (c.get("text", "") or "").split()
            for i, w in enumerate(words):
                m = _PANEL_ROW_TYPE_RE.match(w)
                if not m:
                    continue
                door_type = m.group(1)
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
                    pm = _PRICE_LINE_RE.fullmatch(words[j])
                    if pm:
                        price = float(pm.group(1).replace(",", ""))
                        break
                if price is None:
                    continue
                is_set = any("(2-DOOR" in words[k].upper() for k in range(i, mod_idx))
                raw_code = " ".join(words[i + 1 : mod_idx + 1])
                key = f"{door_type}_{width}"
                seen[key] = {
                    "door_type": door_type,
                    "width": width,
                    "code": raw_code,
                    "raw_price": price,
                    "is_set": is_set,
                    "unit_price": round(price / 2, 2) if is_set else price,
                }
    return list(seen.values())


# IMPORTANT: the character class includes "(" and ")" — real codes in the current (2026) pricelist
# include parenthesized infixes, e.g. "SGV2(1S)_DP_GAL42_LH" for panel adaptors. An earlier version
# of this regex omitted parens, which meant \b[A-Z][A-Z0-9]*(?:[_.]+[A-Z0-9]+)+\b simply could not
# match across the "(1S)" gap at all — known_product_codes() silently dropped every single panel-
# adaptor code in the whole pricelist, so a correctly-copied "SGV2(1S)_DP_GAL42_LH" got flagged by
# the whitelist check in agent.py as "not found verbatim... likely fabricated" every time, even
# though it was real. Confirmed directly against the live pricelist_index.json.
_PRODUCT_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9()]*(?:[_.]+[A-Z0-9()]+)+\b")


def known_product_codes() -> Set[str]:
    """Every clean, single-token, underscore-bearing code that literally appears in the indexed
    pricelist text — used as a whitelist backstop in agent.py's _sanitize_quote to catch a real
    failure mode: the model submitting a plausible-looking but entirely fabricated product_code
    (e.g. "CSGB03_CAR_SAFETIES" — it has an underscore and passes the earlier "looks like a code"
    heuristic, but no such SKU exists anywhere in the pricelist; the real code is
    "WS_CSGB_CAR_SAFETIES"). Roller guide codes (e.g. "WRG_MOTION_GEAR150") ARE plain single tokens
    present verbatim in the source PDF — confirmed directly against the live index — so they're
    meaningfully checkable here too now; car door panel rows are still NOT meaningfully checkable
    this way, since their real "code" in this pricelist is actually the entire multi-word PRODUCT
    NAME column text (spaces, dimensions, and all — there is no separate short SKU for that section
    of the catalogue), which this single-token regex can't represent — callers should keep skipping
    this whitelist for car door panels specifically."""
    index = load_index()
    codes: Set[str] = set()
    for page in index.get("pages", []):
        for c in page.get("chunks", []):
            codes.update(_PRODUCT_CODE_RE.findall(c.get("text", "") or ""))
    return codes


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
