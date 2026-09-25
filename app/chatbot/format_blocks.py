"""Format Global Search hits as CHATBOT.md-style text.blocks + optional browse."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.global_search.types import GlobalSearchResult, SearchHit

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _looks_like_guid(value: str) -> bool:
    return bool(_GUID_RE.match((value or "").strip()))


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


_FIELD_LABELS = {
    "description": "Description",
    "doctype": "Document type",
    "documenttype": "Document type",
    "name": "Name",
    "ifilename": "File name",
    "filename": "File name",
    "supplier": "Supplier",
    "vendor": "Vendor",
    "invoiceno": "Invoice No",
    "invoice_no": "Invoice No",
    "referencenumber": "Reference",
    "reference_number": "Reference",
}


def _human_field_label(field: str) -> str:
    key = (field or "").strip().lower().replace(" ", "")
    if not key:
        return "Field"
    if key in _FIELD_LABELS:
        return _FIELD_LABELS[key]
    return field.replace("_", " ").strip().title() or "Field"


def _build_filter_items(
    *,
    query: str,
    hits: list[SearchHit],
    repository_name: str = "",
) -> list[dict[str, str]]:
    """CHATBOT.md-style Filters Applied / Filters Tried (from hits, not LLM terms)."""
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(label: str, value: str) -> None:
        label_s = (label or "").strip()
        value_s = (value or "").strip()
        if not label_s or not value_s:
            return
        key = (label_s.lower(), value_s.lower())
        if key in seen:
            return
        seen.add(key)
        items.append({"label": label_s, "value": value_s})

    if query.strip():
        add("Search", query.strip())

    repo_names: list[str] = []
    locked_name = (repository_name or "").strip()
    if locked_name and not _looks_like_guid(locked_name):
        repo_names.append(locked_name)
    for hit in hits:
        id_obj = hit.id if isinstance(hit.id, dict) else {}
        rname = str(id_obj.get("repositoryName") or "").strip()
        if rname and not _looks_like_guid(rname) and rname not in repo_names:
            repo_names.append(rname)
    for name in repo_names[:3]:
        add("Repository", name)

    # Document field matches → semantic filter rows (like Supplier / FILFREE in CHATBOT.md).
    for hit in hits:
        if (hit.type or hit.entity_type or "").lower() not in {"document", "documents"}:
            continue
        field = str(hit.matched_field or "").strip()
        value = str(hit.matched_value or hit.description or "").strip()
        if field and value:
            add(_human_field_label(field), value[:120])

    for hit in hits:
        typ = (hit.type or hit.entity_type or "").lower()
        if typ in {"document", "documents", "repository", "repositories"}:
            continue
        field = str(hit.matched_field or "Name").strip()
        value = str(hit.matched_value or hit.entity_name or "").strip()
        if value:
            add(_human_field_label(field), value[:120])

    return items[:12]


def _filter_by_from_items(items: list[dict[str, str]], query: str) -> dict[str, str]:
    """Populate browse_request.filterBy for the left panel (CHATBOT.md shape)."""
    out: dict[str, str] = {}
    for item in items:
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        if label.lower() in {"search", "query"}:
            out["search"] = value
            continue
        if label.lower() == "repository" and _looks_like_guid(value):
            continue
        key = label.lower().replace(" ", "_")
        out[key] = value
    if query.strip() and "search" not in out:
        out["search"] = query.strip()
    return out


def _browse_from_hits(
    hits: list[SearchHit],
    *,
    workspace_id: str = "",
    specific_id: str = "",
    query: str = "",
    filter_items: Optional[list[dict[str, str]]] = None,
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
    filter_by = _filter_by_from_items(filter_items or [], query)
    action = {
        "browse_request": {
            "repositoryId": repo_id,
            "filterBy": filter_by,
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
    catalog_list: Optional[str] = None,
    repository_name: str = "",
) -> dict[str, Any]:
    """Build chatbot_result fields from a GlobalSearchResult."""
    hits = list(result.hits)
    query = result.query
    total = len(hits)
    filter_items = _build_filter_items(
        query=query,
        hits=hits,
        repository_name=repository_name,
    )
    if total == 0:
        if catalog_list:
            label = catalog_list
            reply = f"No {label.lower()} found in this tenant."
            blocks: list[dict[str, Any]] = [
                {"type": "paragraph", "text": reply},
            ]
            if filter_items:
                blocks.append(
                    {
                        "type": "bullets",
                        "title": "Filters Tried",
                        "items": filter_items,
                    }
                )
        else:
            reply = f'No matches found for "{query}".' if query.strip() else "No matches."
            blocks = [
                {
                    "type": "paragraph",
                    "text": reply,
                }
            ]
            if filter_items:
                blocks.append(
                    {
                        "type": "bullets",
                        "title": "Filters Tried",
                        "items": filter_items,
                    }
                )
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
    doc_count = by_type.get("document", 0) + by_type.get("documents", 0)
    if catalog_list:
        reply = f"Found {total} {catalog_list.lower()}."
        intro = f"Here are the {catalog_list.lower()} available in this tenant ({total}):"
        if not filter_items:
            filter_items = [{"label": "Catalog", "value": catalog_list}]
    elif doc_count and doc_count == total:
        reply = f"Found {doc_count} document{'s' if doc_count != 1 else ''}."
        intro = f'I found {doc_count} document{"s" if doc_count != 1 else ""} matching "{query}".'
    elif doc_count:
        reply = f"Found {total} matches."
        intro = (
            f'I found {doc_count} document{"s" if doc_count != 1 else ""} and '
            f'{total - doc_count} other result{"s" if total - doc_count != 1 else ""} for "{query}".'
        )
    else:
        reply = f"Found {total} match{'es' if total != 1 else ''}."
        intro = (
            f'I found {total} result{"s" if total != 1 else ""} matching "{query}"'
            f" ({', '.join(summary_bits)})."
        )
    if not filter_items:
        filter_items = [{"label": "Search", "value": query}]
    blocks = [
        {
            "type": "paragraph",
            "text": intro,
        },
        {
            "type": "bullets",
            "title": "Filters Applied",
            "items": filter_items,
        },
        {
            "type": "cards",
            "items": [_card_from_hit(hit) for hit in hits],
        },
    ]
    if catalog_list == "Repositories":
        picker_items = []
        for hit in hits:
            id_obj = hit.id if isinstance(hit.id, dict) else {}
            rid = id_obj.get("repositoryId") or hit.entity_id
            if rid:
                picker_items.append({"repositoryId": rid, "name": _hit_title(hit)})
        if picker_items:
            blocks.append(
                {
                    "type": "repo_picker",
                    "title": "Choose a repository",
                    "items": picker_items,
                }
            )
    action, action_context, action_to = _browse_from_hits(
        hits,
        workspace_id=workspace_id,
        specific_id=specific_id,
        query=query,
        filter_items=filter_items,
    )
    return {
        "reply": reply,
        "text": {"blocks": blocks},
        "hits": [hit.model_dump() for hit in hits],
        "action": action,
        "actionTo": action_to,
        "actionContext": action_context,
    }
