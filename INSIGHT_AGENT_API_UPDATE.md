# Insight Agent — API Request & Response Specification (Updated)

**Endpoint:** `POST /chat`  
**Intent:** `"insight"`  
**Local:** `http://localhost:8010` · Swagger `/docs` · Console `/console`

---

## 1. Overview

The Insight Agent generates a **locked list of insights** from dashboard/report data or document text through a single chat endpoint. It supports:

| Source | Use when |
|--------|----------|
| `insight_json` | Structured metrics / dashboard / report JSON already available |
| `ocr_text` | OCR or notes already available as text |
| `file` | Direct browser/client upload |
| `filepath` | Document already in Azure Blob Storage |

Response always includes a short status `reply` plus locked `insight_result`.

> **Not Summary.** Summary → narrative + key facts (`summary_result`).  
> Insight → bullet insights (`insight_result.insights[]`).

---

## 2. Request structure

JSON envelope:

```json
{
  "session_id": "demo",
  "intent": "insight",
  "message": "optional note",
  "instruction": "optional extra hint for the model",
  "payload": {}
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `session_id` | **Yes** | Conversation / request session |
| `intent` | **Yes** | Must be `"insight"` for document jobs |
| `message` | No | Optional; defaulted when a document source is present |
| `instruction` | No | Extra free-text hint for the model |
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
| 1 | `insight_json` | Structured dashboard / report data already available | No |
| 2 | `ocr_text` | OCR / notes already performed | No |
| 3 | `file` (multipart) | Direct browser/client upload | Yes, where applicable |
| 4 | `filepath` | Document in Azure Blob Storage | Yes, where applicable |

**Relative blob path:** needs `tenant_id` → container `ezts{tenant_id}`.  
**Full blob URL:** may be passed as `filepath`.

---

## 4. Structured JSON input (`insight_json`)

```json
{
  "session_id": "ap-insight-001",
  "intent": "insight",
  "payload": {
    "insights_count": 4,
    "insight_area": "AP Aging Dashboard",
    "model": "qwen3.5-9b",
    "insight_json": {
      "open_invoices": 120,
      "overdue_invoices": 18,
      "total_outstanding": 245000,
      "buckets": {
        "0_30": 80000,
        "31_60": 90000,
        "61_90": 45000,
        "90_plus": 30000
      }
    }
  }
}
```

**Flow:** `insight_json` → strip control keys → LLM → locked `insight_result`.

**cURL**

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"ap-insight-001\",\"intent\":\"insight\",\"payload\":{\"insights_count\":4,\"insight_area\":\"AP Aging Dashboard\",\"insight_json\":{\"open_invoices\":120,\"overdue_invoices\":18,\"total_outstanding\":245000}}}"
```

---

## 5. Existing OCR text (`ocr_text`)

```json
{
  "session_id": "ap-insight-002",
  "intent": "insight",
  "payload": {
    "insights_count": 3,
    "insight_area": "AP Aging",
    "model": "qwen3.5-9b",
    "ocr_text": "Open invoices: 50\nOverdue: 12\nOutstanding: $90,000\n90+ days: $20,000"
  }
}
```

**Flow:** `ocr_text` → LLM → `insight_result` (no blob download, no Paddle).

---

## 6. Azure Blob document (`filepath`)

```json
{
  "session_id": "ap-insight-003",
  "intent": "insight",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "reports/2026/aging-report.pdf",
    "pageno": "1",
    "insights_count": 4,
    "insight_area": "AP Aging",
    "model": "qwen3.5-9b"
  }
}
```

**Flow:** blob download → OCR (Paddle for PDF/images) → text → LLM → `insight_result`.

---

## 7. Direct file upload (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=ap-insight-005 \
  -F intent=insight \
  -F insights_count=4 \
  -F insight_area="AP Aging" \
  -F pageno=1 \
  -F model=qwen3.5-9b \
  -F file=@aging-report.pdf
