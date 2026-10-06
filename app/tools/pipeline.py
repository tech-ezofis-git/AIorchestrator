"""Document pipeline — chains the tools, skipping the ones the input makes unnecessary.

    ocr_text given      → Prompt Builder → LLM Reasoner
    file uploaded       → OCR → Prompt Builder → LLM Reasoner
    filepath given      → File Fetcher → OCR → Prompt Builder → LLM Reasoner

The pipeline result is generic. Each agent registers an `AgentProfile` whose
`adapter` turns that result into the agent's own response shape, so the same
tools serve any agent without changing their output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from app.tools.file_fetcher import fetch_file_by_path
from app.tools.llm_reasoner import run_llm_reasoning
from app.tools.ocr_tool import run_ocr_tool
from app.tools.progress_reporter import InstanceProgress
from app.tools.prompt_builder import build_llm_prompt

logger = logging.getLogger("orchestrator.tools.pipeline")

NO_INPUT_ERROR = "Provide OCR text, a file upload, or a file path."

Adapter = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
StepHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AgentProfile:
    """How one agent uses the pipeline.

    `adapter(pipeline_result, document_job)` returns the agent's response dict.
    `user_template` shapes the Prompt Builder's user message ({text}, {source},
    {page_label}, {page_suffix}, {instruction}); None keeps the builder default.
    `progress_steps` maps a step name to its (message, percent) workflow update;
    `response_error(response)` reports a failure the adapter found after the
    pipeline itself succeeded (e.g. unparseable LLM JSON).
    """

    intent: str
    adapter: Adapter
    user_template: Optional[str] = None
    default_pageno: Optional[str] = None
    progress_steps: Mapping[str, tuple[str, int]] = field(default_factory=dict)
    progress_done: str = "Completed"
    response_error: Optional[Callable[[dict[str, Any]], Optional[str]]] = None


_PROFILES: dict[str, AgentProfile] = {}


def register_profile(profile: AgentProfile) -> None:
    _PROFILES[profile.intent] = profile


def get_profile(intent: str) -> AgentProfile:
    try:
        return _PROFILES[intent]
    except KeyError:
        raise KeyError(f"No pipeline profile registered for intent '{intent}'.") from None


class DispatcherOcrEngine:
    """Lets `run_ocr_tool` call OCR through the Dispatcher's `run_ocr` tool."""

    def __init__(self, dispatcher: Any):
        self._dispatcher = dispatcher

    async def run_ocr(
        self,
        reference: str = "",
        *,
        filepath: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        page_selection: Any = None,
        tenant_id: Optional[str] = None,
        layout: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        return await self._dispatcher.dispatch(
            "run_ocr",
            {
                "reference": reference,
                "filepath": filepath,
                "tenant_id": tenant_id,
                "filename": filename,
                "content_type": content_type,
                "file_bytes": file_bytes,
                "page_start": page_selection.start,
                "page_end": page_selection.end,
                "page_raw": page_selection.raw,
                "layout": layout,
            },
        )


def _new_result(intent: str) -> dict[str, Any]:
    return {
        "intent": intent,
        "status": "FAILED",
        "failed_step": None,
        "error": None,
        "source": None,
        "page_label": "",
        "ocr_text": "",
        "steps": {},
        "reasoning": None,
        "usage": None,
    }


def _fail(result: dict[str, Any], step: str, error: Optional[str]) -> dict[str, Any]:
    result["failed_step"] = step
    result["error"] = error or f"{step} failed."
    return result


async def run_document_pipeline(
    *,
    intent: str,
    job: dict[str, Any],
    ocr_engine: Any,
    llm: Any,
    user_template: Optional[str] = None,
    default_pageno: Optional[str] = None,
    llm_overrides: Optional[dict[str, Any]] = None,
    fallback_overrides: Sequence[dict[str, Any]] = (),
    on_step: Optional[StepHook] = None,
) -> dict[str, Any]:
    """Run the tools the input needs and return every step's output.

    `job` keys used: ocr_text, file_bytes, filename, content_type, filepath,
    pageno, tenant_id, instruction, login_email, login_password. Never raises; `failed_step` + `error` say
    where it stopped. `fallback_overrides` are tried in order if the LLM call fails.
    `on_step(name)` is awaited just before each tool that actually runs.
    """
    result = _new_result(intent)
    steps = result["steps"]
    tenant_id = (job.get("tenant_id") or "").strip() or None
    pageno = job.get("pageno") or default_pageno

    async def starting(step: str) -> None:
        if on_step is not None:
            await on_step(step)

    ocr_text = job.get("ocr_text") or ""
    if ocr_text.strip():
        result["source"] = "ocr_text"
        result["page_label"] = "supplied text"
    else:
        file_bytes = job.get("file_bytes")
        filename = job.get("filename")
        content_type = job.get("content_type")
        filepath = (job.get("filepath") or "").strip() or None
        if file_bytes is not None:
            result["source"] = filename or "upload"
        elif filepath:
            result["source"] = filepath
            await starting("file_fetcher")
            fetched = await fetch_file_by_path(
                tenant_id=tenant_id or "",
                path=filepath,
                login_email=job.get("login_email"),
                login_password=job.get("login_password"),
            )
            file_bytes = fetched.pop("file_bytes")
            steps["file_fetcher"] = fetched
            if fetched["status"] != "SUCCEEDED":
                return _fail(result, "file_fetcher", fetched["error"])
            filename = fetched["fileName"]
            content_type = fetched["contentType"]
        else:
            return _fail(result, "input", NO_INPUT_ERROR)

        await starting("ocr")
        ocr = await run_ocr_tool(
            ocr_engine,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            pageno=pageno,
            layout=True,
            tenant_id=tenant_id,
        )
        steps["ocr"] = ocr
        result["page_label"] = ocr.get("pages") or ""
        if ocr["status"] != "SUCCEEDED":
            return _fail(result, "ocr", ocr["error"])
        ocr_text = ocr["text"]
    result["ocr_text"] = ocr_text

    page_label = result["page_label"]
    await starting("prompt_builder")
    prepared = await build_llm_prompt(
        intent,
        ocr_text,
        instruction=job.get("instruction"),
        tenant_id=tenant_id,
        user_template=user_template,
        template_vars={
            "source": result["source"],
            "page_label": page_label,
            "page_suffix": f" ({page_label})" if page_label else "",
        },
    )
    steps["prompt_builder"] = prepared
    if prepared["status"] != "SUCCEEDED":
        return _fail(result, "prompt_builder", prepared["error"])

    await starting("llm_reasoner")
    reasoning = await run_llm_reasoning(llm, prepared, llm_overrides=llm_overrides)
    for overrides in fallback_overrides:
        if reasoning["status"] == "SUCCEEDED":
            break
        logger.warning("pipeline_llm_fallback", extra={"intent": intent, "model": overrides.get("model")})
        reasoning = await run_llm_reasoning(llm, prepared, llm_overrides=overrides)
    steps["llm_reasoner"] = reasoning
    result["usage"] = reasoning.get("usage")
    if reasoning["status"] != "SUCCEEDED":
        return _fail(result, "llm_reasoner", reasoning["error"])

    result["reasoning"] = reasoning
    result["status"] = "SUCCEEDED"
    return result


async def run_agent_pipeline(
    intent: str,
    job: dict[str, Any],
    *,
    ocr_engine: Any,
    llm: Any,
    llm_overrides: Optional[dict[str, Any]] = None,
    fallback_overrides: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Run the pipeline with the intent's registered profile and return its adapted response.

    When the job carries tenant_id + workflow_id + instance_id, each step and the
    final outcome are also reported to the workflow instance (best-effort).
    """
    profile = get_profile(intent)
    progress = InstanceProgress.from_job(job)

    async def on_step(step: str) -> None:
        entry = profile.progress_steps.get(step)
        if entry:
            await progress.update("PROCESSING", entry[0], entry[1])

    result = await run_document_pipeline(
        intent=intent,
        job=job,
        ocr_engine=ocr_engine,
        llm=llm,
        user_template=profile.user_template,
        default_pageno=profile.default_pageno,
        llm_overrides=llm_overrides,
        fallback_overrides=fallback_overrides,
        on_step=on_step if progress.enabled else None,
    )
    try:
        response = profile.adapter(result, job)
    except Exception as exc:
        await progress.update("FAILED", str(exc) or type(exc).__name__, 100)
        raise
    error = result.get("error") if result["status"] != "SUCCEEDED" else None
    if error is None and profile.response_error is not None:
        error = profile.response_error(response)
    if error:
        await progress.update("FAILED", error, 100)
    else:
        await progress.update("COMPLETED", profile.progress_done, 100)
    return response
