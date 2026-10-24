"""Named report recipes so description changes produce different reportSpecs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.models.report_agent import (
    ReportCatalogItem,
    ReportSpec,
    ReportSpecColumn,
    ReportSpecFilter,
    ReportSpecSort,
)


def _label(key: str) -> str:
    text = re.sub(r"[_\-]+", " ", (key or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else "Column"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass(frozen=True)
class ReportIntent:
    key: str
    title: str
    objective: str
    report_type: str
    scope: str
    kind: str
    keywords: tuple[str, ...]
    workflow_name: Optional[str] = None
    repository_name: Optional[str] = None
    tag: str = ""


REPORT_INTENTS: tuple[ReportIntent, ...] = (
    ReportIntent(
        key="ap_aging",
        title="AP Aging Report",
        objective="Outstanding AP requests grouped by aging bucket (0-5, 6-15, 16-30, 30+ days).",
        report_type="specific_workflow",
        scope="workflows",
        kind="aging",
        keywords=("ap aging", "aging report", "aging bucket", "0-5", "30+", "outstanding ap"),
        workflow_name="Accounts Payable",
        tag="Accounts Payable",
    ),
    ReportIntent(
        key="pending_approval",
        title="Pending Approval Report",
        objective="AP requests waiting action for more than 5 days, grouped by stage.",
        report_type="specific_workflow",
        scope="workflows",
        kind="pending_approval",
        keywords=("pending approval", "waiting action", "more than 5 days", "grouped by stage"),
        workflow_name="Accounts Payable",
        tag="Accounts Payable",
    ),
    ReportIntent(
        key="vendor_outstanding",
        title="Vendor Outstanding Report",
        objective="Vendor-wise payable exposure across running and on-hold requests.",
        report_type="specific_workflow",
        scope="workflows",
        kind="vendor_outstanding",
        keywords=("vendor outstanding", "vendor-wise", "payable exposure", "on-hold"),
        workflow_name="Accounts Payable",
        tag="Accounts Payable",
    ),
    ReportIntent(
        key="approved_total",
        title="Total Approved with Score",
        objective="Approved invoices with matched status, amounts, and total.",
        report_type="specific_workflow",
        scope="workflows",
        kind="approved_total",
        keywords=("total approved", "approved with score", "approved score"),
        workflow_name="Accounts Payable",
        tag="Accounts Payable",
    ),
    ReportIntent(
        key="awaiting_action",
        title="Total Inbox",
        objective="Open requests sitting in inbox across every workflow.",
        report_type="all_workflows",
        scope="workflows",
        kind="inbox_total",
        keywords=("total inbox", "inbox", "awaiting for your action", "awaiting action", "open tickets"),
        tag="Workflows",
    ),
    ReportIntent(
        key="workflow_aging",
        title="Aging",
        objective="Open requests across all workflows, grouped by aging bucket and workflow.",
        report_type="all_workflows",
        scope="workflows",
        kind="aging",
        keywords=("aging", "aging report", "request aging", "ticket aging", "0-5", "30+"),
        tag="Workflows",
    ),
    ReportIntent(
        key="tickets_processed",
        title="Total Tickets Processed",
        objective="Completed tickets across all workflows.",
        report_type="all_workflows",
        scope="workflows",
        kind="tickets_processed",
        keywords=("tickets processed", "total processed", "completed tickets", "processed in all workflow", "processed"),
        tag="Workflows",
    ),
    ReportIntent(
        key="document_version",
        title="Document Version Activity",
        objective="Version history, uploader and access counts by document.",
        report_type="all_repositories",
        scope="repositories",
        kind="version_activity",
        keywords=("document version", "version activity", "version history", "uploader"),
        tag="Document Management",
    ),
    ReportIntent(
        key="repo_total",
        title="Total Documents",
        objective="Document counts across every repository.",
        report_type="all_repositories",
        scope="repositories",
        kind="repo_total",
        keywords=("total documents", "document count", "all repository", "all repositories"),
        tag="Document Management",
    ),
    ReportIntent(
        key="repo_aging",
        title="Document Aging",
        objective="Documents grouped by aging bucket and repository.",
        report_type="all_repositories",
        scope="repositories",
        kind="aging",
        keywords=("document aging", "aging", "0-5", "30+"),
        tag="Document Management",
    ),
    ReportIntent(
        key="no_recent_access",
        title="Documents Without Recent Access",
        objective="Documents with zero views in the last 30 days, by repository.",
        report_type="all_repositories",
        scope="repositories",
        kind="no_recent_access",
        keywords=("without recent access", "zero views", "last 30 days", "no recent access"),
        tag="Document Management",
    ),
    ReportIntent(
        key="retention_status",
        title="Document Retention Status",
        objective="Active, archived, scheduled-for-deletion and legal-hold document counts.",
        report_type="all_repositories",
        scope="repositories",
        kind="retention",
        keywords=("retention status", "legal-hold", "scheduled-for-deletion", "archived"),
        tag="Document Management",
    ),
    ReportIntent(
        key="workflow_sla",
        title="Workflow SLA Compliance",
        objective="Workflows completed within vs. outside SLA, by category and owner.",
        report_type="all_workflows",
        scope="workflows",
        kind="sla",
        keywords=("sla", "sla compliance", "within vs", "outside sla", "by category"),
        tag="Workflow Automation",
    ),
    ReportIntent(
        key="pending_workflow",
        title="Pending Workflow Requests",
        objective="Open workflow requests with no action for 5+ days.",
        report_type="all_workflows",
        scope="workflows",
        kind="pending_workflow",
        keywords=("pending workflow", "no action for", "open workflow requests", "5+ days"),
        tag="Workflow Automation",
    ),
)


_DATE_NAMES = (
    "invoice_date",
    "invoicedate",
    "due_date",
    "duedate",
    "createdon",
    "created_on",
    "createdat",
    "created_at",
    "createddate",
    "created_date",
    "submitteddate",
    "request_date",
    "requestdate",
    "posteddate",
    "documentdate",
    "lastmodified",
    "modifiedon",
    "activitydate",
    "agingdays",
    "days_pending",
    "dayspending",
    "createdatutc",
    "startedatutc",
    "completedatutc",
)
_VENDOR_NAMES = ("supplier", "vendor", "vendor_name", "supplier_name", "vendorname")
_STATUS_NAMES = (
    "matched_status",
    "matchedstatus",
    "status",
    "stage",
    "state",
    "approvalstatus",
    "approval_status",
)
_STAGE_NAMES = ("stage", "currentstage", "activity", "activity_name", "step")
_AMOUNT_NAMES = (
    "invoice_amount",
    "po_amount",
    "amount",
    "outstanding",
    "balance",
    "total_amount",
    "netamount",
)
_ID_NAMES = ("invoice_no", "invoiceno", "po_number", "ponumber", "name", "filename", "title")
_DOC_NAMES = ("filename", "file_name", "documentname", "document_name", "title", "name")
_VERSION_NAMES = ("fileversion", "version", "versionno", "version_no", "revision")
_UPLOADER_NAMES = ("uploader", "createdby", "created_by", "owner", "uploadedby", "startedby")
_ACCESS_NAMES = ("viewcount", "view_count", "accesscount", "access_count", "views")
_LAST_ACCESS_NAMES = ("lastaccessed", "last_accessed", "lastviewed", "last_viewed", "lastaccessdate")
_OWNER_NAMES = ("workflowname", "workflow_name", "owner", "assignedto", "assigned_to", "category")
_REPO_NAMES = ("repository_name", "repositoryname", "repository")
_COMPLETED_NAMES = ("completedatutc", "completed_at", "completedon", "completed_on")
_SLA_NAMES = ("sla", "sladays", "sla_days", "slastatus", "sla_status")
_HOLD_NAMES = ("legalhold", "legal_hold", "onhold", "on_hold", "retention", "retentionstatus")


def list_catalog() -> list[ReportCatalogItem]:
    return [
        ReportCatalogItem(
            key=item.key,
            title=item.title,
            objective=item.objective,
            report_type=item.report_type,
            scope=item.scope,
            workflow_name=item.workflow_name,
            repository_name=item.repository_name,
            tag=item.tag or None,
        )
        for item in REPORT_INTENTS
        if item.key not in ("approved_total",)
    ]


def match_report_intent(description: str, report_type: Optional[str] = None) -> Optional[ReportIntent]:
    text = _norm(description)
    if not text:
        return None
    want_type = (report_type or "").strip().lower()
    best: Optional[ReportIntent] = None
    best_score = 0
    for intent in REPORT_INTENTS:
        if want_type and intent.report_type != want_type:
            continue
        score = 0
        if _norm(intent.title) == text:
            score += 12
        elif _norm(intent.title) in text or text in _norm(intent.title):
            score += 8
        for kw in intent.keywords:
            if _norm(kw) and _norm(kw) in text:
                score += 4
        if score > best_score:
            best = intent
            best_score = score
    return best if best_score >= 4 else None


def catalog_prompt_hint(
    description: str,
    *,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
    report_type: Optional[str] = None,
) -> str:
    want = _norm(workflow_name or "")
    want_repo = _norm(repository_name or "")
    intent = match_report_intent(description, report_type=report_type)
    if intent and want and intent.workflow_name and _norm(intent.workflow_name) != want:
        intent = None
    if intent and want_repo and intent.repository_name and _norm(intent.repository_name) != want_repo:
        intent = None
    if intent and report_type and intent.report_type != report_type:
        intent = None
    lines = ["Known report recipes (match the user description to one of these shapes):"]
    shown = 0
    for item in REPORT_INTENTS:
        if report_type and item.report_type != report_type:
            continue
        if want and item.workflow_name and _norm(item.workflow_name) != want:
            continue
        if want_repo and item.repository_name and _norm(item.repository_name) != want_repo:
            continue
        mark = " <-- MATCHED" if intent and item.key == intent.key else ""
        lines.append(f"- {item.title}: {item.objective}{mark}")
        shown += 1
    if shown == 0:
        return (
            "Use only the live columns of the selected workflow/repository form. "
            "Do not invent Invoice_No, PO_Number, or other fields that are not on that form."
        )
    lines.append(
        "Do not reuse a generic invoice list when the description asks for aging, "
        "vendor grouping, pending stage, SLA, retention, or document activity. "
        "Never add Invoice_No or PO_Number unless those names are in the live column list."
    )
    return "\n".join(lines)


def _pick(lower_map: dict[str, str], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        hit = lower_map.get(name.lower())
        if hit:
            return hit
    for name in names:
        needle = re.sub(r"[^a-z0-9]", "", name.lower())
        if len(needle) < 4:
            continue
        for low, real in lower_map.items():
            compact = re.sub(r"[^a-z0-9]", "", low)
            if needle == compact or needle in compact:
                return real
    return None


def _col(key: str, label: Optional[str] = None, formula: Optional[str] = None) -> ReportSpecColumn:
    return ReportSpecColumn(key=key, label=label or _label(key), formula=formula)


def _aging_formula(date_col: str) -> str:
    return (
        "CASE WHEN (CURRENT_DATE - CAST("
        f"{date_col} AS date)) <= 5 THEN '0-5' "
        "WHEN (CURRENT_DATE - CAST("
        f"{date_col} AS date)) <= 15 THEN '6-15' "
        "WHEN (CURRENT_DATE - CAST("
        f"{date_col} AS date)) <= 30 THEN '16-30' "
        "ELSE '30+' END"
    )


def spec_from_intent(
    *,
    intent: Optional[ReportIntent],
    description: str,
    report_type: str,
    available_columns: list[str],
    source_schema: Optional[str] = None,
    source_table: Optional[str] = None,
    warnings: Optional[list[str]] = None,
) -> ReportSpec:
    lower_map = {c.lower(): c for c in available_columns}
    warn = list(warnings or [])
    desc = (description or "").strip()
    title = (intent.title if intent else desc[:80]) or "Report"
    objective = (intent.objective if intent else desc) or "Generate the requested report."
    kind = intent.kind if intent else "generic"

    vendor = _pick(lower_map, _VENDOR_NAMES)
    status = _pick(lower_map, _STATUS_NAMES)
    stage = _pick(lower_map, _STAGE_NAMES) or status
    amount = _pick(lower_map, _AMOUNT_NAMES)
    date_col = _pick(lower_map, _DATE_NAMES)
    ident = _pick(lower_map, _ID_NAMES)
    doc = _pick(lower_map, _DOC_NAMES)
    version = _pick(lower_map, _VERSION_NAMES)
    uploader = _pick(lower_map, _UPLOADER_NAMES)
    views = _pick(lower_map, _ACCESS_NAMES)
    last_access = _pick(lower_map, _LAST_ACCESS_NAMES)
    owner = _pick(lower_map, _OWNER_NAMES)
    sla = _pick(lower_map, _SLA_NAMES)
    hold = _pick(lower_map, _HOLD_NAMES)
    workflow_col = _pick(lower_map, _OWNER_NAMES)
    repo_col = _pick(lower_map, _REPO_NAMES)
    completed_col = _pick(lower_map, _COMPLETED_NAMES)
    po_amt = lower_map.get("po_amount")
    inv_amt = lower_map.get("invoice_amount") or amount

    columns: list[ReportSpecColumn] = []
    filters: list[ReportSpecFilter] = []
    group_by: list[str] = []
    sort: Optional[ReportSpecSort] = None

    if kind == "aging":
        if date_col:
            columns.append(_col("Aging_Bucket", "Aging Bucket", _aging_formula(date_col)))
            group_by = ["Aging_Bucket"]
            if vendor:
                columns.append(_col(vendor, "Vendor"))
                group_by = ["Aging_Bucket", vendor]
            elif workflow_col:
                columns.append(_col(workflow_col, "Workflow"))
                group_by = ["Aging_Bucket", workflow_col]
            elif repo_col:
                columns.append(_col(repo_col, "Repository"))
                group_by = ["Aging_Bucket", repo_col]
            columns.append(_col("Request_Count", "Count", "COUNT(*)"))
            if amount:
                columns.append(_col("Outstanding_Amount", "Outstanding Amount", f"SUM({amount})"))
            sort = ReportSpecSort(field=columns[-1].key, direction="desc")
        else:
            warn.append("No date column found for aging buckets; listing outstanding items instead.")
            for key in (ident, vendor, workflow_col, repo_col, status, amount):
                if key and key not in {c.key for c in columns}:
                    columns.append(_col(key))
            if columns:
                sort = ReportSpecSort(field=columns[-1].key, direction="desc")
        if completed_col:
            filters.append(ReportSpecFilter(field=completed_col, op="is_null"))
        elif status:
            filters.append(ReportSpecFilter(field=status, op="not_null"))
    elif kind == "pending_approval":
        if stage:
            columns.append(_col(stage, "Stage"))
            group_by = [stage]
        if vendor:
            columns.append(_col(vendor, "Vendor"))
        if ident:
            columns.append(_col(ident))
        columns.append(_col("Pending_Count", "Pending Requests", "COUNT(*)"))
        if amount:
            columns.append(_col("Pending_Amount", "Pending Amount", f"SUM({amount})"))
        if status:
            filters.append(ReportSpecFilter(field=status, op="not_null"))
        if date_col:
            warn.append("Pending-over-5-days uses available stage/status grouping; date column is listed when present.")
            columns.insert(0, _col(date_col, "Request Date"))
        sort = ReportSpecSort(field="Pending_Count", direction="desc")
    elif kind == "vendor_outstanding":
        if vendor:
            columns.append(_col(vendor, "Vendor"))
            group_by = [vendor]
        if status:
            columns.append(_col(status, "Status"))
            if vendor:
                group_by = [vendor, status]
            else:
                group_by = [status]
        columns.append(_col("Request_Count", "Requests", "COUNT(*)"))
        if amount:
            columns.append(_col("Outstanding_Amount", "Outstanding Amount", f"SUM({amount})"))
        sort = ReportSpecSort(field=columns[-1].key, direction="desc")
    elif kind == "approved_total":
        for key in (ident, status, po_amt, inv_amt, vendor):
            if key and key not in {c.key for c in columns}:
                columns.append(_col(key))
        if po_amt and inv_amt:
            columns.append(_col("Total_Amount", "Total Amount", f"{po_amt} + {inv_amt}"))
        if status:
            filters.append(ReportSpecFilter(field=status, op="not_null"))
        if "score" in _norm(desc) and "score" not in lower_map:
            warn.append("Score column not found in schema — not included.")
        if columns:
            sort = ReportSpecSort(field=columns[-1].key, direction="desc")
    elif kind in ("awaiting", "pending_workflow", "inbox_total"):
        if workflow_col:
            columns.append(_col(workflow_col, "Workflow"))
            group_by = [workflow_col]
        if status:
            columns.append(_col(status, "Status"))
            if group_by:
                group_by.append(status)
            else:
                group_by = [status]
        columns.append(_col("Inbox_Count", "Inbox", "COUNT(*)"))
        if completed_col:
            filters.append(ReportSpecFilter(field=completed_col, op="is_null"))
        elif status:
            filters.append(ReportSpecFilter(field=status, op="not_null"))
        sort = ReportSpecSort(field="Inbox_Count", direction="desc")
    elif kind == "tickets_processed":
        if workflow_col:
            columns.append(_col(workflow_col, "Workflow"))
            group_by = [workflow_col]
        columns.append(_col("Processed_Count", "Tickets Processed", "COUNT(*)"))
        if completed_col:
            filters.append(ReportSpecFilter(field=completed_col, op="not_null"))
        elif status:
            filters.append(ReportSpecFilter(field=status, op="not_null"))
        sort = ReportSpecSort(field="Processed_Count", direction="desc")
    elif kind == "repo_total":
        if repo_col:
            columns.append(_col(repo_col, "Repository"))
            group_by = [repo_col]
        if status:
            columns.append(_col(status, "Status"))
            if group_by:
                group_by.append(status)
        columns.append(_col("Document_Count", "Documents", "COUNT(*)"))
        sort = ReportSpecSort(field="Document_Count", direction="desc")
    elif kind == "version_activity":
        for key in (doc, version, uploader, views, last_access):
            if key and key not in {c.key for c in columns}:
                columns.append(_col(key))
        columns.append(_col("Version_Count", "Versions", "COUNT(*)"))
        if repo_col:
            columns.insert(0, _col(repo_col, "Repository"))
            group_by = [repo_col]
            if doc:
                group_by.append(doc)
        elif doc:
            group_by = [doc]
            if uploader:
                group_by.append(uploader)
        sort = ReportSpecSort(field="Version_Count", direction="desc")
    elif kind == "no_recent_access":
        for key in (doc, last_access, views, uploader):
            if key and key not in {c.key for c in columns}:
                columns.append(_col(key))
        if views:
            filters.append(ReportSpecFilter(field=views, op="eq", value=0))
        elif last_access:
            sort = ReportSpecSort(field=last_access, direction="asc")
            warn.append("No view-count column; sorting by last access date instead of filtering zero views.")
        if not columns:
            columns = [_col(c) for c in available_columns[:6]]
    elif kind == "retention":
        bucket = hold or status
        if bucket:
            columns.append(_col(bucket, "Retention Status"))
            group_by = [bucket]
        columns.append(_col("Document_Count", "Documents", "COUNT(*)"))
        sort = ReportSpecSort(field="Document_Count", direction="desc")
    elif kind == "sla":
        sla_key = sla
        if not sla_key and date_col:
            if completed_col:
                sla_formula = (
                    "CASE WHEN "
                    f"{completed_col} IS NULL THEN 'Open' "
                    "WHEN (CAST("
                    f"{completed_col} AS date) - CAST({date_col} AS date)) <= 5 "
                    "THEN 'Within SLA' ELSE 'Outside SLA' END"
                )
            else:
                sla_formula = (
                    "CASE WHEN (CURRENT_DATE - CAST("
                    f"{date_col} AS date)) <= 5 THEN 'Within SLA' "
                    "ELSE 'Outside SLA' END"
                )
            columns.append(_col("Sla_Bucket", "SLA", sla_formula))
            sla_key = "Sla_Bucket"
            warn.append("SLA uses a 5-day completion window from available dates; no native SLA column was found.")
        elif sla_key:
            columns.append(_col(sla_key, "SLA"))
        if workflow_col:
            columns.append(_col(workflow_col, "Workflow"))
            group_by = [workflow_col]
            if sla_key:
                group_by.append(sla_key)
        elif sla_key:
            group_by = [sla_key]
        columns.append(_col("Request_Count", "Requests", "COUNT(*)"))
        sort = ReportSpecSort(field="Request_Count", direction="desc")
    else:
        picked: list[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", desc):
            hit = lower_map.get(token.lower())
            if not hit:
                needle = re.sub(r"[^a-z0-9]", "", token.lower())
                if len(needle) >= 4:
                    for low, real in lower_map.items():
                        compact = re.sub(r"[^a-z0-9]", "", low)
                        if needle in compact or compact in needle:
                            hit = real
                            break
            if hit and hit not in picked:
                picked.append(hit)
            if len(picked) >= 8:
                break
        if not picked:
            for preferred in (ident, status, vendor, amount, doc, stage, owner):
                if preferred and preferred not in picked:
                    picked.append(preferred)
                if len(picked) >= 6:
                    break
        if not picked:
            picked = available_columns[:6]
        columns = [_col(k) for k in picked]
        if columns:
            sort = ReportSpecSort(field=columns[-1].key, direction="desc")

    if not columns and available_columns:
        columns = [_col(c) for c in available_columns[:6]]

    return ReportSpec(
        title=title,
        objective=objective,
        report_type=report_type,
        columns=columns,
        filters=filters,
        group_by=group_by,
        sort=sort,
        available_columns=available_columns,
        source_schema=source_schema,
        source_table=source_table,
        warnings=warn,
    )


def align_spec_to_intent(
    spec: ReportSpec,
    *,
    description: str,
    report_type: str,
    available_columns: list[str],
    source_schema: Optional[str] = None,
    source_table: Optional[str] = None,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
) -> ReportSpec:
    """Replace a generic invoice-list spec when the description matches a named recipe."""
    intent = match_report_intent(description or spec.title or "", report_type=report_type)
    want = _norm(workflow_name or "")
    want_repo = _norm(repository_name or "")
    if intent and want and intent.workflow_name and _norm(intent.workflow_name) != want:
        return spec
    if intent and want_repo and intent.repository_name and _norm(intent.repository_name) != want_repo:
        return spec
    if intent and report_type and intent.report_type != report_type:
        return spec
    if not intent or intent.kind in ("approved_total", "generic"):
        return spec
    metric_kinds = {
        "aging",
        "sla",
        "inbox_total",
        "tickets_processed",
        "repo_total",
        "retention",
        "version_activity",
        "pending_workflow",
        "awaiting",
    }
    formulas = " ".join((c.formula or "") for c in spec.columns).lower()
    has_metric = any(token in formulas for token in ("count(", "sum(", "case "))
    has_shape = bool(spec.group_by) or has_metric
    title_hit = _norm(intent.title) in _norm(spec.title or "") or _norm(spec.title or "") in _norm(
        intent.title
    )
    if intent.kind not in metric_kinds and has_shape and title_hit:
        return spec
    if intent.kind not in metric_kinds and has_shape and intent.kind != "aging":
        return spec
    rebuilt = spec_from_intent(
        intent=intent,
        description=description or spec.title,
        report_type=report_type,
        available_columns=available_columns or spec.available_columns,
        source_schema=source_schema or spec.source_schema,
        source_table=source_table or spec.source_table,
        warnings=list(spec.warnings or []),
    )
    note = f"Used '{intent.title}' recipe so this description does not reuse a generic invoice list."
    if note not in rebuilt.warnings:
        rebuilt.warnings.append(note)
    return rebuilt
