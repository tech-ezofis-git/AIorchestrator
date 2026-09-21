"""Comprehensive tests for Report Agent semantic table ranking, domain concept coverage, and system table isolation."""
from __future__ import annotations

import pytest
from app.models.report_agent import GenerateReportPlanRequest
from app.report_agent.field_discovery import find_relevant_fields
from app.report_agent.metadata_service import ColumnMeta, DatabaseSchema
from app.report_agent.planner import create_report_plan
from app.report_agent.report_validator import validate_report_data
from app.report_agent.service import ReportAgentService
from app.report_agent.templates import get_template


def _build_multi_domain_schema() -> DatabaseSchema:
    """Build a realistic database schema containing system tables, form tables, and repo tables."""
    schema = DatabaseSchema()
    
    # 1. System Orchestrator Tables (internal AI agent tracking)
    ap_runs_cols = [
        ColumnMeta("public", "ap_runs", "id", "uuid"),
        ColumnMeta("public", "ap_runs", "tenant_id", "character varying"),
        ColumnMeta("public", "ap_runs", "session_id", "character varying"),
        ColumnMeta("public", "ap_runs", "status", "character varying"),
        ColumnMeta("public", "ap_runs", "requested_skills", "character varying"),
        ColumnMeta("public", "ap_runs", "credits_charged", "numeric"),
        ColumnMeta("public", "ap_runs", "created_at", "timestamp without time zone"),
    ]
    ap_ledger_cols = [
        ColumnMeta("public", "ap_credit_ledger", "id", "uuid"),
        ColumnMeta("public", "ap_credit_ledger", "tenant_id", "character varying"),
        ColumnMeta("public", "ap_credit_ledger", "credits_used", "numeric"),
        ColumnMeta("public", "ap_credit_ledger", "created_at", "timestamp without time zone"),
    ]

    # 2. Form Builder 3-Way Match AP Table
    ezfb_ap_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Date", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Date", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "created_at", "character varying"),
    ]

    # 3. Repository AP Table
    repo_ap_cols = [
        ColumnMeta("repository", "items_a6169a5c", "id", "uuid"),
        ColumnMeta("repository", "items_a6169a5c", "PONumber", "character varying"),
        ColumnMeta("repository", "items_a6169a5c", "POAmount", "text"),
        ColumnMeta("repository", "items_a6169a5c", "PODate", "date"),
        ColumnMeta("repository", "items_a6169a5c", "InvoiceNo", "text"),
        ColumnMeta("repository", "items_a6169a5c", "InvoiceAmount", "text"),
        ColumnMeta("repository", "items_a6169a5c", "InvoiceDate", "date"),
        ColumnMeta("repository", "items_a6169a5c", "DueDate", "date"),
        ColumnMeta("repository", "items_a6169a5c", "MatchedStatus", "text"),
        ColumnMeta("repository", "items_a6169a5c", "status", "character varying"),
    ]

    # 4. Workflow Table
    workflow_cols = [
        ColumnMeta("workflow", "workflow_instances", "instance_id", "uuid"),
        ColumnMeta("workflow", "workflow_instances", "workflow_name", "text"),
        ColumnMeta("workflow", "workflow_instances", "current_status", "text"),
        ColumnMeta("workflow", "workflow_instances", "current_stage", "text"),
        ColumnMeta("workflow", "workflow_instances", "created_at", "timestamp without time zone"),
    ]

    for c in ap_runs_cols:
        schema.columns_by_table.setdefault(("public", "ap_runs"), []).append(c)
        schema.all_columns.append(c)
    for c in ap_ledger_cols:
        schema.columns_by_table.setdefault(("public", "ap_credit_ledger"), []).append(c)
        schema.all_columns.append(c)
    for c in ezfb_ap_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)
    for c in repo_ap_cols:
        schema.columns_by_table.setdefault(("repository", "items_a6169a5c"), []).append(c)
        schema.all_columns.append(c)
    for c in workflow_cols:
        schema.columns_by_table.setdefault(("workflow", "workflow_instances"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("public", "ap_runs"),
        ("public", "ap_credit_ledger"),
        ("dbo", "ezfb_6e45749f_items"),
        ("repository", "items_a6169a5c"),
        ("workflow", "workflow_instances"),
    ]

    return schema


def test_ap_aging_never_selects_ap_runs():
    """Accounts Payable Aging must strictly select business tables and never system orchestrator tables."""
    schema = _build_multi_domain_schema()
    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)

    # 1. ap_runs and ap_credit_ledger must NOT be discovered for AP Aging
    assert "ap_runs" not in disc_tables
    assert "public.ap_runs" not in disc_tables
    assert "ap_credit_ledger" not in disc_tables

    # 2. Business tables must be discovered and ranked at the top
    top_table = disc_tables[0]
    assert top_table in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items", "repository.items_a6169a5c")

    # 3. Create report plan and verify columns & aging logic
    plan = create_report_plan(
        template=template,
        schema=schema,
        discovered_tables=disc_tables,
        discovered_fields=disc_fields,
    )

    assert plan.source.table in ("ezfb_6e45749f_items", "items_a6169a5c")

    # Check that aging calculation uses Due_Date / DueDate, NOT created_at
    calc_expressions = [c.expression for c in plan.calculations]
    assert any("Due_Date" in expr or "DueDate" in expr for expr in calc_expressions)
    assert not any("created_at" in expr for expr in calc_expressions)

    # Check that amount column is discovered with currency formatting and true db type
    col_map = {c.field: c for c in plan.columns}
    if "Invoice_Amount" in col_map:
        assert col_map["Invoice_Amount"].type == "text"
        assert col_map["Invoice_Amount"].format == "currency"
    elif "InvoiceAmount" in col_map:
        assert col_map["InvoiceAmount"].type == "text"
        assert col_map["InvoiceAmount"].format == "currency"


