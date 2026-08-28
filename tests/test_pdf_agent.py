"""Unit tests for the PDF generator engine and PdfAgent."""
import os
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agents.pdf_agent import PdfAgent
from app.pdf_skills.pdf_generator import generate_pdf_from_json, PdfGenerationResult
from app.pdf_skills.rules import get_theme, infer_title, sanitize_filename


def test_infer_title_and_sanitize_filename():
    data = {"invoice_number": "INV-2026-99", "vendor": "Acme Corp"}
    assert infer_title(data, "Custom Title") == "Custom Title"
    assert "invoice" in infer_title(data).lower() or "document" in infer_title(data).lower()

    bad_name = "Invoice/2026:01*Special?.pdf"
    clean = sanitize_filename(bad_name)
    assert "/" not in clean and ":" not in clean and "*" not in clean and "?" not in clean
    assert clean.endswith(".pdf")


def test_theme_palettes():
    for theme_key in ["corporate_blue", "emerald", "graphite", "purple", "amber"]:
        theme = get_theme(theme_key)
        assert isinstance(theme, dict)
        assert "primary" in theme
        assert "secondary" in theme
        assert "table_head_bg" in theme
        assert "table_alt_bg" in theme


def test_generate_pdf_simple_key_value():
    data = {
        "title": "System Diagnostic Report",
        "status": "Healthy",
        "uptime_hours": 720,
        "active_users": 1540,
        "environment": "Production",
        "notes": "All health checks passed with zero errors in the past 30 days."
    }
    result = generate_pdf_from_json(data, title="System Diagnostic Report", theme_name="corporate_blue")
    assert isinstance(result, PdfGenerationResult)
    assert result.page_count >= 1
    assert result.file_size_bytes > 0
    assert len(result.pdf_base64) > 0
    # Verify valid base64
    raw_bytes = base64.b64decode(result.pdf_base64)
    assert raw_bytes.startswith(b"%PDF")


def test_generate_pdf_nested_tables_and_arrays():
    data = {
        "invoice_number": "INV-2026-088",
        "invoice_date": "2026-08-28",
        "due_date": "2026-09-28",
        "vendor": "Acme Solutions Ltd",
        "customer": "Global Corp Inc",
        "items": [
            {"item": "Enterprise AI Orchestrator License", "quantity": 1, "rate": 5000.0, "amount": 5000.0},
            {"item": "Custom PDF Generator Agent Skill", "quantity": 1, "rate": 2500.0, "amount": 2500.0},
            {"item": "Dedicated GPU Cluster (1 Month)", "quantity": 2, "rate": 1200.0, "amount": 2400.0},
        ],
        "subtotal": 9900.0,
        "tax_amount": 990.0,
        "total_amount": 10890.0,
        "currency": "USD",
        "notes": "Payment terms: Net 30 days. Thank you for your business!",
        "approved_by": "John Doe, Financial Controller"
    }
    result = generate_pdf_from_json(data, title="Commercial Invoice", theme_name="emerald")
    assert result.page_count >= 1
    assert result.file_size_bytes > 1000
    raw_bytes = base64.b64decode(result.pdf_base64)
    assert raw_bytes.startswith(b"%PDF")


def test_generate_pdf_list_of_records():
    data = [
        {"employee_id": "EMP-001", "name": "Alice Smith", "department": "Engineering", "role": "Lead Architect"},
        {"employee_id": "EMP-002", "name": "Bob Jones", "department": "Product", "role": "Product Manager"},
        {"employee_id": "EMP-003", "name": "Charlie Brown", "department": "Operations", "role": "DevOps Engineer"},
    ]
    result = generate_pdf_from_json(data, title="Employee Roster", theme_name="graphite")
    assert result.page_count >= 1
    assert result.file_size_bytes > 0


@pytest.mark.asyncio
async def test_pdf_agent_handle_success():
    llm_mock = MagicMock()
    llm_mock.chat_completion = AsyncMock()
    settings_mock = MagicMock()
    settings_mock.api_base_url = "http://localhost:8010"

    agent = PdfAgent(llm_adapter=llm_mock, settings=settings_mock)

    document_job = {
        "pdf_json": {
            "title": "Expense Voucher",
            "voucher_id": "VCH-2026-001",
            "amount": "$450.00",
            "category": "Travel & Meals",
            "employee": "Sarah Connor"
        },
        "pdf_title": "Travel Expense Voucher",
        "pdf_theme": "purple"
    }

    resp = await agent.handle(
        session_id="test-session-123",
        message="Generate PDF for expense voucher",
        document_job=document_job
    )

    assert "reply" in resp
    assert "pdf_result" in resp
    pdf_res = resp["pdf_result"]
    assert pdf_res["status"] == "success"
    assert pdf_res["page_count"] >= 1
    assert pdf_res["title"] == "Travel Expense Voucher"
    assert pdf_res["download_url"].startswith("/api/pdf/download/")
    assert pdf_res["preview_url"].startswith("/api/pdf/preview/")
    assert os.path.exists(pdf_res["file_path"])


