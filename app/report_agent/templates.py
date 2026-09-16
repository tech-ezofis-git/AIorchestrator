"""Business-level definitions for the 10 supported report templates.

Templates provide business intent only (id, title, domain, description).
No database field names, table hints, or expected schema mappings are hardcoded.
"""
from __future__ import annotations

import re
from typing import Optional
from app.models.report_agent import ReportAgentTemplate


class TemplateDefinition:
    """Pure business/report metadata. Does not specify database tables or columns."""

    def __init__(
        self,
        *,
        id: str,
        title: str,
        domain: str,
        description: str,
    ):
        self.id = id
        self.title = title
        self.domain = domain
        self.description = description

    def to_model(self) -> ReportAgentTemplate:
        return ReportAgentTemplate(
            id=self.id,
            title=self.title,
            domain=self.domain,
            description=self.description,
        )


SUPPORTED_TEMPLATES: dict[str, TemplateDefinition] = {
    "tpl-accounts-payable-aging": TemplateDefinition(
        id="tpl-accounts-payable-aging",
        title="Accounts Payable Aging",
        domain="Accounts Payable",
        description="Outstanding AP requests bucketed by age.",
    ),
    "tpl-pending-workflow-requests": TemplateDefinition(
        id="tpl-pending-workflow-requests",
        title="Pending Workflow Requests",
        domain="Workflow Automation",
        description="Open workflow requests awaiting action.",
    ),
    "tpl-workflow-sla-compliance": TemplateDefinition(
        id="tpl-workflow-sla-compliance",
        title="Workflow SLA Compliance",
        domain="Workflow Automation",
        description="Completed workflows within vs. outside SLA.",
    ),
    "tpl-documents-without-recent-access": TemplateDefinition(
        id="tpl-documents-without-recent-access",
        title="Documents Without Recent Access",
        domain="Document Management",
        description="Documents with no views in the selected period.",
    ),
    "tpl-document-retention-status": TemplateDefinition(
        id="tpl-document-retention-status",
        title="Document Retention Status",
        domain="Document Management",
        description="Active, archived, and scheduled-deletion counts.",
    ),
    "tpl-document-version-activity": TemplateDefinition(
        id="tpl-document-version-activity",
        title="Document Version Activity",
        domain="Document Management",
        description="Version history and access counts by document.",
    ),
    "tpl-portal-submission-performance": TemplateDefinition(
        id="tpl-portal-submission-performance",
        title="Portal Submission Performance",
        domain="External Portal",
        description="Submission volume and completion rate trends.",
    ),
    "tpl-user-login-and-security-activity": TemplateDefinition(
        id="tpl-user-login-and-security-activity",
        title="User Login and Security Activity",
        domain="User Sessions & Security",
        description="Login activity, failures and anomalies.",
    ),
    "tpl-ai-credit-consumption": TemplateDefinition(
        id="tpl-ai-credit-consumption",
        title="AI Credit Consumption",
        domain="Report Agent Impact & ROI",
        description="Monthly AI credit usage vs. allocation.",
    ),
    "tpl-report-agent-roi": TemplateDefinition(
        id="tpl-report-agent-roi",
        title="Report Agent ROI",
        domain="Report Agent Impact & ROI",
        description="Net business value and ROI% by report.",
    ),
}


def list_templates() -> list[ReportAgentTemplate]:
    """Return list of supported templates."""
    return [t.to_model() for t in SUPPORTED_TEMPLATES.values()]


def get_template(template_id: str) -> Optional[TemplateDefinition]:
    """Look up template definition by ID, slug, title, or normalized name."""
    raw = (template_id or "").strip()
    if not raw:
        return None

    clean_raw = raw.lower()
    # 1. Exact slug match
    if clean_raw in SUPPORTED_TEMPLATES:
        return SUPPORTED_TEMPLATES[clean_raw]

    # 2. Match with tpl- prefix
    prefixed = f"tpl-{clean_raw}" if not clean_raw.startswith("tpl-") else clean_raw
    if prefixed in SUPPORTED_TEMPLATES:
        return SUPPORTED_TEMPLATES[prefixed]

    # 3. Match by title (case-insensitive)
    for t in SUPPORTED_TEMPLATES.values():
        if t.title.lower() == clean_raw:
            return t

    # 4. Normalized alphanumeric match
    norm_input = re.sub(r"[^a-zA-Z0-9]", "", clean_raw)
    for t in SUPPORTED_TEMPLATES.values():
        norm_title = re.sub(r"[^a-zA-Z0-9]", "", t.title.lower())
        norm_id = re.sub(r"[^a-zA-Z0-9]", "", t.id.lower())
        if norm_input in (norm_title, norm_id):
            return t

    return None
