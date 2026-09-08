"""Group hits into Repositories / Workflows / Documents cards."""
from __future__ import annotations

from app.global_search.types import GlobalSearchResult, SearchGroup, SearchHit

_GROUP_ORDER = (
    ("repository", "Repositories"),
    ("workflow", "Workflows"),
    ("document", "Documents"),
)


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


def build_result(
    query: str,
    *,
    repositories: list[SearchHit],
    workflows: list[SearchHit],
    documents: list[SearchHit],
) -> GlobalSearchResult:
    buckets = {
        "repository": repositories,
        "workflow": workflows,
        "document": documents,
    }
    groups: list[SearchGroup] = []
    for entity_type, label in _GROUP_ORDER:
        hits = buckets.get(entity_type) or []
        groups.append(
            SearchGroup(
                entity_type=entity_type,
                label=label,
                count=len(hits),
                hits=hits,
            )
        )
    return GlobalSearchResult(query=query, groups=groups)


def status_reply(result: GlobalSearchResult) -> str:
    total = sum(g.count for g in result.groups)
    if total == 0:
        return "No matches."
    parts = [f"{g.count} {g.label}" for g in result.groups if g.count]
    return f"Found {total} match{'es' if total != 1 else ''} across {', '.join(parts)}."
