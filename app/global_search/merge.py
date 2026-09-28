"""Merge Global Search tool hits into a flat list."""
from __future__ import annotations

from app.global_search.types import GlobalSearchResult, SearchHit


def _hit_key(hit: SearchHit) -> str:
    typ = (hit.type or hit.entity_type or "").lower()
    eid = (hit.entity_id or "").replace("-", "").lower()
    if hit.id and isinstance(hit.id, dict):
        for key in ("itemId", "formEntryId", "commentId", "repositoryId", "workflowId", "instanceId"):
            val = hit.id.get(key)
            if val not in (None, "", 0, "0"):
                eid = str(val).replace("-", "").lower()
                break
    return f"{typ}:{eid}"


def _guid(value: object) -> str:
    return str(value or "").replace("-", "").strip().lower()


def _document_ids(hit: SearchHit) -> set[str]:
    ids = set()
    eid = _guid(hit.entity_id)
    if eid:
        ids.add(eid)
    id_obj = hit.id if isinstance(hit.id, dict) else {}
    item = _guid(id_obj.get("itemId"))
    if item:
        ids.add(item)
    return ids


def _file_name(hit: SearchHit) -> str:
    return (hit.ifileName or hit.entity_name or "").strip().casefold()


def _repo_id(hit: SearchHit) -> str:
    id_obj = hit.id if isinstance(hit.id, dict) else {}
    return _guid(id_obj.get("repositoryId"))


def _same_document(left: SearchHit, right: SearchHit) -> bool:
    """Same file when ids overlap, or the same name in the same repository."""
    if _document_ids(left) & _document_ids(right):
        return True
    name_left, name_right = _file_name(left), _file_name(right)
    if not name_left or name_left != name_right:
        return False
    repo_left, repo_right = _repo_id(left), _repo_id(right)
    if repo_left and repo_right and repo_left != repo_right:
        return False
    return True


def _match_kind(hit: SearchHit) -> str:
    return (hit.matchSource or "").strip().lower()


def _prefer_document(kept: SearchHit, incoming: SearchHit) -> SearchHit:
    """One file. Keep the field match, and remember when the text matched too."""
    kinds = {_match_kind(kept), _match_kind(incoming)}
    base = incoming if _match_kind(kept) != "field" and _match_kind(incoming) == "field" else kept
    if "field" not in kinds or "content" not in kinds:
        return base
    meta = dict(base.metadata or {})
    meta["alsoDeepHit"] = True
    return base.model_copy(update={"metadata": meta})


def merge_document_hits(field_hits: list[SearchHit], rag_hits: list[SearchHit]) -> list[SearchHit]:
    """One hit per file. Field match wins over content, including different ids."""
    ordered: list[SearchHit] = []
    for hit in [*field_hits, *rag_hits]:
        typ = (hit.type or hit.entity_type or "").lower()
        if typ not in {"document", "documents"}:
            ordered.append(hit)
            continue
        for index, kept in enumerate(ordered):
            if _same_document(kept, hit):
                ordered[index] = _prefer_document(kept, hit)
                break
        else:
            ordered.append(hit)
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
