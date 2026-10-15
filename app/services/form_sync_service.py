"""Orchestration service for the Generic Form-to-Master AI Agent."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.models.form_sync import FormSyncRequest, FormSyncResponse
from app.packaging_service import get_tenant_db_connection
from app.prompts.form_sync_prompts import FORM_SYNC_SYSTEM_PROMPT, build_form_sync_user_prompt
from app.services.dynamic_db_executor import execute_form_sync_transaction
from app.services.schema_introspector import (
    format_schema_for_llm,
    introspect_form_schema,
)

logger = logging.getLogger("orchestrator.form_sync_service")


def clean_llm_json_response(raw_text: str) -> Dict[str, Any]:
    """Strip markdown code fences and parse JSON cleanly from LLM response."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]

    return json.loads(text)


class FormSyncService:
    def __init__(self, llm_adapter: Any):
        self.llm_adapter = llm_adapter

    async def sync_forms(self, request: FormSyncRequest) -> FormSyncResponse:
        start_time = time.perf_counter()
        conn = None

        try:
            # 1. Connect to Tenant Database
            conn = get_tenant_db_connection(request.tenant_id)

            # 2. Introspect Source and Target Schemas
            source_schema = introspect_form_schema(conn, request.source_form_id)
            target_schema = introspect_form_schema(conn, request.target_form_id)

            if not source_schema.get("table_exists"):
                raise ValueError(
                    f"Physical table for source form '{request.source_form_id}' does not exist: dbo.{source_schema.get('table_name')}"
                )
            if not target_schema.get("table_exists"):
                raise ValueError(
                    f"Physical table for target form '{request.target_form_id}' does not exist: dbo.{target_schema.get('table_name')}"
                )

            # 3. Format Schemas for LLM
            source_schema_text = format_schema_for_llm(source_schema, include_system_cols=False)
            target_schema_text = format_schema_for_llm(target_schema, include_system_cols=False)
            payload_text = json.dumps(request.payload, indent=2)

            # 4. Construct Prompt & Call LLM
            user_prompt = build_form_sync_user_prompt(
                source_schema_text=source_schema_text,
                target_schema_text=target_schema_text,
                payload_json_text=payload_text,
            )

            messages = [
                {"role": "system", "content": FORM_SYNC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            model_to_use = request.model or "gpt-4.1-mini"
            logger.info(
                "Calling LLM (%s) for FormSync: source=%s, target=%s",
                model_to_use,
                source_schema["form_name"],
                target_schema["form_name"],
            )

            llm_result = await self.llm_adapter.chat_completion(messages, model=model_to_use)
            raw_content = llm_result.get("content", "")

            # 5. Parse & Validate LLM Output
            parsed_plan = clean_llm_json_response(raw_content)
            reasoning = parsed_plan.get("reasoning", "")
            source_record_clean = parsed_plan.get("source_record_clean") or request.payload
            target_records = parsed_plan.get("target_records") or []

            # 6. Execute Database Writes (unless dry_run is requested)
            if request.dry_run:
                execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return FormSyncResponse(
                    success=True,
                    source_record_id="SIMULATED_SOURCE_ID",
                    target_records_count=len(target_records),
                    target_records=target_records,
                    execution_time_ms=execution_ms,
                    message=f"[DRY RUN] Generated plan for {len(target_records)} master record(s) without DB write.",
                    reasoning=reasoning,
                )

            db_result = execute_form_sync_transaction(
                conn=conn,
                source_schema=source_schema,
                target_schema=target_schema,
                source_record_data=source_record_clean,
                target_records_plan=target_records,
            )

            execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return FormSyncResponse(
                success=True,
                source_record_id=db_result.get("source_record_id"),
                target_records_count=db_result.get("target_records_count", 0),
                target_records=db_result.get("target_records", []),
                execution_time_ms=execution_ms,
                message=db_result.get("message", "Form synchronization completed."),
                reasoning=reasoning,
            )

        except Exception as exc:
            logger.error("Error in FormSyncService: %s", exc, exc_info=True)
            execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return FormSyncResponse(
                success=False,
                source_record_id=None,
                target_records_count=0,
                target_records=[],
                execution_time_ms=execution_ms,
                message=f"Form synchronization failed: {str(exc)}",
                reasoning=None,
            )
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