def test_ai_credit_consumption_selects_orchestrator_tables():
    """AI Credit Consumption template MUST select ap_runs or ap_credit_ledger."""
    schema = _build_multi_domain_schema()
    template = get_template("tpl-ai-credit-consumption")
    assert template is not None

    disc_tables, disc_fields, _ = find_relevant_fields(template, schema)
    assert any(t in ("ap_runs", "ap_credit_ledger", "public.ap_runs", "public.ap_credit_ledger") for t in disc_tables)

    plan = create_report_plan(
        template=template,
        schema=schema,
        discovered_tables=disc_tables,
        discovered_fields=disc_fields,
    )
    assert plan.source.table in ("ap_runs", "ap_credit_ledger")


def test_workflow_template_selects_workflow_instances():
    """Pending Workflow Requests must select workflow_instances."""
    schema = _build_multi_domain_schema()
    template = get_template("tpl-pending-workflow-requests")
    assert template is not None

    disc_tables, disc_fields, _ = find_relevant_fields(template, schema)
    assert disc_tables[0] in ("workflow.workflow_instances", "workflow_instances")


def test_semantic_validation_flags_missing_domain_fields():
    """Report validation must flag reports missing essential domain fields."""
    template = get_template("tpl-accounts-payable-aging")
    schema = _build_multi_domain_schema()

    # Plan lacking date and amount fields
    from app.models.report_agent import ReportColumn, ReportData, ReportPlan, ReportSource
    invalid_plan = ReportPlan(
        title="Accounts Payable Aging",
        description="Test",
        template_id="tpl-accounts-payable-aging",
        source=ReportSource(table="ap_runs"),
        columns=[ReportColumn(field="session_id", label="Session ID", type="text")],
        calculations=[],
        filters=[],
        group_by=[],
        order_by=[],
        status_rules=[],
    )
    data = ReportData(columns=["session_id"], rows=[{"session_id": "123"}], row_count=1)
    validation = validate_report_data(invalid_plan, data)

    assert validation.valid is False
    assert validation.checks["semantic_match"] is False
    assert any("essential Accounts Payable fields" in w for w in validation.warnings)


