# OCR Agent — API Request & Response Specification (Updated)

**Endpoint:** `POST /chat`  
**Intent:** `"ocr"`  
**Local:** `http://localhost:8010` · Swagger `/docs` · Console `/console`

---

## 1. Overview

The OCR Agent extracts text from a document and returns **locked structured fields** through a single chat endpoint. It supports:

| Source | Use when |
|--------|----------|
| `file` (multipart) | Direct browser/client upload |
| `filepath` | Document already in Azure Blob Storage |

Unlike Summary / Insight, OCR does **not** accept `summary_json` / `insight_json` / `ocr_text` as the document source for `intent: "ocr"`. Those belong to other agents.

Response includes:

- `reply` — JSON **string** of the core OCR payload (see §9)
- `ocr_result` — full locked object (fields + status + source)

---

## 2. Request structure

JSON envelope:

```json
{
  "session_id": "demo",
  "intent": "ocr",
  "message": "optional note",
  "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
  "payload": {}
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `session_id` | **Yes** | Conversation / request session |
| `intent` | **Yes** | Must be `"ocr"` for document jobs |
| `message` | No | Optional; defaulted when file/filepath is present |
| `instruction` | No | **Top-level** (not inside `payload`). Structuring hints for the LLM |
| `payload` | Yes for document jobs | Source + field/table parameters |

**Headers**

| Transport | Header |
|-----------|--------|
| JSON | `Content-Type: application/json` |
| Multipart | `multipart/form-data` |

---

## 3. Supported input sources (precedence)

If both are supplied, **multipart `file` wins** and `filepath` is ignored.

| Priority | Source | Recommended use | Remote OCR |
|----------|--------|-----------------|------------|
| 1 | `file` (multipart) | Direct browser/client upload | Yes for PDF/images (when extract URL configured) |
| 2 | `filepath` | Document in Azure Blob Storage | Yes for PDF/images |

**Relative blob path:** needs `tenant_id` → container `ezts{tenant_id}` (hyphens stripped from tenant id in container name).  
**Full blob URL:** may be passed as `filepath`.

> **Note:** `payload.ocr_text` is **not** an OCR-agent input. Use Summary/Insight with `ocr_text` if text is already extracted.

---

## 4. Azure Blob document (`filepath`)

```json
{
  "session_id": "ocr-demo-001",
  "intent": "ocr",
  "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "invoices/2026/INV26-27002140.pdf",
    "pageno": "1",
    "parameters": [
      "Invoice No,SHORT_TEXT",
      "Due Date,DATE",
      "Total Amount,AMOUNT"
    ],
    "tableparameters": [],
    "model": "qwen3.5-9b"
  }
}
```

**Flow:** blob download → extract text (local and/or Paddle) → LLM field structuring → locked `ocr_result`.

**cURL**

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"ocr-demo-001\",\"intent\":\"ocr\",\"instruction\":\"Region: India. Normalize DATE fields to YYYY-MM-DD.\",\"payload\":{\"tenant_id\":\"2e3b7b37-38a3-4f94-878e-a006dad93230\",\"filepath\":\"invoices/2026/INV26-27002140.pdf\",\"pageno\":\"1\",\"parameters\":[\"Invoice No,SHORT_TEXT\",\"Due Date,DATE\"]}}"
```

---

## 5. Direct file upload (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=ocr-demo-002 \
  -F intent=ocr \
  -F pageno=1 \
  -F "instruction=Region: India. Normalize DATE fields to YYYY-MM-DD." \
  -F "parameters=Invoice No,SHORT_TEXT" \
  -F "parameters=Due Date,DATE" \
  -F "parameters=Total Amount,AMOUNT" \
  -F model=qwen3.5-9b \
  -F file=@invoice.pdf
