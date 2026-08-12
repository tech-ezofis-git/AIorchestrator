"""Placeholder EZOFIS API client.

This is intentionally mocked. Real EZOFIS API base URL(s), auth scheme
(likely OAuth2/API-key — TBD), and endpoint paths are NOT known at the time
this scaffold was generated, so nothing here should be treated as a real
integration. Every method below is a TODO that currently returns static
mock data so the rest of the orchestrator (context manager, future agents)
can be built and tested against a stable interface.

When real EZOFIS details are available, wire them in here — e.g.:
  - an httpx.AsyncClient configured with the real base URL + auth headers
  - proper error handling / retries for the actual API's failure modes
  - request/response models matching EZOFIS's real payloads
No other module should need to change; they only depend on this class's
method signatures.
"""
import hashlib
from typing import Any


class EzofisClient:
    def __init__(self):
        # TODO(phase-4+): load real EZOFIS base URL + credentials from
        # Settings once they're defined (e.g. EZOFIS_BASE_URL, EZOFIS_API_KEY).
        pass

    async def get_user_context(self, user_id: str) -> dict[str, Any]:
        """TODO: replace with a real call to EZOFIS's user/profile endpoint.

        Expected to eventually return things like the user's roles,
        permissions, department, and default workspace — used to scope
        what agents are allowed to do/see on the user's behalf.
        """
        return {
            "user_id": user_id,
            "display_name": "Mock User",
            "roles": ["employee"],
            "department": "Unknown",
            "mock": True,
        }

    async def get_document(self, document_id: str) -> dict[str, Any]:
        """TODO: replace with a real call to EZOFIS's document retrieval endpoint.

        Not used by Phase 1 (Chat has no document access), included as an
        extension point for the Search/Summary/OCR agents in later phases.
        Note: this returns metadata only (content=None) — see
        fetch_document below for the Phase 3a Summary agent, which needs
        actual content to summarize.
        """
        return {
            "document_id": document_id,
            "title": "Mock Document",
            "content": None,
            "mock": True,
        }

    async def fetch_document(self, document_id: str) -> dict[str, Any]:
        """TODO: replace with a real call to EZOFIS's document content
        endpoint (auth mechanism, base URL, and response shape are all
        unknown at the time this was written — do not treat this as real).

        Used by the Phase 3a Summary agent (app/agents/summary_agent.py)
        via the `fetch_document` tool (app/tools/fetch_document.py), which
        calls this through the Dispatcher rather than directly. Returns
        realistic-shaped placeholder content so summarization has
        something real to work with.
        """
        return {
            "document_id": document_id,
            "title": f"Mock Document {document_id}",
            "content": (
                f"This is placeholder content for EZOFIS document '{document_id}'. "
                "In a real integration this would be the document's actual text "
                "(or an extracted/OCR'd version of it) fetched from EZOFIS. "
                "For now it exists only so the Summary agent has real text to "
                "summarize during development and testing."
            ),
            "mock": True,
        }

    async def fetch_invoice_status(self, invoice_reference: str) -> dict[str, Any]:
        """TODO: replace with a real call to EZOFIS's AP/ERP invoice status
        endpoint (auth mechanism, base URL, and response shape are all
        unknown at the time this was written — do not treat this as real).

        Used by the Phase 3c AP agent (app/agents/ap_agent.py) via the
        `fetch_invoice_status` tool (app/tools/fetch_invoice_status.py),
        which calls this through the Dispatcher rather than directly.
        Returns realistic-shaped placeholder AP data (status, amount,
        dates, approver, ...), deterministic per `invoice_reference` (same
        reference always yields the same mock data, keeping tests
        reproducible).
        """
        digest = hashlib.sha256(invoice_reference.encode()).hexdigest()
        statuses = ("Pending Approval", "Approved", "Paid", "Overdue")
        status = statuses[int(digest[:2], 16) % len(statuses)]
        amount = round(500 + (int(digest[2:8], 16) % 2_000_000) / 100, 2)
        return {
            "invoice_reference": invoice_reference,
            "status": status,
            "amount": amount,
            "currency": "USD",
            "vendor": f"Mock Vendor for {invoice_reference}",
            "invoice_date": "2026-06-01",
            "due_date": "2026-07-01",
            "approver": "Mock Approver",
            "mock": True,
        }

    async def fetch_report_data(self, report_id: str) -> dict[str, Any]:
        """TODO: replace with a real call to EZOFIS's reporting/analytics
        endpoint (auth mechanism, base URL, and response shape are all
        unknown at the time this was written — do not treat this as real).

        Used by the Phase 3a Insight agent (app/agents/insight_agent.py)
        via the `fetch_report_data` tool (app/tools/fetch_report_data.py),
        which calls this through the Dispatcher rather than directly.
        Returns a realistic-shaped list of labeled data points so insight
        synthesis has something real to cite.
        """
        return {
            "report_id": report_id,
            "data_points": [
                {"label": "Open Invoices", "value": 42},
                {"label": "Overdue Invoices", "value": 7},
                {"label": "Total Outstanding ($)", "value": 128450.75},
                {"label": "Avg Days to Payment", "value": 18.4},
            ],
            "mock": True,
        }
