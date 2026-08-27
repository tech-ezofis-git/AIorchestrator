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
    ocr_text = _form_str(form.get("ocr_text"))
    model = _form_str(form.get("model"))
    tenant_id = _form_str(form.get("tenant_id"))
    form_id = (
        _form_str(form.get("form_id"))
        or _form_str(form.get("formid"))
        or _form_str(form.get("formId"))
    )
    item_id = _form_str(form.get("item_id")) or _form_str(form.get("itemId")) or _form_str(form.get("ItemId"))
    repository_item_id = (
        _form_str(form.get("repository_item_id"))
        or _form_str(form.get("repositoryItemId"))
        or _form_str(form.get("repositoryItemID"))
    )
    workflow_id = (
        _form_str(form.get("workflow_id"))
        or _form_str(form.get("workflowId"))
    )
    instance_id = (
        _form_str(form.get("instance_id"))
        or _form_str(form.get("instanceId"))
    )
    repository_id = (
        _form_str(form.get("repository_id"))
        or _form_str(form.get("repositoryId"))
        or _form_str(form.get("repository"))
    )
    transaction_id = (
        _form_str(form.get("transaction_id"))
        or _form_str(form.get("transactionId"))
    )
    form_entry_id = (
        _form_str(form.get("form_entry_id"))
        or _form_str(form.get("formentryId"))
        or _form_str(form.get("formEntryId"))
    )
    process_id = (
        _form_str(form.get("process_id"))
        or _form_str(form.get("processId"))
    )
    activity_id = (
        _form_str(form.get("activity_id"))
        or _form_str(form.get("activityid"))
        or _form_str(form.get("activityId"))
        or _form_str(form.get("ActivityId"))
    )
    connector_id = _form_str(form.get("connector_id"))
    resource = _form_str(form.get("resource"))
    matter_master_id = _form_str(form.get("matter_master_id"))
    parameters = _parse_form_string_list(form, "parameters")
    tableparameters = _parse_form_string_list(form, "tableparameters")
    skills_raw = _form_str(form.get("skills"))
    skills = _parse_optional_string_list(form, "skills")
    invoice_json = _parse_optional_json_object(_form_str(form.get("invoice_json")), field="invoice_json")
    insight_json = _parse_optional_json_object(_form_str(form.get("insight_json")), field="insight_json")
    summary_json = _parse_optional_json_object(_form_str(form.get("summary_json")), field="summary_json")
    key_facts_count = _parse_optional_int(_form_str(form.get("key_facts_count")), field="key_facts_count")
    insights_count = _parse_optional_int(_form_str(form.get("insights_count")), field="insights_count")
    insight_area = _form_str(form.get("insight_area"))

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
    has_ap_fields = bool(
        tenant_id
        or item_id
        or skills_raw is not None
        or skills
        or invoice_json
        or workflow_id
        or instance_id
        or repository_id
        or transaction_id
        or form_entry_id
        or process_id
        or activity_id
        or repository_item_id
        or connector_id
        or resource
        or matter_master_id
        or form_id
    )
    if (
        filepath
        or pageno
        or ocr_text
        or parameters
        or tableparameters
        or model
        or file_bytes is not None
        or has_ap_fields
        or insight_json
        or summary_json
        or key_facts_count is not None
        or insights_count is not None
        or insight_area
    ):
        payload = DocumentPayload(
            filepath=filepath,
            pageno=pageno,
            ocr_text=ocr_text,
            summary_json=summary_json,
            key_facts_count=key_facts_count,
            insight_json=insight_json,
            insights_count=insights_count,
            insight_area=insight_area,
            parameters=parameters,
            tableparameters=tableparameters,
            model=model,
            tenant_id=tenant_id,
            skills=skills if skills_raw is not None or skills else None,
            invoice_json=invoice_json,
            item_id=item_id,
            repository_item_id=repository_item_id,
            workflow_id=workflow_id,
            instance_id=instance_id,
            repository_id=repository_id,
            transaction_id=transaction_id,
            form_entry_id=form_entry_id,
            process_id=process_id,
            activity_id=activity_id,
            connector_id=connector_id,
            resource=resource,
            matter_master_id=matter_master_id,
            form_id=form_id,
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


def _parse_optional_string_list(form: Any, field: str) -> Optional[list[str]]:
    """Like `_parse_form_string_list` but None when the field is omitted."""
    values = form.getlist(field) if hasattr(form, "getlist") else [form.get(field)]
    values = [v for v in values if v is not None and not isinstance(v, UploadFile)]
    if not values:
        return None
    parsed = _parse_form_string_list(form, field)
    return parsed


def _parse_optional_json_object(raw: Optional[str], *, field: str) -> Optional[dict[str, Any]]:
    if raw is None or raw == "":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be a JSON object string.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail=f"{field} must be a JSON object string.")
    return data


def _parse_optional_int(raw: Optional[str], *, field: str) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be an integer between 1 and 20.",
        ) from exc
    if value < 1 or value > 20:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be an integer between 1 and 20.",
        )
    return value


def _parse_form_string_list(form: Any, field: str) -> list[str]:
    """Accept Swagger multipart array fields OR a single JSON-array string."""
    values = form.getlist(field) if hasattr(form, "getlist") else [form.get(field)]
    values = [v for v in values if v is not None and not isinstance(v, UploadFile)]
    if not values:
        return []

    # Multiple form parts with the same name (OpenAPI array → Swagger UI).
    if len(values) > 1:
        return [str(v).strip() for v in values if str(v).strip()]

    return _parse_json_list(_form_str(values[0]), field=field)


def _parse_json_list(raw: Optional[str], *, field: str) -> list[str]:
    if raw is None or raw == "":
        return []
    text = (
        str(raw)
        .strip()
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    # Swagger sometimes wraps the whole JSON array in extra quotes.
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        text = text[1:-1].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Single Name,TYPE entry pasted without JSON brackets.
        if "," in text and not text.startswith("["):
            return [text]
        # Bare skill id (intent=ap), e.g. vendor_validate
        if field == "skills":
            return [text]
        raise HTTPException(
            status_code=422,
            detail=(
                f'{field} must be a JSON array string, e.g. '
                f'["Invoice No,SHORT_TEXT","Due Date,DATE"]'
            ),
        ) from None
    if isinstance(data, str):
        # Double-encoded JSON string containing an array.
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return [data]
    if not isinstance(data, list):
        raise HTTPException(
            status_code=422,
            detail=(
                f'{field} must be a JSON array string, e.g. '
                f'["Invoice No,SHORT_TEXT","Due Date,DATE"]'
            ),
        )
    return [str(x) for x in data]
