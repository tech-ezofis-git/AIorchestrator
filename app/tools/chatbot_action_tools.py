"""Confirm-gated Chatbot Core action tools (Phases 3–5).

These tools never run via Dispatcher.dispatch() — only via
dispatch_confirmed() after POST /actions/{action_id}/confirm.
Propose via NL extraction or structured ``payload.propose_action``.
"""
from __future__ import annotations

from typing import Any, Optional

from app.integrations.ezofis_client import EzofisClient
from app.models.tool_schema import ToolSchema

CHATBOT_START_WORKFLOW_SCHEMA = ToolSchema(
    name="chatbot_start_workflow",
    description="Start an EZOFIS workflow instance (confirm before execute).",
    parameters={
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "workflow_id": {"type": "string"},
            "context": {"type": "string"},
            "env_type": {"type": "string"},
            "form_data": {"type": "object"},
            "skills": {"type": "array", "items": {"type": "string"}},
            "attachment_file_name": {"type": "string"},
            "attachment_content_base64": {"type": "string"},
            "attachment_content_type": {"type": "string"},
        },
        "required": ["tenant_id", "workflow_id"],
    },
    requires_confirmation=True,
)

CHATBOT_UPLOAD_REPOSITORY_FILE_SCHEMA = ToolSchema(
    name="chatbot_upload_repository_file",
    description="Upload a file to an EZOFIS repository (confirm before execute).",
    parameters={
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "repository_id": {"type": "string"},
            "file_name": {"type": "string"},
            "content_base64": {"type": "string"},
            "content_type": {"type": "string"},
            "workflow_id": {"type": "string"},
            "instance_id": {"type": "string"},
            "process_id": {"type": "string"},
            "transaction_id": {"type": "string"},
        },
        "required": ["tenant_id", "repository_id", "file_name", "content_base64"],
    },
    requires_confirmation=True,
)

CHATBOT_CREATE_USER_SCHEMA = ToolSchema(
    name="chatbot_create_user",
    description="Create an EZOFIS user (confirm before execute).",
    parameters={
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "email": {"type": "string"},
            "display_name": {"type": "string"},
            "password": {"type": "string"},
            "role": {"type": "string"},
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "user_name": {"type": "string"},
            "department": {"type": "string"},
        },
        "required": ["tenant_id", "email", "display_name"],
    },
    requires_confirmation=True,
)

CHATBOT_START_TICKET_SCHEMA = ToolSchema(
    name="chatbot_start_ticket_with_attachments",
    description=(
        "Start a workflow ticket and optionally attach file(s) "
        "(confirm before execute)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "workflow_id": {"type": "string"},
            "repository_id": {"type": "string"},
            "context": {"type": "string"},
            "form_data": {"type": "object"},
            "file_name": {"type": "string"},
            "content_base64": {"type": "string"},
            "content_type": {"type": "string"},
            "extra_attachments": {"type": "array"},
        },
        "required": ["tenant_id", "workflow_id"],
    },
    requires_confirmation=True,
)

CHATBOT_ACTION_TOOL_NAMES = frozenset(
    {
        CHATBOT_START_WORKFLOW_SCHEMA.name,
        CHATBOT_UPLOAD_REPOSITORY_FILE_SCHEMA.name,
        CHATBOT_CREATE_USER_SCHEMA.name,
        CHATBOT_START_TICKET_SCHEMA.name,
    }
)

# Phase 4–5 NL extraction (all confirm-gated chatbot actions).
CHATBOT_NL_ACTION_TOOLS = frozenset(CHATBOT_ACTION_TOOL_NAMES)


def make_chatbot_start_workflow_handler(ezofis_client: EzofisClient):
    async def handler(
        *,
        tenant_id: str,
        workflow_id: str,
        context: Optional[str] = None,
        env_type: Optional[str] = None,
        form_data: Optional[dict[str, Any]] = None,
        skills: Optional[list[str]] = None,
        attachment_file_name: Optional[str] = None,
        attachment_content_base64: Optional[str] = None,
        attachment_content_type: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        return await ezofis_client.start_workflow(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            context=context,
            env_type=env_type,
            form_data=form_data,
            skills=skills,
            attachment_file_name=attachment_file_name,
            attachment_content_base64=attachment_content_base64,
            attachment_content_type=attachment_content_type,
        )

    return handler


def make_chatbot_upload_repository_file_handler(ezofis_client: EzofisClient):
    async def handler(
        *,
        tenant_id: str,
        repository_id: str,
        file_name: str,
        content_base64: str,
        content_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        process_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        return await ezofis_client.upload_repository_file(
            tenant_id=tenant_id,
            repository_id=repository_id,
            file_name=file_name,
            content_base64=content_base64,
            content_type=content_type,
            workflow_id=workflow_id,
            instance_id=instance_id,
            process_id=process_id,
            transaction_id=transaction_id,
        )

    return handler


def make_chatbot_create_user_handler(ezofis_client: EzofisClient):
    async def handler(
        *,
        tenant_id: str,
        email: str,
        display_name: str,
        password: Optional[str] = None,
        role: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        user_name: Optional[str] = None,
        department: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        return await ezofis_client.create_user(
            tenant_id=tenant_id,
            email=email,
            display_name=display_name,
            password=password,
            role=role,
            first_name=first_name,
            last_name=last_name,
            user_name=user_name,
            department=department,
        )

    return handler


def make_chatbot_start_ticket_handler(ezofis_client: EzofisClient):
    async def handler(
        *,
        tenant_id: str,
        workflow_id: str,
        repository_id: Optional[str] = None,
        context: Optional[str] = None,
        form_data: Optional[dict[str, Any]] = None,
        file_name: Optional[str] = None,
        content_base64: Optional[str] = None,
        content_type: Optional[str] = None,
        extra_attachments: Optional[list[dict[str, Any]]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        return await ezofis_client.start_ticket_with_attachments(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            repository_id=repository_id,
            context=context,
            form_data=form_data,
            file_name=file_name,
            content_base64=content_base64,
            content_type=content_type,
            extra_attachments=extra_attachments,
        )

    return handler
