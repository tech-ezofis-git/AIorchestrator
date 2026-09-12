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
    rewrite_search_query,
)
from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.core.pending_actions import PendingActionStore
from app.global_search.merge import build_result
from app.global_search.runner import run_global_search
from app.global_search.sql_search import normalize_query
from app.global_search.types import SearchHit
from app.llm.adapter import LLMAdapter

logger = logging.getLogger("orchestrator.chatbot_agent")


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

        # NL questions → keyword tokens so "what invoices for ACME" still searches.
        query = rewrite_search_query(user_text) or normalize_query(user_text)
        result = await run_global_search(
            self._dispatcher,
            query=query,
            tenant_id=tenant_id,
            specific_id=specific_id,
            limit=self._limit,
            rag_limit=self._rag_limit,
            include_comments_tickets=True,
        )
        formatted = format_search_blocks(
            result,
            workspace_id=workspace_id,
            specific_id=specific_id,
        )
        return self._pack(
            user_text=query,
            reply=formatted["reply"],
            blocks=formatted["text"]["blocks"],
            hits=formatted["hits"],
            tenant_id=tenant_id,
            specific_id=specific_id or None,
            action=formatted.get("action"),
            action_to=formatted.get("actionTo"),
            action_context=formatted.get("actionContext"),
        )

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