def test_generate_pdf_ezofis_workflow_form():
    data = {
        "workflowName": "Expense Reimbursement",
        "requestNo": "REQ-2026-9901",
        "requestedBy": "jane.doe@company.com",
        "requestedOn": "2026-08-28",
        "formFields": {
            "panels": [
                {
                    "title": "Expense Overview",
                    "mainFields": [
                        {"name": "Category", "value": "Client Entertainment", "type": "TEXT"},
                        {"name": "Total Amount", "value": "{\"currency\": \"USD\", \"value\": \"1,250.00\"}", "type": "CURRENCY_AMOUNT"},
                        {"name": "Contact", "value": "{\"code\": \"1\", \"phoneNo\": \"5551234567\", \"verified\": true}", "type": "PHONE_NUMBER"},
                        {"name": "Expense Date", "value": "2026-08-25", "type": "DATE"},
                        {"name": "Approvals Needed", "value": "['Department Head', 'Finance VP']", "type": "MULTIPLE_CHOICE"},
                        {"name": "Notes", "value": "<p>Meeting with <b>Global Partners</b> regarding AI platform integration.</p>", "type": "TEXT"}
                    ],
                    "tables": [
                        {
                            "Line Items": [
                                {"Item": "Conference Dinner", "Attendees": 4, "Amount": 850.00},
                                {"Item": "Taxi Transfers", "Attendees": 2, "Amount": 400.00}
                            ]
                        }
                    ]
                }
            ]
        },
        "formSignature": {
            "Department Head": "Approved by Alice Smith on 2026-08-28",
            "Finance VP": "Approved by Bob Vance on 2026-08-28"
        },
        "processHistory": [
            {
                "stage": "Request Created",
                "actionUser": "Jane Doe",
                "receivedOn": "2026-08-28 09:00",
                "processedBy": "Jane Doe",
                "processedOn": "2026-08-28 09:05",
                "status": "Submitted"
            },
            {
                "stage": "Manager Approval",
                "actionUser": "Alice Smith",
                "receivedOn": "2026-08-28 09:05",
                "processedBy": "Alice Smith",
                "processedOn": "2026-08-28 10:15",
                "status": "Approved"
            }
        ]
    }
    result = generate_pdf_from_json(data, theme_name="corporate_blue")
    assert result.page_count >= 1
    assert result.file_size_bytes > 1500
    raw_bytes = base64.b64decode(result.pdf_base64)
    assert raw_bytes.startswith(b"%PDF")


def test_generate_pdf_discharge_summary_structure():
    data = {
        "workflowName": "Patient Discharge Summary",
        "requestNo": "DIS-88219",
        "requestedBy": "dr.watson@hospital.org",
        "requestedOn": "2026-08-28",
        "patient": {
            "name": "Arthur Pendelton",
            "id": "HC-99201",
            "age": "45",
            "sex": "M",
            "dob": "1981-04-12",
            "contact": "+1 555-0199"
        },
        "admission": {
            "admissionDate": "2026-08-20",
            "dischargeDate": "2026-08-28",
            "consultant": "Dr. Sarah Watson, MD"
        },
        "clinical": {
            "diagnosis": "Acute Bronchitis (Resolved)",
            "treatmentSummary": "Course of antibiotics and bronchodilator therapy administered. Patient stable.",
            "allergies": "Penicillin"
        },
        "followUp": {
            "instructions": "Follow up with primary care physician in 2 weeks. Continue hydration and rest."
        },
        "signatures": [
            {"role": "Attending Physician", "name": "Dr. Sarah Watson, MD", "date": "2026-08-28"},
            {"role": "Discharge Nurse", "name": "Nurse Kelly Roberts, RN", "date": "2026-08-28"}
        ]
    }
    result = generate_pdf_from_json(data, theme_name="emerald")
    assert result.page_count >= 1
    assert result.file_size_bytes > 1500
    raw_bytes = base64.b64decode(result.pdf_base64)
    assert raw_bytes.startswith(b"%PDF")
