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
from app.llm.model_presets import resolve_preset_overrides

logger = logging.getLogger("orchestrator.chatbot.search_plan")

# Keyword extraction uses this preset even when the console default is another model.
KEYWORD_PRESET_ID = os.getenv("CHATBOT_KEYWORD_PRESET", "gpt-5-nano").strip() or "gpt-5-nano"

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
    '{"target":"documents|tickets|both|ask_term","query":""}. '
    "The word documents, document, file, or pdf selects repositories. It is not the file keyword. "
    "query is only the file keyword, such as APEX. Search every repository for that keyword. "
    "tickets = requests and tickets in workflows. "
    "both = invoice, PO, document number, or a bare keyword that could be in a file or a ticket. "
    "Drop words such as search, text, word, document, documents, of, from, form, need. "
    "form typed instead of from is not a keyword. "
    'Examples: "Find the documents from APEX" → {"target":"documents","query":"APEX"}. '
    '"Find the documents form APEX" → {"target":"documents","query":"APEX"}. '
    '"Search my documents" → {"target":"ask_term","query":""}. '
    '"Search a Text of APEX" → {"target":"documents","query":"APEX"}. '
    '"documents from 6001" → {"target":"documents","query":"6001"}. '
    '"find the content of APEX" → {"target":"documents","query":"APEX"}. '
    '"find request REQ-12" → {"target":"tickets","query":"REQ-12"}. '
    '"invoice 6001" → {"target":"both","query":"6001"}. '
    "If the message has no value to find, use ask_term and an empty query. "
    "Never ask the user to choose a repository. Do not invent ids. Do not name tools."
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


_LOCATOR_RE = re.compile(
    r"\b(?:at|in|inside|within|check|choose|select|use|using)\b",
    re.IGNORECASE,
)
_ANYWHERE_RE = re.compile(
    r"\b(?:anywhere|everywhere|any\s+repository|all\s+repositor(?:y|ies))\b",
    re.IGNORECASE,
)