def test_dynamic_table_and_column_rename_requires_no_template_changes():
    """Verify that completely new or renamed database tables and columns are discovered dynamically without modifying TemplateDefinition."""
    schema = DatabaseSchema()
    custom_cols = [
        ColumnMeta("dbo", "custom_supplier_bills", "bill_id", "uuid"),
        ColumnMeta("dbo", "custom_supplier_bills", "supplier_name", "text"),
        ColumnMeta("dbo", "custom_supplier_bills", "bill_number", "text"),
        ColumnMeta("dbo", "custom_supplier_bills", "bill_amount", "numeric"),
        ColumnMeta("dbo", "custom_supplier_bills", "payment_due_date", "date"),
        ColumnMeta("dbo", "custom_supplier_bills", "bill_status", "text"),
    ]
    for c in custom_cols:
        schema.columns_by_table.setdefault(("dbo", "custom_supplier_bills"), []).append(c)
        schema.all_columns.append(c)
    schema.tables = [("dbo", "custom_supplier_bills")]

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    assert any("custom_supplier_bills" in t for t in disc_tables)

    # Create plan and verify exact custom columns are used without template modifications
    plan = create_report_plan(template, schema, disc_tables, disc_fields)
    assert plan.source.table == "custom_supplier_bills"
    planned_fields = [c.field for c in plan.columns]
    assert "supplier_name" in planned_fields
    assert "bill_number" in planned_fields
    assert "bill_amount" in planned_fields
    assert "payment_due_date" in planned_fields
    assert any("payment_due_date" in c.expression for c in plan.calculations)


@pytest.mark.asyncio
async def test_live_connection_probe_filters_inaccessible_tables():
    """Verify service probes candidate tables on live connection and drops any table returning relation does not exist."""
    schema = _build_multi_domain_schema()

    class FlakyDb:
        async def fetch(self, query: str, *args):
            q_lower = query.lower()
            if "from information_schema.columns" in q_lower:
                return [
                    {
                        "table_schema": c.schema,
                        "table_name": c.table,
                        "column_name": c.column,
                        "data_type": c.data_type,
                        "udt_name": c.data_type,
                        "is_nullable": "YES",
                    }
                    for c in schema.all_columns
                ]
            if "ezfb_6e45749f_items" in q_lower:
                raise RuntimeError('relation "dbo"."ezfb_6e45749f_items" does not exist')
            if "items_a6169a5c" in q_lower:
                return [{"id": "1", "PONumber": "PO-100", "InvoiceAmount": "500", "DueDate": "2026-10-01", "MatchedStatus": "matched", "status": "open"}]
            return [{"val": 1}]

    service = ReportAgentService(db_pool=FlakyDb())
    req = GenerateReportPlanRequest(template_id="tpl-accounts-payable-aging")
    res = await service.generate_report_plan(req)

    # ezfb_6e45749f_items failed connection probe, so service automatically selected items_a6169a5c
    assert res.report_plan.source.table in ("items_a6169a5c", "workflow_instances")
    assert res.validation.valid is True
    assert len(res.report_plan.calculations) > 0