```

**Supported types:** PDF, PNG, JPG/JPEG, TIFF, DOCX, TXT.  
DOCX/TXT use local text extraction (no Paddle).

---

## 8. Optional request parameters

| Parameter | Description |
|-----------|-------------|
| `session_id` | Session id (**required**) |
| `intent` | Must be `"insight"` |
| `insights_count` | Max insights (1–20, **default 4**). Wins over `insight_json.no` / `insight_json.insights_count` |
| `insight_area` | Business/domain context (e.g. `"AP Aging"`). Wins over JSON `insight_area` / `area` / `dashboard` |
| `model` | Optional LLM override |
| `pageno` | `"1"` = one page; `"-1"` = pages 1..max. Ignored for `insight_json` / `ocr_text` |
| `tenant_id` | Required for relative blob `filepath` |
| `filepath` | Azure blob path or blob URL |
| `insight_json` | Structured metrics / dashboard data |
| `ocr_text` | Existing OCR / plain text |
| `file` | Multipart upload |
| `instruction` | Optional top-level free-text hint |

Control keys inside `insight_json` (`no`, `insights_count`, `insight_area`, `area`, `dashboard`) are stripped before the LLM sees the metrics.

---

## 9. Success response

Same envelope for every input source:

```json
{
  "session_id": "ap-insight-001",
  "reply": "Insights generated successfully.",
  "correlation_id": "def-456-e29b-41d4-a716-446655440000",
  "latency_ms": 9200.3,
  "token_usage": {
    "prompt_tokens": 350,
    "completion_tokens": 150,
    "total_tokens": 500
  },
  "document_id": null,
  "chunk_ids": null,
  "cited_data_points": null,
  "ocr_result": null,
  "summary_result": null,
  "insight_result": {
    "insights": [
      "Overdue invoices represent <mark>15%</mark> of open AP.",
      "The 90+ day bucket holds a material share of outstanding balance.",
      "Total outstanding is concentrated across 31–60 and 0–30 day buckets.",
      "Open AP of 120 invoices warrants aging follow-up on the overdue set."
    ],
    "insights_count": 4,
    "insight_area": "AP Aging Dashboard",
    "source_reference": "insight_json"
  },
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null,
  "prompt_result": null,
  "pdf_result": null,
  "global_search_result": null
}
```

### Locked `insight_result` fields

| Key | Type | Description |
|-----|------|-------------|
| `insights` | string[] | Insight bullets (≤ requested count) |
| `insights_count` | int | Resolved max count (may be > `insights.length` if model returns fewer) |
| `insight_area` | string | Present only when an area was provided |
| `source_reference` | string | `"insight_json"` \| `"ocr_text"` \| filepath \| filename |

### Highlight markup (important)

Insight highlights use **`<mark>…</mark>`** (at most one important span per insight sentence).  
This differs from Summary, which returns **`<b><u>…</u></b>`**.

### `document_id`

For Insight document jobs, `document_id` is typically **`null`**.  
Use `insight_result.source_reference` as the source id.

---

## 10. Fail-closed

When no usable data is available:

- `reply`: `"I couldn't find usable data to generate insights."`
- `insight_result.insights`: `[]`
- `insight_result.insights_count`: requested/default count
- `token_usage`: may be `null`

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

1. Always send `intent: "insight"` for document jobs.  
2. Prefer `insight_json` for dashboards/metrics already in your API.  
3. Read `insight_result.insights`, not only `reply`.  
4. Use payload-level `insights_count` + `insight_area` for predictable UX.  
5. Do **not** expect Summary fields (`document_summary`, `key_facts_extracted`).  
6. Render Insight highlights as `<mark>`; Summary uses `<b><u>`.

---

## 13. Summary vs Insight (quick compare)

| | Summary | Insight |
|--|---------|---------|
| Intent | `summary` | `insight` |
| Primary JSON input | `summary_json` | `insight_json` |
| Count control | `key_facts_count` (default **6**) | `insights_count` (default **4**) |
| Area / domain hint | — | `insight_area` |
| Result object | `summary_result` | `insight_result` |
| Core output | narrative + key facts | `insights[]` list |
| Highlight markup | `<b><u>…</u></b>` | `<mark>…</mark>` |
| `document_id` | source id string | usually `null` (use `source_reference`) |
