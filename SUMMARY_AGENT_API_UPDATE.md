# Summary Agent — API Request & Response Specification (Updated)

**Endpoint:** `POST /chat`  
**Intent:** `"summary"`  
**Local:** `http://localhost:8010` · Swagger `/docs` · Console `/console`

---

## 1. Overview

The Summary Agent generates a **locked structured summary** of a document or document data through a single chat endpoint. It supports:

| Source | Use when |
|--------|----------|
| `summary_json` | Structured data already available (invoice fields, API output, etc.) |
| `ocr_text` | OCR already done elsewhere |
| `file` | Direct browser/client upload |
| `filepath` | Document already in Azure Blob Storage |

Response always includes a short status `reply` plus locked `summary_result`.

---

## 2. Request structure

JSON envelope:

```json
{
  "session_id": "demo",
  "intent": "summary",
  "message": "optional note",
  "payload": {}
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `session_id` | **Yes** | Conversation / request session |
| `intent` | **Yes** | Must be `"summary"` for document jobs |
| `message` | No | Optional; defaulted when a document source is present |
| `payload` | Yes for document jobs | Source + optional controls |

**Headers**

| Transport | Header |
|-----------|--------|
| JSON | `Content-Type: application/json` |
| Multipart | `multipart/form-data` |

---

## 3. Supported input sources (precedence)

If multiple sources are supplied, **only the highest-priority source** is processed.

| Priority | Source | Recommended use | OCR |
|----------|--------|-----------------|-----|
| 1 | `summary_json` | Structured document data already available | No |
| 2 | `ocr_text` | OCR already performed | No |
| 3 | `file` (multipart) | Direct browser/client upload | Yes, where applicable |
| 4 | `filepath` | Document in Azure Blob Storage | Yes, where applicable |

**Relative blob path:** needs `tenant_id` → container `ezts{tenant_id}`.  
**Full blob URL:** may be passed as `filepath`.

---

## 4. Structured JSON input (`summary_json`)

```json
{
  "session_id": "invoice-summary-001",
  "intent": "summary",
  "payload": {
    "key_facts_count": 6,
    "model": "qwen3.5-9b",
    "summary_json": {
      "invoice_no": "INV/26-27/002140",
      "invoice_date": "2026-03-15",
      "due_date": "2026-04-14",
      "vendor": "Niss Internet Services",
      "customer": "Acme Corporation",
      "line_items": [
        {"description": "Internet Service", "amount": 1500.00},
        {"description": "GST 18%", "amount": 270.00}
      ],
      "total": 1770.00,
      "currency": "INR"
    }
  }
}
```

**Flow:** `summary_json` → validate → build context → LLM → locked `summary_result`.

**cURL**

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"invoice-summary-001\",\"intent\":\"summary\",\"payload\":{\"key_facts_count\":6,\"summary_json\":{\"vendor\":\"Niss Internet Services\",\"invoice_no\":\"INV/26-27/002140\",\"total\":1770.0,\"currency\":\"INR\"}}}"
```

---

## 5. Existing OCR text (`ocr_text`)

```json
{
  "session_id": "invoice-summary-002",
  "intent": "summary",
  "payload": {
    "key_facts_count": 6,
    "model": "qwen3.5-9b",
    "ocr_text": "Niss Internet Services\nInvoice Number: INV/26-27/002140\nInvoice Date: 15-Mar-2026\nDue Date: 14-Apr-2026\nInternet Service: 1500.00 INR\nGST: 270.00 INR\nTotal Amount: 1770.00 INR"
  }
}
```

**Flow:** `ocr_text` → validate → LLM → `summary_result` (no blob download, no Paddle).

---

## 6. Azure Blob document (`filepath`)

```json
{
  "session_id": "invoice-summary-003",
  "intent": "summary",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "invoices/2026/INV26-27002140.pdf",
    "pageno": "1",
    "key_facts_count": 6,
    "model": "qwen3.5-9b"
  }
}
```

**Flow:** blob download → OCR (Paddle for PDF/images) → text → LLM → `summary_result`.

---

