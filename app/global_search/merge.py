"""Merge Global Search tool hits into a flat list."""
from __future__ import annotations

from app.global_search.types import GlobalSearchResult, SearchHit


def _hit_key(hit: SearchHit) -> str:
    typ = (hit.type or hit.entity_type or "").lower()
    eid = (hit.entity_id or "").replace("-", "").lower()
    if hit.id and isinstance(hit.id, dict):
        for key in ("itemId", "formEntryId", "repositoryId", "workflowId", "instanceId"):
            val = hit.id.get(key)
            if val not in (None, "", 0, "0"):
                eid = str(val).replace("-", "").lower()
                break
    return f"{typ}:{eid}"


def merge_document_hits(field_hits: list[SearchHit], rag_hits: list[SearchHit]) -> list[SearchHit]:
    """Field (quick) wins over content (RAG) for the same itemId."""
    by_id: dict[str, SearchHit] = {}
    for hit in rag_hits:
        key = (hit.entity_id or "").replace("-", "").lower()
        if key:
            by_id[key] = hit
    for hit in field_hits:
        key = (hit.entity_id or "").replace("-", "").lower()
        if key:
            by_id[key] = hit
    field_ids = {(h.entity_id or "").replace("-", "").lower() for h in field_hits}
    ordered: list[SearchHit] = []
    seen: set[str] = set()
    for hit in field_hits:
        key = (hit.entity_id or "").replace("-", "").lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(by_id[key])
    for hit in rag_hits:
        key = (hit.entity_id or "").replace("-", "").lower()
        if key and key not in seen and key not in field_ids:
            seen.add(key)
            ordered.append(hit)
        elif key and key not in seen:
            seen.add(key)
            ordered.append(by_id[key])
    return ordered


def build_result(query: str, hits: list[SearchHit]) -> GlobalSearchResult:
    """Dedupe by type+primary id; preserve order."""
    out: list[SearchHit] = []
    seen: set[str] = set()
    for hit in hits:
        if not (hit.type or hit.entity_type):
            continue
        if not hit.type:
            hit.type = hit.entity_type
        if not hit.entity_type:
            hit.entity_type = hit.type
        key = _hit_key(hit)
        if key in seen or key.endswith(":"):
            continue
        seen.add(key)
        out.append(hit)
    return GlobalSearchResult(query=query, hits=out)


def status_reply(result: GlobalSearchResult) -> str:
    total = len(result.hits)
    if total == 0:
        return "No matches."
    return f"Found {total} match{'es' if total != 1 else ''}."
