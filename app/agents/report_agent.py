"""Report agent — /chat Intent.REPORT wrapper around ReportAgentService."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.models.report_agent import GeneratePromptRequest, ReportSortSpec, RunReportRequest

logger = logging.getLogger("orchestrator.report_agent.handler")


class ReportAgent:
    def __init__(self, service: Any):
        self._service = service

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
        phase = str(job.get("phase") or "prompt").strip().lower()
        tenant_id = job.get("tenant_id")
        model = job.get("model")
        report_type = job.get("report_type")
        description = (job.get("description") or message or "").strip()
        workflow_name = job.get("workflow_name")
        repository_name = job.get("repository_name")
        report_prompt = job.get("report_prompt") or job.get("prompt")

        try:
            if phase in ("run", "execute", "data"):
                if not report_prompt:
                    return {
                        "reply": "reportPrompt is required for phase=run.",
                        "report_result": None,
                    }
                sort_raw = job.get("sort")
                sort_spec = None
                if isinstance(sort_raw, dict):
                    sort_spec = ReportSortSpec(
                        field=str(sort_raw.get("field") or ""),
                        direction=str(sort_raw.get("direction") or "asc"),
                    )
                elif sort_raw is not None:
                    sort_spec = sort_raw
                result = await self._service.run_report(
                    RunReportRequest(
                        report_prompt=report_prompt,
                        report_type=report_type,
                        workflow_name=workflow_name,
                        repository_name=repository_name,
                        tenant_id=tenant_id,
                        filters=job.get("filters") or {},
                        page=int(job.get("page") or 1),
                        page_size=int(job.get("page_size") or job.get("pageSize") or 25),
                        sort=sort_spec,
                        model=model,
                        include_debug=bool(job.get("include_debug")),
                    )
                )
                payload = result.model_dump(by_alias=True)
                return {
                    "reply": f"Report ready: {result.title} ({result.total_count} rows).",
                    "report_result": payload,
                    "usage": None,
                }

            # default: prompt phase
            result = await self._service.generate_prompt(
                GeneratePromptRequest(
                    report_type=report_type or "all_workflows",
                    description=description,
                    workflow_name=workflow_name,
                    repository_name=repository_name,
                    tenant_id=tenant_id,
                    model=model,
                )
            )
            payload = result.model_dump(by_alias=True)
            return {
                "reply": "Report prompt generated.",
                "report_result": payload,
                "usage": None,
            }
        except ValueError as exc:
            return {"reply": str(exc), "report_result": None}
        except RuntimeError as exc:
            logger.warning("report_agent_handle_runtime", extra={"error": str(exc)[:200]})
            return {"reply": str(exc), "report_result": None}
        except Exception as exc:
            logger.exception("report_agent_handle_failed")
            return {"reply": f"Report Agent failed: {exc}", "report_result": None}