def test_accounts_payable_workflow_table_selection_for_aging():
    """Verify that when the database contains Accounts Payable workflow tables (Form Builder, Repo, and Workflow Instances), the AP Aging template accurately selects the AP business table and due dates."""
    schema = DatabaseSchema()
    
    # Workflow instances for AP workflow 452859c2-9bbe-4e17-acd2-f11524a0650e
    wf_inst_cols = [
        ColumnMeta("workflow", "workflow_instances_452859c2", "id", "uuid"),
        ColumnMeta("workflow", "workflow_instances_452859c2", "reference_number", "character varying"),
        ColumnMeta("workflow", "workflow_instances_452859c2", "request_no", "character varying"),
        ColumnMeta("workflow", "workflow_instances_452859c2", "status", "character varying"),
        ColumnMeta("workflow", "workflow_instances_452859c2", "created_at", "timestamp with time zone"),
    ]
    # Form Builder items for Form 6e45749f-c65d-4f28-9e0f-f22fdde4cb03
    ezfb_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
    ]
    # Repository items for Repository a6169a5c-1468-4fb5-90a9-220082a89f2a
    repo_cols = [
        ColumnMeta("dbo", "items_a6169a5c", "ItemId", "uuid"),
        ColumnMeta("dbo", "items_a6169a5c", "PONumber", "character varying"),
        ColumnMeta("dbo", "items_a6169a5c", "InvoiceNumber", "character varying"),
        ColumnMeta("dbo", "items_a6169a5c", "TotalAmount", "numeric"),
        ColumnMeta("dbo", "items_a6169a5c", "DueDate", "date"),
    ]

    for c in wf_inst_cols:
        schema.columns_by_table.setdefault(("workflow", "workflow_instances_452859c2"), []).append(c)
        schema.all_columns.append(c)
    for c in ezfb_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)
    for c in repo_cols:
        schema.columns_by_table.setdefault(("dbo", "items_a6169a5c"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("workflow", "workflow_instances_452859c2"),
        ("dbo", "ezfb_6e45749f_items"),
        ("dbo", "items_a6169a5c"),
    ]

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    # The AP form table containing Invoice and Due Date metrics is top-ranked
    assert disc_tables[0] in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items")
    assert len(missing_fields) == 0

    plan = create_report_plan(template, schema, disc_tables, disc_fields)
    assert plan.source.table == "ezfb_6e45749f_items"
    assert any("Due_Date" in c.expression for c in plan.calculations)


def test_vessel_workflow_table_rejected_for_accounts_payable():
    """Verify that when the database contains both Vessel workflow tables (dbo.ezfb_d7ad8ffb_items) and AP tables (dbo.ezfb_6e45749f_items), the AP Aging template strictly selects the AP table and rejects the Vessel table."""
    schema = DatabaseSchema()
    
    # 1. Accounts Payable form table (Form ID 6e45749f-c65d-4f28-9e0f-f22fdde4cb03)
    ap_form_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
    ]
    # 2. Vessel workflow form table (Form ID d7ad8ffb-...)
    vessel_form_cols = [
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "vessel_name", "text"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "imo_number", "text"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "port_of_entry", "text"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "eta", "timestamp with time zone"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "berth_number", "text"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "cargo_weight", "numeric"),
        ColumnMeta("dbo", "ezfb_d7ad8ffb_items", "status", "text"),
    ]

    for c in ap_form_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)
    for c in vessel_form_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_d7ad8ffb_items"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("dbo", "ezfb_d7ad8ffb_items"),
        ("dbo", "ezfb_6e45749f_items"),
    ]

    # Workflow map associations
    schema.table_to_workflow["6e45749f"] = "Accounts Payable"
    schema.table_to_workflow["d7ad8ffb"] = "Vessel Workflow"

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    # Must select AP table and NOT the Vessel table
    assert disc_tables[0] in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items")
    assert not any("d7ad8ffb" in t for t in disc_tables)


def test_workflow_tasks_not_selected_for_ap_aging():
    """Verify that internal workflow engine task tables (workflow.workflow_tasks_452859c2) are not selected over the business form table (dbo.ezfb_6e45749f_items) for AP Aging."""
    schema = DatabaseSchema()

    # 1. Internal workflow task table for workflow 452859c2
    wf_task_cols = [
        ColumnMeta("workflow", "workflow_tasks_452859c2", "id", "uuid"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "tenant_id", "uuid"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "workflow_instance_id", "uuid"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "step_instance_id", "uuid"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "task_name", "character varying"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "assigned_to_user_id", "uuid"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "due_date", "timestamp with time zone"),
        ColumnMeta("workflow", "workflow_tasks_452859c2", "completed_at", "timestamp with time zone"),
    ]

    # 2. Form Builder table for AP Form 6e45749f
    ezfb_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
    ]

    for c in wf_task_cols:
        schema.columns_by_table.setdefault(("workflow", "workflow_tasks_452859c2"), []).append(c)
        schema.all_columns.append(c)
    for c in ezfb_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("workflow", "workflow_tasks_452859c2"),
        ("dbo", "ezfb_6e45749f_items"),
    ]

    schema.table_to_workflow["452859c2"] = "Accounts Payable"
    schema.table_to_workflow["6e45749f"] = "Accounts Payable"

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    # Must choose ezfb_6e45749f_items and NOT workflow_tasks_452859c2
    assert disc_tables[0] in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items")
    assert not any("workflow_tasks" in t for t in disc_tables)


