"""Detect NL chatbot actions and extract tool args via LLM (Phases 4–5)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.llm.adapter import LLMAdapter
from app.tools.chatbot_action_tools import CHATBOT_NL_ACTION_TOOLS

logger = logging.getLogger("orchestrator.chatbot.nl_actions")

_ACTION_TRIGGERS = (
    "start workflow",
    "initiate workflow",
    "start the workflow",
    "run workflow",
    "start that workflow",
    "upload to",
    "upload file",
    "upload this",
    "upload document",
    "upload the",
    "start ticket",
    "create ticket",
    "open ticket",
    "new ticket",
    "start a ticket",
    "attach to",
    "with attachment",
    "with attachments",
    "create user",
    "create a user",
    "add user",
    "add a user",
    "new user",
    "invite user",
    "invite a user",
)

_CONFIRM_YES = frozenset(
    {
        "yes",
        "y",
        "confirm",
        "ok",
        "okay",
        "do it",
        "go ahead",
        "proceed",
        "yes confirm",
        "confirm it",
    }
)

_SYSTEM = (
    "You extract EZOFIS Chatbot action arguments. Reply with ONLY JSON "
    '(no markdown): {"tool":"<name>|null","arguments":{},"clarification":null|string}. '
    "Allowed tools: chatbot_start_workflow, chatbot_upload_repository_file, "
    "chatbot_start_ticket_with_attachments, chatbot_create_user. "
    "Use prior_hits to resolve pronouns like 'that workflow' / 'Accounts Payable repo'. "
    "arguments must include GUID ids when available (workflow_id, repository_id). "
    "Do not invent GUIDs. If required ids are missing, set tool null and clarification. "
    "Do not include content_base64 unless the user message or upload_file already has it. "
    "For chatbot_create_user require email + display_name; include password only if the "
    "user explicitly provided one — never invent a password."
)


@dataclass
class NlActionResult:
    tool: Optional[str]
    arguments: dict[str, Any]
    clarification: Optional[str]
    usage: Optional[dict] = None


def looks_like_action(message: str) -> bool:
    lowered = (message or "").strip().lower()
    if not lowered:
        return False
    return any(t in lowered for t in _ACTION_TRIGGERS)


def looks_like_confirm(message: str) -> bool:
    return (message or "").strip().lower() in _CONFIRM_YES


def compact_hits(hits: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        id_obj = hit.get("id") if isinstance(hit.get("id"), dict) else {}
        out.append(
            {
                "type": hit.get("type") or hit.get("entity_type"),
                "name": hit.get("ifileName")
                or hit.get("name")
                or hit.get("entity_name")
                or hit.get("matched_value"),
                "requestNo": hit.get("requestNo") or id_obj.get("requestNo"),
                "id": {
                    k: id_obj.get(k)
                    for k in (
                        "workflowId",
                        "workflowName",
                        "repositoryId",
                        "repositoryName",
                        "instanceId",
                        "itemId",
                        "requestNo",
                    )
                    if id_obj.get(k) not in (None, "", 0, "0")
                },
            }
        )
        if len(out) >= limit:
            break
    return out


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


async def extract_action_with_llm(
    llm: LLMAdapter,
    *,
    message: str,
    hits: list[dict[str, Any]],
    tenant_id: str,
    upload_file: Optional[dict[str, Any]] = None,
) -> NlActionResult:
    """Ask the LLM to pick a chatbot action tool and fill arguments from hits."""
    payload = {
        "message": message,
        "tenant_id": tenant_id,
        "prior_hits": compact_hits(hits),
        "upload_file": (
            {
                "file_name": (upload_file or {}).get("file_name"),
                "content_type": (upload_file or {}).get("content_type"),
                "has_content": bool((upload_file or {}).get("content_base64")),
            }
            if upload_file
            else None
        ),
    }
    result = await llm.chat_completion(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
    )
    usage = result.get("usage")
    data = _parse_json_object(str(result.get("content") or ""))
    if not data:
        return NlActionResult(
            tool=None,
            arguments={},
            clarification=(
                "I couldn't understand that action. Try naming a workflow, repository, "
                "or say create user with email and display name."
            ),
            usage=usage,
        )
    tool = str(data.get("tool") or "").strip() or None
    clarification = data.get("clarification")
    clarification_s = str(clarification).strip() if clarification else None
    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    args = dict(args)
    if not str(args.get("tenant_id") or "").strip():
        args["tenant_id"] = tenant_id

    # Merge upload bytes from Console when the LLM selected an upload/ticket tool.
    if upload_file and tool in {
        "chatbot_upload_repository_file",
        "chatbot_start_ticket_with_attachments",
    }:
        if upload_file.get("file_name") and not args.get("file_name"):
            args["file_name"] = upload_file["file_name"]
        if upload_file.get("content_base64") and not args.get("content_base64"):
            args["content_base64"] = upload_file["content_base64"]
        if upload_file.get("content_type") and not args.get("content_type"):
            args["content_type"] = upload_file["content_type"]

    if tool and tool not in CHATBOT_NL_ACTION_TOOLS:
        return NlActionResult(
            tool=None,
            arguments={},
            clarification=f"Action '{tool}' is not available here.",
            usage=usage,
        )
    if not tool:
        return NlActionResult(
            tool=None,
            arguments={},
            clarification=clarification_s
            or "I need a workflow, repository, or user details to continue.",
            usage=usage,
        )
    return NlActionResult(tool=tool, arguments=args, clarification=clarification_s, usage=usage)
