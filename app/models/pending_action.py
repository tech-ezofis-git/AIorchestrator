"""Schema for a pending confirmation-gated tool action (Phase 3d).

A PendingAction is created when a confirmation-gated tool's arguments are
ready (e.g. Mail Agent has drafted a subject+body) but before the tool
runs — the caller must see the draft and explicitly confirm via
POST /actions/{action_id}/confirm before the Dispatcher executes it for
real (see app/core/pending_actions.py, app/core/dispatcher.py).
"""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class PendingAction(BaseModel):
    action_id: str
    tool_name: str
    arguments: dict[str, Any]
    session_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConfirmActionResponse(BaseModel):
    action_id: str
    tool_name: str
    status: str
    result: dict[str, Any]
