# PDF Generator Agent — API & Integration Guide

The **PDF Generator Agent** (`intent: "pdf"`) transforms structured JSON data into publication-quality, styled PDF documents with automated layout, key-value cards, auto-wrapped data tables, running headers/footers with dynamic page numbering (*"Page X of Y"*), and customizable color themes.

---

## 1. Overview & Capabilities

- **Automatic Layout & Sectioning**: Automatically organizes scalar properties into highlight cards and key-value grids, list of objects into zebra-striped tables, long text into formatted paragraphs, and signature/approval keys into authorization blocks.
- **5 Professional Color Themes**:
  - `corporate_blue` (Default) — Deep navy primary, crisp slate accents.
  - `emerald` — Rich forest and mint palette, ideal for finance and eco-reports.
  - `graphite` — Minimalist slate & zinc monochrome, perfect for formal legal and corporate memos.
  - `purple` — Modern purple & violet palette.
  - `amber` — Warm golden amber palette.
- **Dynamic Header & Footer (NumberedCanvas)**:
  - Header: Document title & running category rule.
  - Footer: Generation timestamp, EZOFIS branding, and exact *"Page X of Y"* two-pass page numbering.
- **Direct Output Formats**:
  - `pdf_base64`: Standard base64-encoded PDF string for direct programmatic transmission or client storage.
  - `download_url`: `/api/pdf/download/{filename}` (Forces browser attachment download).
  - `preview_url`: `/api/pdf/preview/{filename}` (Inline browser rendering).
  - `file_path`: Absolute path on disk in `app/static/generated_pdfs/`.

---

## 2. API Reference

### `POST /chat`

Submit a standard JSON payload or multipart form data to `/chat`.

#### Request Body (JSON)

```json
{
  "session_id": "session-123",
  "intent": "pdf",
  "message": "Generate commercial invoice PDF",
  "payload": {
    "pdf_title": "Commercial Invoice INV-2026-0042",
    "pdf_theme": "corporate_blue",
    "pdf_json": {
      "invoice_number": "INV-2026-0042",
      "invoice_date": "2026-08-28",
      "due_date": "2026-09-28",
      "vendor_name": "Apex Cloud Systems Inc.",
      "vendor_address": "100 Innovation Way, Austin TX 78701",
      "customer_name": "Enterprise Holdings LLC",
      "customer_address": "500 Corporate Blvd, New York NY 10001",
      "currency": "USD",
      "items": [
        { "item": "AI Orchestration Platform License", "quantity": 1, "rate": 8500.00, "amount": 8500.00 },
        { "item": "Custom PDF Agent Integration", "quantity": 1, "rate": 3200.00, "amount": 3200.00 },
        { "item": "Multi-Tenant Cloud Configuration", "quantity": 2, "rate": 750.00, "amount": 1500.00 }
      ],
      "subtotal": 13200.00,
      "tax_amount": 1089.00,
      "total_amount": 14289.00,
      "notes": "Payment terms: Net 30 days. Thank you for your business!",
      "approved_by": "Finance Controller"
    }
  }
}
```

#### Response Structure

```json
{
  "session_id": "session-123",
  "reply": "PDF document 'Commercial_Invoice_INV-2026-0042_20260828131500.pdf' generated successfully (1 page, 14.2 KB).",
  "correlation_id": "9799b873-a9c1-48bf-b437-a0d93d600378",
  "latency_ms": 42.1,
  "token_usage": null,
  "pdf_result": {
    "status": "success",
    "filename": "Commercial_Invoice_INV-2026-0042_20260828131500.pdf",
    "file_path": "c:/Users/moham/Desktop/prompt/AIorchestrator/app/static/generated_pdfs/Commercial_Invoice_INV-2026-0042_20260828131500.pdf",
    "download_url": "/api/pdf/download/Commercial_Invoice_INV-2026-0042_20260828131500.pdf",
    "preview_url": "/api/pdf/preview/Commercial_Invoice_INV-2026-0042_20260828131500.pdf",
    "static_url": "/static/generated_pdfs/Commercial_Invoice_INV-2026-0042_20260828131500.pdf",
    "pdf_base64": "JVBERi0xLjQKJcTl8uXr...",
    "page_count": 1,
    "file_size_bytes": 14538,
    "title": "Commercial Invoice INV-2026-0042",
    "generated_at": "2026-08-28T13:15:00Z"
  }
}
```

