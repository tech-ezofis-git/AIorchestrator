"""Format Global Search hits as CHATBOT.md-style text.blocks + optional browse."""
from __future__ import annotations

from typing import Any, Optional

from app.global_search.types import GlobalSearchResult, SearchHit


def _hit_title(hit: SearchHit) -> str:
    return (
        hit.ifileName
        or hit.name
        or hit.entity_name
        or hit.matched_value
        or hit.entity_id
        or hit.type
        or "Untitled"
    )


def _hit_subtitle(hit: SearchHit) -> str:
    bits: list[str] = []
    typ = (hit.type or hit.entity_type or "").strip()
    if typ:
        bits.append(typ)
    if hit.matchSource:
        bits.append(str(hit.matchSource))
    if hit.formKind:
        bits.append(str(hit.formKind))
    if hit.matched_field:
        bits.append(str(hit.matched_field))
    id_obj = hit.id if isinstance(hit.id, dict) else {}
    repo = id_obj.get("repositoryName") or id_obj.get("repositoryId")
    if repo and str(repo) not in bits:
        bits.append(f"repo: {repo}")
    wf = id_obj.get("workflowName") or id_obj.get("workflowId")
    if wf not in (None, "", 0, "0"):
        bits.append(f"wf: {wf}")
    req = id_obj.get("requestNo") or hit.requestNo
    if req:
        bits.append(f"req: {req}")
    stage = id_obj.get("stage") or (hit.metadata or {}).get("stage")
    if stage:
        bits.append(f"stage: {stage}")
    if id_obj.get("commentId"):
        bits.append(f"comment: {id_obj.get('commentId')}")
    if id_obj.get("instanceId") and (hit.type or "") in {"ticket", "comment"}:
        bits.append(f"instance: {id_obj.get('instanceId')}")
    return " · ".join(str(b) for b in bits if b)


def _card_from_hit(hit: SearchHit) -> dict[str, Any]:
    id_obj = hit.id if isinstance(hit.id, dict) else {}
    return {
        "title": _hit_title(hit),
        "subtitle": _hit_subtitle(hit),
        "description": hit.description or hit.matched_value or "",
        "type": hit.type or hit.entity_type or "",
        "id": id_obj or None,
        "matchSource": hit.matchSource,
        "modifiedDateandtime": hit.modifiedDateandtime or "",
        "dateandtime": hit.dateandtime or "",
    }


def _browse_from_hits(
    hits: list[SearchHit],
    *,
    workspace_id: str = "",
    specific_id: str = "",
) -> tuple[Optional[dict], Optional[dict], Optional[str]]:
    """Return (action, actionContext, actionTo) when a repository can be opened."""
    repo_id = (specific_id or "").strip()
    item_id = ""
    if not repo_id:
        for hit in hits:
            id_obj = hit.id if isinstance(hit.id, dict) else {}
            candidate = str(id_obj.get("repositoryId") or "").strip()
            if candidate:
                repo_id = candidate
                item_id = str(id_obj.get("itemId") or "").strip()
                break
    if not repo_id:
        return None, None, None
    action = {
        "browse_request": {
            "repositoryId": repo_id,
            "filterBy": {},
            "itemsPerPage": 100,
        }
    }
    action_context = {
        "workspaceId": workspace_id or "",
        "repositoryId": repo_id,
        "itemId": item_id,
    }
    return action, action_context, "Repository"


def format_search_blocks(
    result: GlobalSearchResult,
    *,
    workspace_id: str = "",
    specific_id: str = "",
) -> dict[str, Any]:
    """Build chatbot_result fields from a GlobalSearchResult."""
    hits = list(result.hits)
    query = result.query
    total = len(hits)
    if total == 0:
        reply = "No matches."
        blocks: list[dict[str, Any]] = [
            {
                "type": "paragraph",
                "text": f'No matches found for "{query}".',
            }
        ]
        return {
            "reply": reply,
            "text": {"blocks": blocks},
            "hits": [],
            "action": None,
            "actionTo": None,
            "actionContext": None,
        }

    by_type: dict[str, int] = {}
    for hit in hits:
        typ = hit.type or hit.entity_type or "other"
        by_type[typ] = by_type.get(typ, 0) + 1
    summary_bits = [f"{count} {typ}" for typ, count in sorted(by_type.items())]
    reply = f"Found {total} match{'es' if total != 1 else ''}."
    blocks = [
        {
            "type": "paragraph",
            "text": f'I found {total} result{"s" if total != 1 else ""} matching "{query}" ({", ".join(summary_bits)}).',
        },
        {
            "type": "bullets",
            "title": "Filters Applied",
            "items": [
                {"label": "Query", "value": query},
                *([{"label": "Repository", "value": specific_id}] if specific_id else []),
            ],
        },
        {
            "type": "cards",
            "items": [_card_from_hit(hit) for hit in hits],
        },
    ]
    action, action_context, action_to = _browse_from_hits(
        hits, workspace_id=workspace_id, specific_id=specific_id
    )
    return {
        "reply": reply,
        "text": {"blocks": blocks},
        "hits": [hit.model_dump() for hit in hits],
        "action": action,
        "actionTo": action_to,
        "actionContext": action_context,
    }