def searches_anywhere(message: str) -> bool:
    """True when the user wants the lookup with no repository lock."""
    return bool(_ANYWHERE_RE.search(message or ""))


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

    'at Accounts Payable' → 'Accounts Payable'.
    'find the content of APEX' and 'APEX' are lookups, so this returns empty.
    """
    if not _LOCATOR_RE.search(message or ""):
        return ""
    rules = plan_from_rules(message)
    if rules.query and any(ch.isdigit() for ch in rules.query):
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


def match_repository_id(phrase: str, hits: list[Any]) -> str:
    """Pick one repository id for a user phrase such as 'Accounts Payable'."""
    phrase_key = (phrase or "").strip().casefold()
    if not phrase_key:
        return ""
    tokens = [part for part in phrase_key.split() if part]
    scored: list[tuple[int, int, str]] = []
    for item in hits or []:
        if isinstance(item, dict):
            id_obj = item.get("id") if isinstance(item.get("id"), dict) else {}
            name = str(
                id_obj.get("repositoryName") or item.get("entity_name") or item.get("name") or ""
            ).strip()
            repo_id = str(id_obj.get("repositoryId") or item.get("entity_id") or "").strip()
        else:
            id_obj = item.id if isinstance(getattr(item, "id", None), dict) else {}
            name = str(
                id_obj.get("repositoryName") or getattr(item, "entity_name", "") or getattr(item, "name", "") or ""
            ).strip()
            repo_id = str(id_obj.get("repositoryId") or getattr(item, "entity_id", "") or "").strip()
        if not repo_id or not name:
            continue
        key = name.casefold()
        if key == phrase_key:
            score = 100
        elif key.startswith(phrase_key) or phrase_key.startswith(key):
            score = 80
        elif phrase_key in key or key in phrase_key:
            score = 60
        elif tokens and all(token in key for token in tokens):
            score = 50
        else:
            continue
        scored.append((score, len(key), repo_id))
    if not scored:
        return ""
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    best_score, _best_len, best_id = scored[0]
    tied = [row for row in scored if row[0] == best_score and row[2] != best_id]
    if tied and best_score < 100:
        return ""
    return best_id


def repository_phrase_from_history(history: Optional[list[dict[str, str]]]) -> str:
    """Latest repository the user named, such as 'Accounts Payable'."""
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        phrase = repository_choice_phrase(str(item.get("content") or ""))
        if phrase:
            return phrase
    return ""


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


def model_file_keyword(query: str) -> str:
    """File keyword from a GPT-5 nano query. 'documents' is removed."""
    return search_text_for_scope(query, "other")


def lookup_override_plan(plan: SearchPlan, message: str, specific_id: str = "") -> SearchPlan:
    """Use the model file keyword. Fall back to rules when it is empty.

    'documents' selects repositories and is never the file keyword.
    'Find the documents from APEX' searches APEX.
    """
    rules = plan_from_rules(message, specific_id=specific_id)
    found = model_file_keyword(plan.query) if plan.source == "model" else ""
    if found and found.casefold() in (message or "").casefold():
        scope = classify_search_scope(message)
        if scope in {"repository", "workflow", "both"}:
            target = rules.target if rules.target in {"documents", "tickets", "both"} else "documents"
        elif plan.target in {"documents", "tickets", "both"}:
            target = plan.target
        else:
            target = rules.target if rules.target in {"documents", "tickets", "both"} else "both"
        return SearchPlan(target=target, query=found, source="model", usage=plan.usage)
    if rules.target == "ask_term" or not rules.query:
        return SearchPlan(target="ask_term", query="", source="rules", usage=plan.usage)
    if rules.target not in {"documents", "tickets", "both"}:
        return plan
    return SearchPlan(
        target=rules.target,
        query=rules.query,
        source="rules",
        usage=plan.usage,
    )


def plan_from_rules(message: str, *, specific_id: str = "") -> SearchPlan:
    """Keyword fallback matching the pre-model document and ticket routes.

    specific_id is ignored. Document search covers every repository.
    """
    _ = specific_id
    scope = classify_search_scope(message)
    query = search_text_for_scope(message, scope)
    if not query and scope in {"repository", "workflow", "both"}:
        return SearchPlan(target="ask_term", query="", source="rules")
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
    _ = specific_id
    if target == "ask_repository":
        target = "ask_term"
    if target in {"documents", "tickets", "both"} and not query:
        target = "ask_term"
    if target in {"ask_repository", "ask_term"}:
        query = ""
    if target in {"documents", "tickets", "both"} and not query:
        query = _clean_query(message)
    return SearchPlan(target=target, query=query, source="model", usage=usage)


def continue_from_history(
    plan: SearchPlan,
    message: str,
    history: Optional[list[dict[str, str]]],
) -> SearchPlan:
    """Reuse the previous lookup when this message names a scope but no value.

    'need document from APEX' then 'search the request' keeps APEX and
    switches to workflow tickets. A message that already has a value is unchanged.
    """
    if plan.target in {"documents", "tickets", "both"} and plan.query:
        return plan
    pending = pending_lookup_from_history(history)
    if pending is None or not pending.query:
        if plan.target == "ask_repository":
            return SearchPlan(target="ask_term", query="", source=plan.source, usage=plan.usage)
        return plan
    scope = classify_search_scope(message)
    target = {
        "repository": "documents",
        "workflow": "tickets",
        "both": "both",
        "other": pending.target if pending.target in {"documents", "tickets", "both"} else "both",
    }[scope]
    return SearchPlan(
        target=target,
        query=pending.query,
        source="history",
        usage=plan.usage,
    )


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
    """Ask GPT-5 nano for the file keyword. Fall back to keyword rules."""
    rules = plan_from_rules(message, specific_id=specific_id)
    if not search_llm_enabled():
        return lookup_override_plan(rules, message, specific_id)
    payload = {
        "message": message,
        "history": _history_snippet(history or []),
    }
    overrides = resolve_preset_overrides(KEYWORD_PRESET_ID) or {}
    logger.info(
        "chatbot_keyword_model",
        extra={"preset_id": KEYWORD_PRESET_ID, "model": overrides.get("model") or ""},
    )
    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            **overrides,
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