def test_po_form_not_selected_over_ap_invoice_form():
    """Verify that pure Purchase Order form tables (dbo.ezfb_1e16dd88_items) are not selected over the true Accounts Payable invoice form table (dbo.ezfb_6e45749f_items) for AP Aging."""
    schema = DatabaseSchema()

    # 1. Pure PO Form table (ezfb_1e16dd88_items)
    po_form_cols = [
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "Due_Date", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "PO_Amount", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "Supplier", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "Supplier_Address", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "PO_Date", "text"),
        ColumnMeta("dbo", "ezfb_1e16dd88_items", "item_id", "uuid"),
    ]

    # 2. True AP Invoice Form table (ezfb_6e45749f_items)
    ap_form_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
    ]

    for c in po_form_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_1e16dd88_items"), []).append(c)
        schema.all_columns.append(c)
    for c in ap_form_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("dbo", "ezfb_1e16dd88_items"),
        ("dbo", "ezfb_6e45749f_items"),
    ]

    schema.table_to_workflow["6e45749f"] = "Accounts Payable"
    schema.table_to_workflow["1e16dd88"] = "Purchase Order"

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    # Must choose ezfb_6e45749f_items and NOT ezfb_1e16dd88_items
    assert disc_tables[0] in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items")
    assert not any("1e16dd88" in t for t in disc_tables)


def test_stage_table_rejected_in_favor_of_form_table():
    """Verify that staging tables (repository.items_a6169a5c_stage) are strictly rejected and the live Form Builder table (dbo.ezfb_6e45749f_items) is selected."""
    schema = DatabaseSchema()

    # 1. OCR Ingestion Staging Table
    stage_cols = [
        ColumnMeta("repository", "items_a6169a5c_stage", "DueDate", "date"),
        ColumnMeta("repository", "items_a6169a5c_stage", "InvoiceAmount", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "InvoiceTaxAmount", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "MatchedStatus", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "PONumber", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "total_pages", "integer"),
        ColumnMeta("repository", "items_a6169a5c_stage", "InvoiceDate", "date"),
        ColumnMeta("repository", "items_a6169a5c_stage", "POAmount", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "Supplier", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "storage_provider_id", "uuid"),
        ColumnMeta("repository", "items_a6169a5c_stage", "stage_status", "character varying"),
        ColumnMeta("repository", "items_a6169a5c_stage", "InvoiceNo", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "SupplierAddress", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "InvoiceExtractedLineItem", "text"),
        ColumnMeta("repository", "items_a6169a5c_stage", "file_size", "integer"),
    ]

    # 2. Live AP Form Table
    form_cols = [
        ColumnMeta("dbo", "ezfb_6e45749f_items", "item_id", "uuid"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Number", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "PO_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_No", "text"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Amount", "numeric"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Invoice_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Due_Date", "date"),
        ColumnMeta("dbo", "ezfb_6e45749f_items", "Matched_Status", "text"),
    ]

    for c in stage_cols:
        schema.columns_by_table.setdefault(("repository", "items_a6169a5c_stage"), []).append(c)
        schema.all_columns.append(c)
    for c in form_cols:
        schema.columns_by_table.setdefault(("dbo", "ezfb_6e45749f_items"), []).append(c)
        schema.all_columns.append(c)

    schema.tables = [
        ("repository", "items_a6169a5c_stage"),
        ("dbo", "ezfb_6e45749f_items"),
    ]

    schema.table_to_workflow["6e45749f"] = "Accounts Payable"
    schema.table_to_workflow["a6169a5c"] = "Accounts Payable"

    template = get_template("tpl-accounts-payable-aging")
    assert template is not None

    disc_tables, disc_fields, missing_fields = find_relevant_fields(template, schema)
    # Must choose ezfb_6e45749f_items and NOT items_a6169a5c_stage
    assert disc_tables[0] in ("dbo.ezfb_6e45749f_items", "ezfb_6e45749f_items")
    assert not any("stage" in t for t in disc_tables)







