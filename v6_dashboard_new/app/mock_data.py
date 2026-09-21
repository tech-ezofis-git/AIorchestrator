"""Rich sample dataset for offline / no-database testing."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

# Default sample tenant GUID
DEFAULT_SAMPLE_TENANT_ID = "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"
DEFAULT_SAMPLE_REPO_ID = "DF175C77-598C-4E92-BA55-318D222C3B52"
DEFAULT_SAMPLE_WORKFLOW_ID = "11111111-1111-1111-1111-111111111111"

SAMPLE_TENANTS: list[dict[str, Any]] = [
    {
        "id": DEFAULT_SAMPLE_TENANT_ID,
        "name": "Acme Global (Accounts Payable Demo)",
        "database": "ezofis_Tenant_3ee0e334",
        "available": True,
    },
    {
        "id": "0B3E1B77-4A6C-46F2-83EE-2F0A5B84956B",
        "name": "TechCorp Solutions (Procurement & POs)",
        "database": "ezofis_Tenant_0b3e1b77",
        "available": True,
    },
    {
        "id": "7A8B9C0D-1E2F-3A4B-5C6D-7E8F9A0B1C2D",
        "name": "Apex Enterprise (HR & Operations)",
        "database": "ezofis_Tenant_7a8b9c0d",
        "available": True,
    },
]

SAMPLE_REPOSITORIES: dict[str, list[dict[str, Any]]] = {
    DEFAULT_SAMPLE_TENANT_ID: [
        {
            "id": DEFAULT_SAMPLE_REPO_ID,
            "name": "Accounts Payable",
            "items_table": "repository.Items_3ee0e334",
            "schema": "repository",
            "table": "Items_3ee0e334",
        },
        {
            "id": "E2F3A4B5-6C7D-8E9F-0A1B-2C3D4E5F6A7B",
            "name": "Vendor Contracts",
            "items_table": "repository.Items_contracts",
            "schema": "repository",
            "table": "Items_contracts",
        },
    ],
    "0B3E1B77-4A6C-46F2-83EE-2F0A5B84956B": [
        {
            "id": "A1B2C3D4-E5F6-7A8B-9C0D-E1F2A3B4C5D6",
            "name": "Purchase Orders",
            "items_table": "repository.Items_purchase_orders",
            "schema": "repository",
            "table": "Items_purchase_orders",
        },
    ],
    "7A8B9C0D-1E2F-3A4B-5C6D-7E8F9A0B1C2D": [
        {
            "id": "B2C3D4E5-F6A7-8B9C-0D1E-2F3A4B5C6D7E",
            "name": "HR Onboarding Files",
            "items_table": "repository.Items_hr_files",
            "schema": "repository",
            "table": "Items_hr_files",
        },
    ],
}

SAMPLE_WORKFLOWS: dict[str, list[dict[str, Any]]] = {
    DEFAULT_SAMPLE_TENANT_ID: [
        {
            "id": DEFAULT_SAMPLE_WORKFLOW_ID,
            "name": "AP Invoice Approval Workflow",
            "repository_id": DEFAULT_SAMPLE_REPO_ID,
        },
    ],
    "0B3E1B77-4A6C-46F2-83EE-2F0A5B84956B": [
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "3-Way PO Matching Workflow",
            "repository_id": "A1B2C3D4-E5F6-7A8B-9C0D-E1F2A3B4C5D6",
        },
    ],
}

SAMPLE_COLUMNS: dict[str, list[str]] = {
    DEFAULT_SAMPLE_REPO_ID: [
        "Id",
        "Supplier",
        "InvoiceAmount",
        "DueDate",
        "MatchedStatus",
        "InvoiceDate",
        "Currency",
        "Status",
        "AiStatus",
        "Department",
        "CreatedAt",
        "IsDeleted",
    ],
    "E2F3A4B5-6C7D-8E9F-0A1B-2C3D4E5F6A7B": [
        "Id",
        "Vendor",
        "ContractValue",
        "StartDate",
        "EndDate",
        "Status",
        "Department",
        "Category",
        "IsDeleted",
    ],
    "A1B2C3D4-E5F6-7A8B-9C0D-E1F2A3B4C5D6": [
        "Id",
        "Vendor",
        "Amount",
        "OrderDate",
        "DeliveryDate",
        "Status",
        "Department",
        "Category",
        "IsDeleted",
    ],
}


def _generate_sample_invoices() -> list[dict[str, Any]]:
    today = date.today()
    suppliers = [
        "Acme Supplies",
        "Global Tech Inc",
        "Apex Logistics",
        "Delta Cloud Services",
        "OmniCorp International",
        "Zenith Office Products",
        "Starlight Power & Utilities",
        "Pinnacle Hardware",
        "Nexus Consulting",
    ]
    currencies = ["USD", "USD", "USD", "EUR", "USD", "GBP", "USD"]
    statuses = ["Approved", "Pending", "In Review", "Approved", "Paid", "Rejected"]
    match_statuses = [
        "Matched",
        "Matched",
        "Not Matched",
        "Partially Matched",
        "Matched",
        "Not Matched",
    ]
    departments = ["Finance", "IT", "Operations", "Procurement", "Marketing", "Legal"]

    amounts = [
        450.00, 1250.50, 3400.00, 8900.00, 15400.00, 22000.00, 4800.00, 750.00,
        18500.00, 920.00, 6400.00, 31000.00, 2750.00, 12800.00, 45000.00, 5200.00,
        8300.00, 16200.00, 9500.00, 1400.00, 28000.00, 7100.00, 3900.00, 19500.00,
        8800.00, 2300.00, 11000.00, 36000.00, 6700.00, 4200.00, 15000.00, 50000.00,
    ]

    due_offsets = [
        -45, -32, -20, -15, -8, -3, -1, 0,
        2, 5, 10, 14, 21, 28, 35, 45,
        -60, -25, -10, 7, 18, 30, 60, -5,
        -18, 12, 25, 40, -40, 8, 15, 50,
    ]

    invoices: list[dict[str, Any]] = []
    for i in range(len(amounts)):
        inv_date = today + timedelta(days=due_offsets[i] - 30)
        due_d = today + timedelta(days=due_offsets[i])
        supplier = suppliers[i % len(suppliers)]
        match_st = match_statuses[i % len(match_statuses)]
        status = "Paid" if match_st == "Matched" and due_offsets[i] < 0 else statuses[i % len(statuses)]
        
        invoices.append({
            "Id": f"INV-2026-{1000 + i}",
            "Supplier": supplier,
            "InvoiceAmount": amounts[i],
            "DueDate": due_d,
            "MatchedStatus": match_st,
            "InvoiceDate": inv_date,
            "Currency": currencies[i % len(currencies)],
            "Status": status,
            "AiStatus": "Completed",
            "Department": departments[i % len(departments)],
            "CreatedAt": datetime.combine(inv_date, datetime.min.time()),
            "IsDeleted": 0,
        })
    return invoices


SAMPLE_ROWS: dict[str, list[dict[str, Any]]] = {
    DEFAULT_SAMPLE_REPO_ID: _generate_sample_invoices(),
}


def get_sample_repositories(tenant_id: str) -> list[dict[str, Any]]:
    clean = str(tenant_id or "").strip().upper()
    for tid, repos in SAMPLE_REPOSITORIES.items():
        if tid.upper() == clean:
            return repos
    return SAMPLE_REPOSITORIES[DEFAULT_SAMPLE_TENANT_ID]


def get_sample_workflows(tenant_id: str) -> list[dict[str, Any]]:
    clean = str(tenant_id or "").strip().upper()
    for tid, wfs in SAMPLE_WORKFLOWS.items():
        if tid.upper() == clean:
            return wfs
    return SAMPLE_WORKFLOWS.get(DEFAULT_SAMPLE_TENANT_ID, [])


def get_sample_target(
    tenant_id: str,
    repository_id: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    tid = str(tenant_id or DEFAULT_SAMPLE_TENANT_ID).strip()
    repos = get_sample_repositories(tid)
    target_repo = None

    if repository_id:
        clean_repo = repository_id.strip().upper()
        target_repo = next((r for r in repos if r["id"].upper() == clean_repo or clean_repo in r["name"].upper()), None)

    if not target_repo and workflow_id:
        wfs = get_sample_workflows(tid)
        clean_wf = workflow_id.strip().upper()
        wf = next((w for w in wfs if w["id"].upper() == clean_wf or clean_wf in w["name"].upper()), None)
        if wf and wf.get("repository_id"):
            target_repo = next((r for r in repos if r["id"].upper() == wf["repository_id"].upper()), None)

    if not target_repo:
        target_repo = repos[0]

    return {
        "tenant_id": tid,
        "repository_id": target_repo["id"],
        "repository_name": target_repo["name"],
        "workflow_id": workflow_id or DEFAULT_SAMPLE_WORKFLOW_ID,
        "workflow_name": "AP Invoice Approval Workflow",
        "form_id": None,
        "schema": target_repo.get("schema", "repository"),
        "table": target_repo.get("table", "Items_3ee0e334"),
        "qualified_table": target_repo.get("items_table", "repository.Items_3ee0e334"),
    }


def get_sample_columns(repository_id: str | None = None) -> list[str]:
    if repository_id:
        clean = repository_id.strip().upper()
        for rid, cols in SAMPLE_COLUMNS.items():
            if rid.upper() == clean:
                return cols
    return SAMPLE_COLUMNS[DEFAULT_SAMPLE_REPO_ID]


def get_sample_rows(repository_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    rows = SAMPLE_ROWS.get(DEFAULT_SAMPLE_REPO_ID, [])
    if repository_id:
        clean = repository_id.strip().upper()
        for rid, r in SAMPLE_ROWS.items():
            if rid.upper() == clean:
                rows = r
                break
    if limit is not None:
        return rows[:max(1, limit)]
    return list(rows)