## 7. Direct file upload (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=invoice-summary-005 \
  -F intent=summary \
  -F pageno=1 \
  -F key_facts_count=6 \
  -F model=qwen3.5-9b \
  -F file=@invoice.pdf
```

**Supported types:** PDF, PNG, JPG/JPEG, TIFF, DOCX, TXT.  
DOCX/TXT use local text extraction (no Paddle).

---

## 8. Optional request parameters

| Parameter | Description |
|-----------|-------------|
| `session_id` | Session id (**required**) |
| `intent` | Must be `"summary"` |
| `key_facts_count` | Max key facts (1–20, **default 6**). Wins over `summary_json.no` / `summary_json.key_facts_count` |
| `model` | Optional LLM override |
| `pageno` | `"1"` = one page; `"-1"` = pages 1..max. Ignored for `summary_json` / `ocr_text` |
| `tenant_id` | Required for relative blob `filepath` |
| `filepath` | Azure blob path or blob URL |
| `summary_json` | Structured document data |
| `ocr_text` | Existing OCR / plain text |
| `file` | Multipart upload |

Control keys inside `summary_json` (`no`, `key_facts_count`) are stripped before the LLM sees the data.

---

## 9. Success response

Same envelope for every input source:

```json
{
  "session_id": "invoice-summary-001",
  "reply": "Document summary generated successfully.",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "latency_ms": 18113.94,
  "token_usage": {
    "prompt_tokens": 959,
    "completion_tokens": 605,
    "total_tokens": 1564
  },
  "document_id": "summary_json",
  "chunk_ids": null,
  "cited_data_points": null,
  "ocr_result": null,
  "summary_result": {
    "confidence_score": 82.0,
    "document_type": "Invoice",
    "document_title": "Internet Service Invoice",
    "document_language": "English",
    "document_summary": "This is an invoice from <b><u>Niss Internet Services</u></b> for internet charges totaling <b><u>1770.00 INR</u></b>.",
    "key_facts_extracted": [
      "The invoice number is <b><u>INV/26-27/002140</u></b>.",
      "The vendor is Niss Internet Services.",
      "The total amount due is 1770.00 INR.",
      "GST of 270.00 INR is included in the total."
    ],
    "ocr_text": "{\"vendor\":\"Niss Internet Services\",\"invoice_no\":\"INV/26-27/002140\",\"total\":1770.0}",
    "source_reference": "summary_json"
  },
  "insight_result": null,
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null,
  "prompt_result": null,
  "pdf_result": null,
  "global_search_result": null
}
```

### Locked `summary_result` fields

| Key | Type | Description |
|-----|------|-------------|
| `confidence_score` | number | Model confidence |
| `document_type` | string | e.g. Invoice |
| `document_title` | string | Short title |
| `document_language` | string | e.g. English |
| `document_summary` | string | Narrative summary |
| `key_facts_extracted` | string[] | Up to `key_facts_count` facts |
| `ocr_text` | string | Source text (or stringified `summary_json`) |
| `source_reference` | string | `"summary_json"` \| `"ocr_text"` \| filepath \| filename |

### Highlight markup (important)

API consumers must expect **`<b><u>…</u></b>`** (bold + underline) in `document_summary` / `key_facts_extracted`.  
If the model emits `<mark>…</mark>`, the server **rewrites** it to `<b><u>…</u></b>` before response.

---

## 10. Fail-closed

When no usable text is available:

- `reply`: `"I couldn't extract any text from that document, so I can't summarize it."`
- `token_usage`: usually `null`
- `summary_result` still returned with empty/minimal locked shape

---

## 11. HTTP errors

| Status | Typical cause |
|--------|----------------|
| 400 | Content filter; empty upload; invalid `pageno` |
| 422 | Validation (missing `session_id` / bad payload) |
| 429 | Rate limit |
| 502 / 503 | Upstream OCR / LLM / store |

Body: `{ "detail": "…" }`

---

## 12. Client notes

1. Always send `intent: "summary"` for document jobs.  
2. Prefer `summary_json` when structured data already exists (fastest).  
3. Read `summary_result`, not only `reply`.  
4. Render highlights as bold+underline (`<b><u>`).  
5. Ignore extra keys outside the locked schema.