```

`parameters` / `tableparameters` may also be sent as a JSON-array string, e.g. `["Invoice No,SHORT_TEXT"]`.

---

## 6. Optional request parameters

| Parameter | Where | Description |
|-----------|--------|-------------|
| `session_id` | root | Session id (**required**) |
| `intent` | root | Must be `"ocr"` |
| `instruction` | root | Structuring hints (DATE normalize, region, etc.) |
| `tenant_id` | payload | Required for relative blob `filepath` |
| `filepath` | payload | Azure blob path or blob URL |
| `file` | multipart | Document upload |
| `pageno` | payload / form | Page selector (see §7) |
| `parameters` | payload / form | Field list: `"Name,TYPE"` |
| `tableparameters` | payload / form | Table column hints (strings) |
| `model` | payload / form | Optional LLM override for structuring |

### Field types for `parameters`

`SHORT_TEXT`, `LONG_TEXT`, `DATE`, `NUMBER`, `AMOUNT`, `CURRENCY`, `BOOLEAN`, `EMAIL`, `PHONE`

When `parameters` is provided, the response `ocrResult` is **ordered to those exact names/types** (missing values → `null`).  
When `parameters` is empty, the model may recommend up to **`OCR_MAX_RECOMMENDED_FIELDS`** fields (default **15**).

---

## 7. `pageno` semantics

Default max pages: **`OCR_MAX_PAGES` = 5**.

| Value | Meaning |
|-------|---------|
| omit / `""` / `"1"` | Page 1 only |
| `"2"` … `"5"` | That single page (≤ max) |
| `"-1"` | Pages `1..OCR_MAX_PAGES` |
| other | HTTP **400** (`InvalidOcrPageError`) |

---

## 8. Supported file types

| Type | Behavior |
|------|----------|
| PDF | Local text if usable; else remote extract (Paddle) when configured |
| PNG / JPG / JPEG / TIFF | Remote extract when configured |
| DOCX | Local text extract (no Paddle) |
| TXT | Local UTF-8 decode |
| DOC | Rejected |
| Max size | **25 MiB** (`OCR_MAX_FILE_BYTES`) |

Empty upload → HTTP **400** `"Uploaded file is empty."`

---

## 9. Success response

```json
{
  "session_id": "ocr-demo-001",
  "reply": "{\"ocrResult\":[{\"name\":\"Invoice No\",\"value\":\"INV/26-27/002140\",\"type\":\"SHORT_TEXT\"},{\"name\":\"Due Date\",\"value\":\"2026-05-20\",\"type\":\"DATE\"}],\"tableResult\":[],\"ocr_text\":\"...\"}",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "latency_ms": 12450.2,
  "token_usage": {
    "prompt_tokens": 800,
    "completion_tokens": 200,
    "total_tokens": 1000
  },
  "document_id": null,
  "chunk_ids": null,
  "cited_data_points": null,
  "ocr_result": {
    "ocrResult": [
      {"name": "Invoice No", "value": "INV/26-27/002140", "type": "SHORT_TEXT"},
      {"name": "Due Date", "value": "2026-05-20", "type": "DATE"},
      {"name": "Total Amount", "value": "1770.00", "type": "AMOUNT"}
    ],
    "tableResult": [],
    "ocr_text": "<raw extracted text from the document>",
    "source_reference": "invoices/2026/INV26-27002140.pdf",
    "ocr_status": "success"
  },
  "summary_result": null,
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

### Locked `ocr_result` fields

| Key | Type | Description |
|-----|------|-------------|
| `ocrResult` | object[] | `{ "name", "value", "type" }` — `value` may be `null` |
| `tableResult` | array | Table extractions, or `[]` |
| `ocr_text` | string | Raw extracted text |
| `source_reference` | string | filepath / filename / `"upload"` |
| `ocr_status` | string | `"success"` or `"fallback"` |
| `mock` | boolean | Optional; present when mock OCR engine path is used |

### Important: `reply` vs `ocr_result`

| | `reply` | `ocr_result` |
|--|---------|--------------|
| Type | **string** (JSON text) | object |
| Contains | `ocrResult`, `tableResult`, `ocr_text` | same + `source_reference`, `ocr_status` |

Clients should prefer parsing **`ocr_result`**. If reading `reply`, `JSON.parse(reply)` first.

This differs from Summary/Insight, where `reply` is a short human status line.

---

## 10. Fail-closed (no usable text)

HTTP **200** (not 502) when extract fails or text is empty:

- `ocr_result.ocr_text`: `""`
- Each requested `parameters` entry: `value: null`
- `tableResult`: `[]`
- `ocr_status`: `"fallback"`
- `token_usage`: `null`
- `reply`: JSON string of the same core payload (null values / empty text)

---

## 11. HTTP errors

| Status | Typical cause |
|--------|----------------|
| 400 | Content filter; empty upload; invalid `pageno` |
| 422 | Validation (missing `session_id` / bad payload) |
| 429 | Rate limit |
| 502 / 503 | Upstream infrastructure (legacy keyword OCR path / store outages) |

Body: `{ "detail": "…" }`

---

## 12. Client notes

1. Always send `intent: "ocr"` for document OCR jobs.  
2. Prefer reading **`ocr_result`**, not only `reply`.  
3. Pass explicit `parameters` when the product knows required fields (stable order + nulls for missing).  
4. Put structuring hints in top-level **`instruction`**, not inside `payload`.  
5. Do not send `ocr_text` expecting OCR agent to skip extraction — use Summary/Insight for that.  
6. DATE values are expected as `YYYY-MM-DD` when the model can normalize them.

---

## 13. OCR vs Summary vs Insight (quick compare)

| | OCR | Summary | Insight |
|--|-----|---------|---------|
| Intent | `ocr` | `summary` | `insight` |
| Primary inputs | `file` / `filepath` | `summary_json` / `ocr_text` / file / filepath | `insight_json` / `ocr_text` / file / filepath |
| Result object | `ocr_result` | `summary_result` | `insight_result` |
| Core output | `ocrResult[]` + `ocr_text` | narrative + key facts | `insights[]` |
| `reply` shape | JSON **string** of OCR payload | short status text | short status text |
| Field schema control | `parameters` / `tableparameters` | `key_facts_count` | `insights_count` + `insight_area` |

---

## 14. Related env / code (reference)

- Agent: `app/agents/ocr_agent.py`
- Engine: `app/integrations/ocr_engine.py`
- Skills pack: `skills/ocr/`
- Chat models: `app/models/chat.py`
- Key env: `OCR_EXTRACT_URL`, `OCR_MAX_PAGES`, `OCR_MAX_RECOMMENDED_FIELDS`, `OCR_MAX_FILE_BYTES`, `AZURE_STORAGE_CONNECTION_STRING`
