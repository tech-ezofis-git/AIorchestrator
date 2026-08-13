"""Parse JSON or multipart POST /chat into ChatRequest + optional upload bytes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.models.chat import ChatRequest, DocumentPayload


@dataclass
class ParsedChatRequest:
    chat: ChatRequest
    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None


async def parse_chat_request(request: Request) -> ParsedChatRequest:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        return await _parse_multipart(request)
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
    try:
        chat = ChatRequest.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    return ParsedChatRequest(chat=chat)


async def _parse_multipart(request: Request) -> ParsedChatRequest:
    form = await request.form()
    session_id = _form_str(form.get("session_id"))
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required.")

    message = _form_str(form.get("message"))
    intent = _form_str(form.get("intent"))
    instruction = _form_str(form.get("instruction"))
    filepath = _form_str(form.get("filepath"))
    pageno = _form_str(form.get("pageno"))
    model = _form_str(form.get("model"))
    parameters = _parse_json_list(_form_str(form.get("parameters")), field="parameters")
    tableparameters = _parse_json_list(
        _form_str(form.get("tableparameters")), field="tableparameters"
    )

    upload = form.get("file")
    file_bytes = None
    filename = None
    content_type = None
    if upload is not None and not isinstance(upload, str):
        # UploadFile or Starlette form file-like
        if hasattr(upload, "read"):
            raw = await upload.read()
            file_bytes = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
            filename = getattr(upload, "filename", None) or "upload.bin"
            content_type = getattr(upload, "content_type", None)

    payload = None
    if filepath or pageno or parameters or tableparameters or model or file_bytes is not None:
        payload = DocumentPayload(
            filepath=filepath,
            pageno=pageno,
            parameters=parameters,
            tableparameters=tableparameters,
            model=model,
        )

    try:
        chat = ChatRequest(
            session_id=session_id,
            message=message or None,
            intent=intent or None,
            instruction=instruction or None,
            payload=payload,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc

    return ParsedChatRequest(
        chat=chat,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
    )


def _form_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, UploadFile):
        return None
    text = str(value).strip()
    return text or None


def _parse_json_list(raw: Optional[str], *, field: str) -> list[str]:
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a JSON array.") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail=f"{field} must be a JSON array.")
    return [str(x) for x in data]
