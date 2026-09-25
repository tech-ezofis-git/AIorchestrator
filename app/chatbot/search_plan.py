"""Model plan for chatbot document and ticket search.

The model only chooses a target and the lookup text, then writes one
sentence from the hits the tools already returned. Tool execution stays
in code. If the model is off or fails, keyword rules are used instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.chatbot.query_rewrite import rewrite_search_query
from app.chatbot.search_scope import classify_search_scope, search_text_for_scope
from app.global_search.sql_search import normalize_query
from app.llm.adapter import LLMAdapter

logger = logging.getLogger("orchestrator.chatbot.search_plan")

SearchTarget = str

_DOCUMENT_TOOLS = ("search_repo_metadata", "search_repo_rag")
_TICKET_TOOLS = ("search_tickets", "search_comments")
_TARGET_TOOLS = {
    "documents": _DOCUMENT_TOOLS,
    "tickets": _TICKET_TOOLS,
    "both": _DOCUMENT_TOOLS + _TICKET_TOOLS,
}

_PLAN_SYSTEM = (
    "You route an EZOFIS search over documents and tickets. "
    "Reply with ONLY JSON, no markdown: "
    '{"target":"documents|tickets|both|ask_repository|ask_term","query":""}. '
    "documents = files, text, words, pdfs, attachments inside repositories. "
    "tickets = requests and tickets. "
    "both = invoice, PO, document number, or a bare keyword that could be in a file or a ticket. "
    "query is only the value to find. Drop words such as search, text, word, document, of, from. "
    'Examples: "Search a Text of APEX" → {"target":"documents","query":"APEX"}. '
    '"Search a word of APEX" → {"target":"documents","query":"APEX"}. '
    '"documents from 6001" → {"target":"documents","query":"6001"}. '
    '"documents in 6001" → {"target":"documents","query":"6001"}. '
    '"find ticket REQ-12" → {"target":"tickets","query":"REQ-12"}. '
    '"invoice 6001" → {"target":"both","query":"6001"}. '
    "If the user wants documents but gives no value: "
    "has_repository false → ask_repository and query empty; "
    "has_repository true → ask_term and query empty. "
    "Do not invent ids. Do not name tools."
)

_REPLY_SYSTEM = (
    "Write one or two plain sentences about these search hits for the user. "
    "Use only the query and the hit names, types, and request numbers provided. "
    "Say whether they are documents or tickets. "
    "If hits is empty, say nothing was found for the query. "
    "Do not invent files, tickets, or ids. No JSON. No markdown."
)

_TARGET_ALIASES = {
    "documents": "documents",
    "document": "documents",
    "files": "documents",
    "file": "documents",
    "repository": "documents",
    "tickets": "tickets",
    "ticket": "tickets",
    "request": "tickets",
    "requests": "tickets",
    "both": "both",
    "ask_repository": "ask_repository",
    "ask_repo": "ask_repository",
    "repository_choice": "ask_repository",
    "ask_term": "ask_term",
    "ask_query": "ask_term",
    "clarify": "ask_term",
}


@dataclass(frozen=True)
class SearchPlan:
    target: SearchTarget
    query: str
    source: str
    usage: Optional[dict] = None


def search_llm_enabled() -> bool:
    flag = os.getenv("CHATBOT_SEARCH_LLM", "true").strip().lower()
    return flag not in {"0", "false", "off", "no"}


def tools_for_plan(plan: SearchPlan) -> tuple[str, ...]:
    return _TARGET_TOOLS.get(plan.target, _TARGET_TOOLS["both"])


# Words used to point at a repository. They are not the repository name.
_CHOICE_FILLER = frozenset(
    {
        "check",
        "choose",
        "doc",
        "docs",
        "document",
        "documents",
        "file",
        "files",
        "go",
        "inside",
        "look",
        "looking",
        "open",
        "pdf",
        "pdfs",
        "please",
        "repo",
        "repos",
        "repositories",
        "repository",
        "search",
        "select",
        "use",
        "using",
        "within",
    }
)


def repository_choice_phrase(message: str) -> str:
    """Name left when the user is picking a repository.

    'check at Accounts Payable' → 'Accounts Payable'.
    'documents in 6001' is a lookup, so this returns empty.
    """
    rules = plan_from_rules(message)
    scope = classify_search_scope(message)
    if scope in {"repository", "workflow", "both"} and rules.query:
        return ""
    rewritten = rewrite_search_query(message)
    kept: list[str] = []
    for token in rewritten.split():
        cleaned = token.strip("?.!,;:\"'()[]{}").lower()
        if not cleaned or cleaned in _CHOICE_FILLER:
            continue
        kept.append(token.strip("?.!,;:\"'()[]{}"))
    phrase = normalize_query(" ".join(kept))
    if not phrase or any(ch.isdigit() for ch in phrase):
        return ""
    if len(phrase.split()) > 4:
        return ""
    return phrase


def pending_lookup_from_history(history: Optional[list[dict[str, str]]]) -> Optional[SearchPlan]:
    """Last user lookup in this chat, skipping repository-choice turns."""
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content or repository_choice_phrase(content):
            continue
        rules = plan_from_rules(content)
        if rules.query and rules.target in {"documents", "tickets", "both"}:
            return rules
    return None


def lookup_override_plan(plan: SearchPlan, message: str, specific_id: str = "") -> SearchPlan:
    """Keep a lookup the keyword rules already found.

    The model sometimes answers ask_repository for 'documents in 6001'
    and drops 6001. The number is still in the message, so search it.
    """
    if repository_choice_phrase(message):
        return plan
    rules = plan_from_rules(message, specific_id=specific_id)
    if (
        plan.target in {"ask_repository", "ask_term"}
        and rules.query
        and rules.target in {"documents", "tickets", "both"}
    ):
        return SearchPlan(
            target=rules.target,
            query=rules.query,
            source="rules",
            usage=plan.usage,
        )
    return plan


def plan_from_rules(message: str, *, specific_id: str = "") -> SearchPlan:
    """Keyword fallback matching the pre-model document and ticket routes."""
    scope = classify_search_scope(message)
    query = search_text_for_scope(message, scope)
    if scope == "repository" and not query:
        target = "ask_term" if (specific_id or "").strip() else "ask_repository"
        return SearchPlan(target=target, query="", source="rules")
    if not query:
        query = normalize_query(message)
    target = {
        "repository": "documents",
        "workflow": "tickets",
        "both": "both",
        "other": "both",
    }[scope]
    return SearchPlan(target=target, query=query, source="rules")


def _parse_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _clean_query(raw: str) -> str:
    return rewrite_search_query(str(raw or ""))[:200]


def plan_from_model_json(
    data: dict[str, Any],
    *,
    message: str,
    specific_id: str,
    usage: Optional[dict] = None,
) -> Optional[SearchPlan]:
    target = _TARGET_ALIASES.get(str(data.get("target") or "").strip().lower())
    if not target:
        return None
    query = _clean_query(str(data.get("query") or ""))
    has_repo = bool((specific_id or "").strip())
    if target == "ask_repository" and has_repo:
        target = "ask_term"
    if target in {"documents", "tickets", "both"} and not query:
        if target == "documents" and not has_repo:
            target = "ask_repository"
        else:
            target = "ask_term"
    if target in {"ask_repository", "ask_term"}:
        query = ""
    if target in {"documents", "tickets", "both"} and not query:
        query = _clean_query(message)
    return SearchPlan(target=target, query=query, source="model", usage=usage)


def _history_snippet(history: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in (history or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role and content:
            out.append({"role": role, "content": content[:200]})
    return out


async def plan_document_ticket_search(
    llm: LLMAdapter,
    *,
    message: str,
    specific_id: str = "",
    history: Optional[list[dict[str, str]]] = None,
) -> SearchPlan:
    """Ask the chat model where to search. Fall back to keyword rules."""
    rules = plan_from_rules(message, specific_id=specific_id)
    if not search_llm_enabled():
        return lookup_override_plan(rules, message, specific_id)
    payload = {
        "message": message,
        "has_repository": bool((specific_id or "").strip()),
        "history": _history_snippet(history or []),
    }
    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
        )
    except Exception:
        logger.warning("chatbot_search_plan_failed")
        return rules
    data = _parse_json_object(str(result.get("content") or ""))
    if not data:
        logger.warning("chatbot_search_plan_unparsed")
        return rules
    plan = plan_from_model_json(
        data,
        message=message,
        specific_id=specific_id,
        usage=result.get("usage") if isinstance(result.get("usage"), dict) else None,
    )
    return lookup_override_plan(plan or rules, message, specific_id)


def _compact_hits(hits: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        id_obj = hit.get("id") if isinstance(hit.get("id"), dict) else {}
        out.append(
            {
                "type": str(hit.get("type") or hit.get("entity_type") or ""),
                "name": str(
                    hit.get("ifileName")
                    or hit.get("name")
                    or hit.get("entity_name")
                    or hit.get("matched_value")
                    or ""
                ),
                "requestNo": str(hit.get("requestNo") or id_obj.get("requestNo") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


async def friendly_search_reply(
    llm: LLMAdapter,
    *,
    query: str,
    hits: list[dict[str, Any]],
    fallback: str,
) -> tuple[str, Optional[dict]]:
    """One short sentence from real hits. Keep fallback text if the model fails."""
    if not search_llm_enabled():
        return fallback, None
    payload = {"query": query, "hit_count": len(hits or []), "hits": _compact_hits(hits)}
    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": _REPLY_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
        )
    except Exception:
        logger.warning("chatbot_search_reply_failed")
        return fallback, None
    text = str(result.get("content") or "").strip()
    if not text or text.startswith("{") or len(text) > 500:
        return fallback, None
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    return text, usage


def merge_usage(left: Optional[dict], right: Optional[dict]) -> Optional[dict]:
    if not left:
        return right
    if not right:
        return left
    merged: dict[str, Any] = {}
    for key in set(left) | set(right):
        a = left.get(key)
        b = right.get(key)
        if isinstance(a, int) and isinstance(b, int):
            merged[key] = a + b
        else:
            merged[key] = b if b is not None else a
    return merged
