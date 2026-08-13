"""The AP (Accounts Payable) agent — legacy invoice-status Q&A, or an
agentic document job (`intent=ap` + file/filepath/invoice_json) that runs
tenant-gated skills at 1 credit each.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.agents.reference_extraction import extract_invoice_reference
from app.ap_skills.runner import ApSkillRunner
from app.ap_skills.store import ApStore
from app.config import Settings
from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer

logger = logging.getLogger("orchestrator.ap_agent")

_CLARIFICATION_REPLY = (
    "I couldn't identify a specific invoice reference in your message. "
    "Please provide one (e.g. 'INV-1234') so I can look up its status."
)


class ApAgent:
    def __init__(
        self,
        dispatcher: Dispatcher,
        response_composer: ResponseComposer,
        *,
        settings: Optional[Settings] = None,
        ezofis_client: Any = None,
        llm_adapter: Any = None,
        db_pool: Any = None,
        tenant_pools: Any = None,
    ):
        self._dispatcher = dispatcher
        self._response_composer = response_composer
        self._settings = settings
        self._ezofis = ezofis_client
        self._llm = llm_adapter
        self._db_pool = db_pool
        self._tenant_pools = tenant_pools
        self._runner: Optional[ApSkillRunner] = None

    def _cfg(self) -> Settings:
        if self._settings is None:
            from app.config import get_settings

            return get_settings()
        return self._settings

    def _skill_runner(self) -> ApSkillRunner:
        if self._runner is None:
            if self._db_pool is None or self._ezofis is None:
                raise RuntimeError("AP document jobs require a database pool and Ezofis client.")
            self._runner = ApSkillRunner(
                store=ApStore(self._db_pool, tenant_pools=self._tenant_pools),
                ezofis=self._ezofis,
                settings=self._cfg(),
                dispatcher=self._dispatcher,
                llm=self._llm,
            )
        return self._runner

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: object,
    ) -> dict:
        if document_job:
            result = await self._skill_runner().run(session_id=session_id, document_job=document_job)
            return {
                "reply": json.dumps(result, default=str),
                "usage": None,
                "ap_result": result,
                "invoice_reference": None,
            }

        invoice_reference = extract_invoice_reference(message)
        if invoice_reference is None:
            logger.info("ap_reference_not_found", extra={"session_id": session_id, "outcome": "not_found"})
            return {"reply": _CLARIFICATION_REPLY, "usage": None, "invoice_reference": None}

        logger.info(
            "ap_reference_identified",
            extra={"invoice_reference": invoice_reference, "outcome": "found"},
        )
        invoice = await self._dispatcher.dispatch(
            "fetch_invoice_status", {"invoice_reference": invoice_reference}
        )
        synthesis = await self._response_composer.synthesize_ap_status(invoice=invoice)
        return {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "invoice_reference": invoice_reference,
        }