---

## 3. Dedicated Download & Preview Endpoints

### 1. Download PDF (Attachment)
```http
GET /api/pdf/download/{filename}
```
- Sets `Content-Disposition: attachment; filename="{filename}"`
- Sets `Content-Type: application/pdf`

### 2. Preview PDF (Inline)
```http
GET /api/pdf/preview/{filename}
```
- Sets `Content-Disposition: inline; filename="{filename}"`
- Sets `Content-Type: application/pdf`
- Allows instant rendering inside browser tabs or iframe components.

### 3. List Pre-Installed Templates
```http
GET /api/pdf/templates
```
- Discovers and lists all pre-installed coordinate templates located in `templates/` (e.g. `Vessel_Call_FDA_Exact_Format.json`, `Vessel_Call_PDA_Exact_Format.json`).

Response:
```json
{
  "status": "success",
  "count": 2,
  "templates": [
    {
      "template_id": "Vessel_Call_FDA_Exact_Format",
      "filename": "Vessel_Call_FDA_Exact_Format.json",
      "title": "Vessel Call Fda Exact Format",
      "file_path": ".../templates/Vessel_Call_FDA_Exact_Format.json",
      "page_count": 2,
      "schema_fields_sample": ["vessel_name", "port_name", "agent_name", "disbursement_items"]
    },
    {
      "template_id": "Vessel_Call_PDA_Exact_Format",
      "filename": "Vessel_Call_PDA_Exact_Format.json",
      "title": "Vessel Call Pda Exact Format",
      "file_path": ".../templates/Vessel_Call_PDA_Exact_Format.json",
      "page_count": 1,
      "schema_fields_sample": ["vessel_name", "proforma_ref", "port_name", "estimated_costs"]
    }
  ]
}
```

---

## 4. Coordinate Template Mode

When a `template_name` or `template_json` is specified, the engine switches to exact coordinate-based rendering using `pdfme`-style template schemas:

```json
{
  "session_id": "session-tpl-1",
  "intent": "pdf",
  "payload": {
    "template_name": "Vessel_Call_FDA_Exact_Format",
    "pdf_title": "Final Disbursement Account — M/V PACIFIC HORIZON",
    "pdf_json": {
      "vessel_name": "M/V PACIFIC HORIZON",
      "call_number": "VC-2026-8841",
      "port_name": "PORT OF SINGAPORE",
      "total_disbursement": "20980.00",
      "advance_received": "25000.00",
      "balance_due_to_principal": "4020.00"
    }
  }
}
```

- **Fuzzy template matching**: Accepts `"fda"`, `"pda"`, `"vessel_call_fda"`, `"vessel_call_pda"`, or full filename `"Vessel_Call_FDA_Exact_Format"`.
- **Variable Interpolation**: Automatically replaces `{{var}}`, `${var}`, `{var}`, `dataKey`, and `sourceFieldId`.
- **Dynamic Table Splitting & Border Math**: Calculates column percentages and applies zebra shading and cell grid rules.

---

## 5. Test Console (`/console`)

1. Open `http://localhost:8010/console` in your browser.
2. Click the **PDF generator** action button above the composer.
3. Choose a template (e.g. **Vessel Call FDA Template**, **Vessel Call PDA Template**, or **Auto-Layout**).
4. Click **FDA Sample**, **PDA Sample**, **Invoice**, or **Report** to populate structured JSON data.
5. Click **Send** / **Generate PDF**.
6. The assistant bubble returns immediate download and inline preview buttons along with page count and file size badge.
