"""Chatbot Phase 3–5 — validate + format confirm-gated Core actions."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.tools.chatbot_action_tools import CHATBOT_ACTION_TOOL_NAMES

_SENSITIVE_KEYS = frozenset({"password", "content_base64", "contentBase64"})
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8
_MAX_DISPLAY_NAME = 120


def redact_action_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Copy args for UI/audit; redact secrets and truncate large blobs."""
    out: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if key in _SENSITIVE_KEYS or key.lower() in _SENSITIVE_KEYS:
            if key.lower() in {"content_base64", "contentbase64"}:
                out[key] = f"<base64 {len(str(value or ''))} chars>"
            else:
                out[key] = "***"
            continue
        out[key] = value
    return out


def _normalize_create_user_args(args: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    email = str(args.get("email") or "").strip()
    display = str(args.get("display_name") or args.get("displayName") or "").strip()
    if not email:
        return None, "email is required for chatbot_create_user."
    if not _EMAIL_RE.match(email):
        return None, "email must look like name@domain.com."
    if not display:
        return None, "display_name is required for chatbot_create_user."
    if len(display) > _MAX_DISPLAY_NAME:
        return None, f"display_name must be at most {_MAX_DISPLAY_NAME} characters."
    out = dict(args)
    out["email"] = email
    out["display_name"] = display
    password = out.get("password")
    if password is not None:
        password_s = str(password)
        if not password_s.strip():
            out.pop("password", None)
        elif len(password_s) < _MIN_PASSWORD_LEN:
            return (
                None,
                f"password must be at least {_MIN_PASSWORD_LEN} characters when provided.",
            )
    # Drop empty optional strings so Core gets clean payloads.
    for key in ("role", "first_name", "last_name", "user_name", "department"):
        if key in out and not str(out.get(key) or "").strip():
            out.pop(key, None)
    return out, None


def validate_propose_action(
    propose: Any,
    *,
    default_tenant_id: str = "",
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    """Return (tool_name, arguments, error_message)."""
    if not isinstance(propose, dict):
        return None, None, "propose_action must be an object with tool and arguments."
    tool = str(propose.get("tool") or propose.get("tool_name") or "").strip()
    if tool not in CHATBOT_ACTION_TOOL_NAMES:
        allowed = ", ".join(sorted(CHATBOT_ACTION_TOOL_NAMES))
        return None, None, f"Unknown chatbot action tool '{tool}'. Allowed: {allowed}."
    raw_args = propose.get("arguments") or propose.get("args") or {}
    if not isinstance(raw_args, dict):
        return None, None, "propose_action.arguments must be an object."
    args = dict(raw_args)
    if default_tenant_id and not str(args.get("tenant_id") or "").strip():
        args["tenant_id"] = default_tenant_id
    if not str(args.get("tenant_id") or "").strip():
        return None, None, "tenant_id is required on propose_action.arguments."
    if tool == "chatbot_start_workflow" and not str(args.get("workflow_id") or "").strip():
        return None, None, "workflow_id is required for chatbot_start_workflow."
    if tool == "chatbot_start_ticket_with_attachments":
        if not str(args.get("workflow_id") or "").strip():
            return None, None, "workflow_id is required for chatbot_start_ticket_with_attachments."
        has_b64 = bool(str(args.get("content_base64") or "").strip())
        has_name = bool(str(args.get("file_name") or "").strip())
        if has_b64 != has_name:
            return (
                None,
                None,
                "file_name and content_base64 are both required when attaching a file.",
            )
        if has_b64 and not str(args.get("repository_id") or "").strip():
            return (
                None,
                None,
                "repository_id is required when attaching files to a ticket.",
            )
    if tool == "chatbot_upload_repository_file":
        if not str(args.get("repository_id") or "").strip():
            return None, None, "repository_id is required for chatbot_upload_repository_file."
        if not str(args.get("file_name") or "").strip():
            return None, None, "file_name is required for chatbot_upload_repository_file."
        if not str(args.get("content_base64") or "").strip():
            return None, None, "content_base64 is required for chatbot_upload_repository_file."
    if tool == "chatbot_create_user":
        normalized, err = _normalize_create_user_args(args)
        if err or normalized is None:
            return None, None, err or "Invalid create-user arguments."
        args = normalized
    return tool, args, None


def success_blocks(*, tool_name: str, result: dict[str, Any]) -> list[dict]:
    """CHATBOT.md-style cards after a confirmed action executes."""
    instance_id = str(result.get("instanceId") or result.get("InstanceId") or "").strip()
    item_id = str(result.get("itemId") or result.get("ItemId") or "").strip()
    workflow_id = str(result.get("workflowId") or result.get("WorkflowId") or "").strip()
    file_name = str(result.get("fileName") or result.get("FileName") or "").strip()
    user_id = str(result.get("userId") or result.get("UserId") or "").strip()
    email = str(result.get("email") or result.get("Email") or "").strip()
    bits = []
    if instance_id:
        bits.append(f"instanceId={instance_id}")
    if item_id:
        bits.append(f"itemId={item_id}")
    if workflow_id:
        bits.append(f"workflowId={workflow_id}")
    if file_name:
        bits.append(f"file={file_name}")
    if user_id:
        bits.append(f"userId={user_id}")
    if email:
        bits.append(f"email={email}")
    mock = " (mock)" if result.get("mock") else ""
    summary = ", ".join(bits) if bits else json_safe_summary(result)
    return [
        {
            "type": "paragraph",
            "text": f"Action executed: {tool_name}{mock}.",
        },
        {
            "type": "card",
            "title": "Success",
            "subtitle": tool_name,
            "description": summary,
            "type_hint": "action_success",
            "id": {
                "instanceId": instance_id or None,
                "itemId": item_id or None,
                "workflowId": workflow_id or None,
                "userId": user_id or None,
            },
        },
    ]


def json_safe_summary(result: dict[str, Any]) -> str:
    safe = {k: v for k, v in result.items() if k not in {"payload", "attachments", "password"}}
    return str(safe)[:240]


def proposal_blocks(*, tool_name: str, action_id: str, arguments: dict[str, Any]) -> list[dict]:
    safe = redact_action_args(arguments)
    items = [{"label": k, "value": str(v)} for k, v in safe.items()]
    return [
        {
            "type": "paragraph",
            "text": (
                f"Action proposed: {tool_name}. Confirm with "
                f"POST /actions/{action_id}/confirm?session_id=… before anything runs."
            ),
        },
        {
            "type": "bullets",
            "title": "Planned arguments",
            "items": items,
        },
        {
            "type": "card",
            "title": "Confirm required",
            "subtitle": action_id,
            "description": "Nothing has been executed yet.",
            "type_hint": "action_pending",
            "id": {"actionId": action_id, "tool": tool_name},
        },
    ]
