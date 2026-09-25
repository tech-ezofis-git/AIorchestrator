"""Chatbot agent — search + confirm-gated NL/structured actions (Phases 4–5)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.chatbot.actions import proposal_blocks, validate_propose_action
from app.chatbot.format_blocks import format_search_blocks
from app.chatbot.intent_gate import classify_chatbot_intent
from app.chatbot.nl_actions import (
    extract_action_with_llm,
    looks_like_action,
    looks_like_confirm,
)
from app.chatbot.query_rewrite import (
    CatalogKind,
    catalog_label,
    catalog_tool_name,
    detect_catalog_list,
)
from app.chatbot.search_plan import (
    SearchPlan,
    friendly_search_reply,
    lookup_override_plan,
    merge_usage,
    pending_lookup_from_history,
    plan_document_ticket_search,
    repository_choice_phrase,
    tools_for_plan,
)
from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.core.pending_actions import PendingActionStore
from app.global_search.merge import build_result
from app.global_search.runner import run_global_search
from app.global_search.sql_search import normalize_query
from app.global_search.types import SearchHit
from app.llm.adapter import LLMAdapter

logger = logging.getLogger("orchestrator.chatbot_agent")


def _replace_reply(packed: dict, reply: str) -> dict:
    packed["reply"] = reply
    result = packed.get("chatbot_result")
    if isinstance(result, dict):
        blocks = (result.get("text") or {}).get("blocks") or []
        if blocks and blocks[0].get("type") == "paragraph":
            blocks[0]["text"] = reply
        else:
            blocks.insert(0, {"type": "paragraph", "text": reply})
        conversation = result.get("conversation") or []
        if conversation and conversation[-1].get("role") == "assistant":
            conversation[-1]["content"] = reply
    return packed


class ChatbotAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        pending_action_store: PendingActionStore,
        llm_adapter: LLMAdapter,
        *,
        limit: int = 20,
        rag_limit: int = 10,
    ):
        self._dispatcher = dispatcher
        self._pending_actions = pending_action_store
        self._llm = llm_adapter
        self._limit = limit
        self._rag_limit = rag_limit

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        job = document_job or {}
        tenant_id = str(job.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError("tenantId is required for intent=chatbot.")

        propose = job.get("propose_action")
        if propose is not None:
            return await self._propose(
                session_id=session_id,
                tenant_id=tenant_id,
                propose=propose,
                message=message,
            )

        user_text = (message or job.get("query") or "").strip() or "help"
        specific_id = str(job.get("specific_id") or job.get("repository_id") or "").strip()
        workspace_id = str(job.get("workspace_id") or "").strip()
        recent_hits = job.get("recent_hits") if isinstance(job.get("recent_hits"), list) else []
        upload_file = job.get("upload_file") if isinstance(job.get("upload_file"), dict) else None
        pending_action_id = str(job.get("pending_action_id") or "").strip()

        if pending_action_id and looks_like_confirm(user_text):
            return self._pack(
                user_text=user_text,
                reply=(
                    f"Confirm this action with POST /actions/{pending_action_id}/confirm"
                    f"?session_id={session_id} (Console Confirm button also works)."
                ),
                blocks=[
                    {
                        "type": "paragraph",
                        "text": (
                            "Use the Confirm button or POST "
                            f"/actions/{pending_action_id}/confirm?session_id=…"
                        ),
                    }
                ],
                hits=[],
                tenant_id=tenant_id,
                specific_id=specific_id or None,
                action={
                    "confirm_request": {
                        "actionId": pending_action_id,
                        "path": f"/actions/{pending_action_id}/confirm",
                    }
                },
                action_to=None,
                action_context=None,
                pending_action={
                    "action_id": pending_action_id,
                    "status": "awaiting_confirm",
                },
            )

        if looks_like_action(user_text):
            return await self._nl_action(
                session_id=session_id,
                tenant_id=tenant_id,
                message=user_text,
                recent_hits=recent_hits,
                upload_file=upload_file,
            )

        gate = classify_chatbot_intent(user_text)
        if gate.kind != "database_question":
            return self._pack(
                user_text=user_text,
                reply=gate.reply,
                blocks=gate.blocks,
                hits=[],
                tenant_id=tenant_id,
                specific_id=specific_id or None,
                action=None,
                action_to="Repository" if gate.kind in {"greeting", "help"} else None,
                action_context=None,
            )

        catalog = detect_catalog_list(user_text)
        if catalog:
            return await self._list_catalog(
                user_text=user_text,
                kind=catalog,
                tenant_id=tenant_id,
                specific_id=specific_id,
                workspace_id=workspace_id,
            )

        # Model picks documents, tickets, or both and the lookup text.
        # Tools stay in code. Keyword rules run when the model is unavailable.
        plan = await plan_document_ticket_search(
            self._llm,
            message=user_text,
            specific_id=specific_id,
            history=history,
        )
        choice = repository_choice_phrase(user_text) if not specific_id else ""
        if choice:
            repo_id = await self._match_repository_name(choice, tenant_id)
            if repo_id:
                pending = pending_lookup_from_history(history)
                if pending is None:
                    return self._ask_document_suggestion(
                        user_text=user_text,
                        tenant_id=tenant_id,
                        specific_id=repo_id,
                    )
                specific_id = repo_id
                plan = SearchPlan(
                    target=pending.target,
                    query=pending.query,
                    source="history",
                    usage=plan.usage,
                )
            else:
                plan = lookup_override_plan(plan, user_text, specific_id)
        logger.info(
            "chatbot_search_plan",
            extra={
                "target": plan.target,
                "source": plan.source,
                "tenant_id": tenant_id,
            },
        )
        if plan.target == "ask_repository":
            return await self._prompt_repository_choice(
                user_text=user_text,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        if plan.target == "ask_term":
            if specific_id:
                return self._ask_document_suggestion(
                    user_text=user_text,
                    tenant_id=tenant_id,
                    specific_id=specific_id,
                )
            return self._ask_lookup_suggestion(user_text=user_text, tenant_id=tenant_id)
        query = plan.query or normalize_query(user_text)
        result = await run_global_search(
            self._dispatcher,
            query=query,
            tenant_id=tenant_id,
            specific_id=specific_id,
            limit=self._limit,
            rag_limit=self._rag_limit,
            tools=tools_for_plan(plan),
        )
        formatted = format_search_blocks(
            result,
            workspace_id=workspace_id,
            specific_id=specific_id,
        )
        reply, reply_usage = await friendly_search_reply(
            self._llm,
            query=query,
            hits=formatted["hits"],
            fallback=formatted["reply"],
        )
        blocks = formatted["text"]["blocks"]
        if reply != formatted["reply"] and blocks and blocks[0].get("type") == "paragraph":
            blocks[0]["text"] = reply
        return self._pack(
            user_text=query,
            reply=reply,
            blocks=blocks,
            hits=formatted["hits"],
            tenant_id=tenant_id,
            specific_id=specific_id or None,
            action=formatted.get("action"),
            action_to=formatted.get("actionTo"),
            action_context=formatted.get("actionContext"),
            usage=merge_usage(plan.usage, reply_usage),
        )

    async def _prompt_repository_choice(
        self,
        *,
        user_text: str,
        tenant_id: str,
        workspace_id: str,
    ) -> dict:
        """No repository selected yet: list repos and ask which one to search."""
        packed = await self._list_catalog(
            user_text=user_text,
            kind="repositories",
            tenant_id=tenant_id,
            specific_id="",
            workspace_id=workspace_id,
        )
        hits = packed.get("chatbot_result", {}).get("hits") or []
        if not hits:
            return packed
        reply = (
            "Which repository should I search? Choose one below, then tell me "
            "what to look for in the documents (a file name, number, or keyword)."
        )
        return _replace_reply(packed, reply)

    def _ask_lookup_suggestion(self, *, user_text: str, tenant_id: str) -> dict:
        """No lookup value yet, and no repository is selected."""
        reply = (
            "What should I look for? Give me a file name, ticket number, or keyword "
            "such as APEX or 6001."
        )
        return self._pack(
            user_text=normalize_query(user_text),
            reply=reply,
            blocks=[{"type": "paragraph", "text": reply}],
            hits=[],
            tenant_id=tenant_id,
            specific_id=None,
            action=None,
            action_to=None,
            action_context=None,
        )

    def _ask_document_suggestion(
        self,
        *,
        user_text: str,
        tenant_id: str,
        specific_id: str,
    ) -> dict:
        """Repository is already selected: ask what to search for inside it."""
        reply = (
            "This repository is selected. Give me a suggestion to search the documents — "
            "a file name, number, or keyword such as 6001."
        )
        return self._pack(
            user_text=normalize_query(user_text),
            reply=reply,
            blocks=[{"type": "paragraph", "text": reply}],
            hits=[],
            tenant_id=tenant_id,
            specific_id=specific_id,
            action=None,
            action_to="Repository",
            action_context={"repositoryId": specific_id, "workspaceId": "", "itemId": ""},
        )

    async def _match_repository_name(self, phrase: str, tenant_id: str) -> str:
        """Return a repository id when the phrase is exactly one repository name."""
        try:
            raw = await self._dispatcher.dispatch(
                "search_repositories",
                {"query": phrase, "tenant_id": tenant_id, "limit": self._limit},
            )
        except (ToolExecutionError, ToolNotFoundError):
            logger.warning("chatbot_repository_choice_failed")
            return ""
        phrase_key = phrase.casefold()
        matched: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, SearchHit):
                    hit = item
                elif isinstance(item, dict):
                    hit = SearchHit.model_validate(item)
                else:
                    continue
                id_obj = hit.id if isinstance(hit.id, dict) else {}
                name = str(id_obj.get("repositoryName") or hit.entity_name or hit.name or "").strip()
                repo_id = str(id_obj.get("repositoryId") or hit.entity_id or "").strip()
                if repo_id and name.casefold() == phrase_key:
                    matched.append(repo_id)
        unique = list(dict.fromkeys(matched))
        if len(unique) == 1:
            return unique[0]
        return ""

    async def _list_catalog(
        self,
        *,
        user_text: str,
        kind: CatalogKind,
        tenant_id: str,
        specific_id: str,
        workspace_id: str,
    ) -> dict:
        """List repositories/workflows/forms with an empty ILIKE (%%) catalog query."""
        tool = catalog_tool_name(kind)
        label = catalog_label(kind)
        payload: dict[str, Any] = {
            "query": "",
            "tenant_id": tenant_id,
            "limit": self._limit,
        }
        if tool == "search_repositories" and specific_id:
            payload["specific_id"] = specific_id
        try:
            raw = await self._dispatcher.dispatch(tool, payload)
        except (ToolExecutionError, ToolNotFoundError):
            logger.warning("chatbot_catalog_list_failed", extra={"tool": tool, "kind": kind})
            raw = []
        hits: list[SearchHit] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, SearchHit):
                    hits.append(item)
                elif isinstance(item, dict):
                    hits.append(SearchHit.model_validate(item))
        result = build_result(f"(all {kind})", hits)
        formatted = format_search_blocks(
            result,
            workspace_id=workspace_id,
            specific_id=specific_id,
            catalog_list=label,
        )
        return self._pack(
            user_text=normalize_query(user_text),
            reply=formatted["reply"],
            blocks=formatted["text"]["blocks"],
            hits=formatted["hits"],
            tenant_id=tenant_id,
            specific_id=specific_id or None,
            action=formatted.get("action"),
            action_to=formatted.get("actionTo") or "Repository",
            action_context=formatted.get("actionContext"),
        )

    async def _nl_action(
        self,
        *,
        session_id: str,
        tenant_id: str,
        message: str,
        recent_hits: list[dict],
        upload_file: Optional[dict],
    ) -> dict:
        extracted = await extract_action_with_llm(
            self._llm,
            message=message,
            hits=recent_hits,
            tenant_id=tenant_id,
            upload_file=upload_file,
        )
        usage = extracted.usage
        if extracted.clarification and not extracted.tool:
            return self._pack(
                user_text=message,
                reply=extracted.clarification,
                blocks=[{"type": "paragraph", "text": extracted.clarification}],
                hits=[],
                tenant_id=tenant_id,
                specific_id=None,
                action=None,
                action_to=None,
                action_context=None,
                usage=usage,
            )
        propose = {"tool": extracted.tool, "arguments": extracted.arguments}
        packed = await self._propose(
            session_id=session_id,
            tenant_id=tenant_id,
            propose=propose,
            message=message,
        )
        if usage and isinstance(packed, dict):
            packed["usage"] = usage
        return packed

    async def _propose(
        self,
        *,
        session_id: str,
        tenant_id: str,
        propose: Any,
        message: str,
    ) -> dict:
        tool_name, arguments, error = validate_propose_action(
            propose, default_tenant_id=tenant_id
        )
        if error or not tool_name or arguments is None:
            reply = error or "Invalid propose_action."
            return self._pack(
                user_text=(message or "").strip() or "propose_action",
                reply=reply,
                blocks=[{"type": "paragraph", "text": reply}],
                hits=[],
                tenant_id=tenant_id,
                specific_id=None,
                action=None,
                action_to=None,
                action_context=None,
                pending_action=None,
            )

        pending = await self._pending_actions.create(
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
        )
        logger.info(
            "chatbot_action_proposed",
            extra={
                "action_id": pending.action_id,
                "tool_name": tool_name,
                "tenant_id": tenant_id,
                "outcome": "drafted",
            },
        )
        reply = (
            f"Draft ready for {tool_name} (action_id={pending.action_id}). "
            "Confirm in Console or POST /actions/{action_id}/confirm?session_id=… — nothing executed yet."
        )
        blocks = proposal_blocks(
            tool_name=tool_name,
            action_id=pending.action_id,
            arguments=arguments,
        )
        pending_payload = {
            "action_id": pending.action_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "status": "pending_confirmation",
        }
        return self._pack(
            user_text=(message or "").strip() or f"propose {tool_name}",
            reply=reply,
            blocks=blocks,
            hits=[],
            tenant_id=tenant_id,
            specific_id=None,
            action={
                "confirm_request": {
                    "actionId": pending.action_id,
                    "tool": tool_name,
                    "path": f"/actions/{pending.action_id}/confirm",
                }
            },
            action_to=None,
            action_context=None,
            pending_action=pending_payload,
        )

    @staticmethod
    def _pack(
        *,
        user_text: str,
        reply: str,
        blocks: list[dict],
        hits: list[dict],
        tenant_id: str,
        specific_id: Optional[str],
        action: Optional[dict],
        action_to: Optional[str],
        action_context: Optional[dict],
        pending_action: Optional[dict] = None,
        usage: Optional[dict] = None,
    ) -> dict:
        conversation = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ]
        result: dict[str, Any] = {
            "conversation": conversation,
            "text": {"blocks": blocks},
            "hits": hits,
            "action": action,
            "actionTo": action_to,
            "actionContext": action_context,
            "query": user_text,
            "tenantId": tenant_id,
            "specificId": specific_id,
        }
        if pending_action is not None:
            from app.chatbot.actions import redact_action_args

            safe = dict(pending_action)
            if isinstance(safe.get("arguments"), dict):
                safe["arguments"] = redact_action_args(safe["arguments"])
            result["pending_action"] = safe
        return {
            "reply": reply,
            "usage": usage,
            "chatbot_result": result,
        }
