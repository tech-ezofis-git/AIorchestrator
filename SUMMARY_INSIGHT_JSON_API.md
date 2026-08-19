# Summary & Insight — JSON Input Only (Request + Response)

**Endpoint:** `POST /chat`  
**Content-Type:** `application/json`  
**Local:** http://localhost:8010/docs

This document covers **JSON-data requests only** (`summary_json` / `insight_json`).  
OCR text, file upload, and blob path use the same **response shape** but are not listed here.

---

## Summary — JSON request

### Minimum request

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "summary_json": {
      "vendor": "Niss Internet Services",
      "invoice_no": "INV/26-27/002140",
      "total": 1770.00,
      "currency": "INR"
    }
  }
}
```

### With `no` (key facts count) inside JSON

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "summary_json": {
      "no": 4,
      "vendor": "Niss Internet Services",
      "invoice_no": "INV/26-27/002140",
      "total": 1770.00
    }
  }
}
```

### With `key_facts_count` at payload level (wins over `no`)

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "key_facts_count": 5,
    "summary_json": {
      "no": 4,
      "vendor": "Acme Corp",
      "total": 1000
    }
  }
}
```

→ Uses **5** facts (`payload.key_facts_count` wins).

### Optional fields

| Field | Location | Default | Range | Notes |
|---|---|---|---|---|
| `no` | inside `summary_json` | **6** | 1–20 | Shorthand for key-facts count |
| `key_facts_count` | `payload` | **6** | 1–20 | **Wins** over `summary_json.no` |
| `model` | `payload` | console default | — | e.g. `qwen3.5-9b` |

`no` and `key_facts_count` are **control fields** — stripped before the LLM runs.  
All other keys in `summary_json` are treated as document/data to summarize.

---

## Insight — JSON request

### Minimum request

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
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

### With `no` (insights count) inside JSON

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "insight_json": {
      "no": 3,
      "open_invoices": 120,
      "overdue_invoices": 18,
      "total_outstanding": 245000
    }
  }
}
```

### With `insights_count` + `insight_area` at payload level

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "insights_count": 4,
    "insight_area": "AP Aging Dashboard",
    "insight_json": {
      "no": 2,
      "dashboard": "AP Aging",
      "open_invoices": 120,
      "overdue_invoices": 18
    }
  }
}
```

→ Uses **4** insights (`payload.insights_count` wins) and area **"AP Aging Dashboard"** (`payload.insight_area` wins).

### Optional fields

| Field | Location | Default | Range | Notes |
|---|---|---|---|---|
| `no` | inside `insight_json` | **4** | 1–20 | Shorthand for insights count |
| `insights_count` | `payload` | **4** | 1–20 | **Wins** over `insight_json.no` |
| `insight_area` | `payload` | omitted | — | e.g. `AP Aging`, `Cash Flow` |
| `area` | inside `insight_json` | omitted | — | Alias for area (if payload area omitted) |
| `dashboard` | inside `insight_json` | omitted | — | Alias for area (if payload area omitted) |
| `model` | `payload` | console default | — | e.g. `qwen3.5-9b` |

Control keys (`no`, `insights_count`, `insight_area`, `area`, `dashboard`) are **stripped** before the LLM runs.

---

## Summary — JSON response (always same shape)

```json
{
  "session_id": "demo",
  "reply": "Document summary generated successfully.",
  "correlation_id": "abc-123",
  "latency_ms": 8500.5,
  "token_usage": {
    "prompt_tokens": 400,
    "completion_tokens": 200,
    "total_tokens": 600
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
    "document_summary": "This is an invoice from Niss Internet Services for internet charges.",
    "key_facts_extracted": [
      "The invoice number is INV/26-27/002140.",
      "The total amount due is 1770.00 INR."
    ],
    "ocr_text": "{\n  \"vendor\": \"Niss Internet Services\",\n  \"invoice_no\": \"INV/26-27/002140\",\n  \"total\": 1770.0\n}",
    "source_reference": "summary_json"
  },
  "insight_result": null,
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null
}
```

### `summary_result` keys (fixed)

| Key | Type | Notes |
|---|---|---|
| `confidence_score` | number | 0–100 |
| `document_type` | string | Inferred from JSON |
| `document_title` | string | |
| `document_language` | string | |
| `document_summary` | string | 2–3 sentences; max 3 `<b><u>` highlights |
| `key_facts_extracted` | string[] | Capped by `no` / `key_facts_count` (default **6**) |
| `ocr_text` | string | Pretty-printed input JSON (audit trace) |
| `source_reference` | string | Always `"summary_json"` for JSON input |

---

## Insight — JSON response (always same shape)

```json
{
  "session_id": "demo",
  "reply": "Insights generated successfully.",
  "correlation_id": "def-456",
  "latency_ms": 6200.3,
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
      "Overdue invoices represent 15% of open AP.",
      "The 90+ day bucket holds the largest share of outstanding balance."
    ],
    "insights_count": 4,
    "insight_area": "AP Aging Dashboard",
    "source_reference": "insight_json"
  },
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null
}
```

### `insight_result` keys (fixed)

| Key | Type | Notes |
|---|---|---|
| `insights` | string[] | Capped by `no` / `insights_count` (default **4**); max 1 `<mark>` per line |
| `insights_count` | number | Echo of limit used (requested or default) |
| `insight_area` | string | Present only when caller sent area / dashboard |
| `source_reference` | string | Always `"insight_json"` for JSON input |

---

## Fail-closed (empty JSON data)

**Summary** — e.g. `"summary_json": { "no": 6 }` only:

```json
{
  "reply": "I couldn't extract any text from that document, so I can't summarize it.",
  "document_id": "summary_json",
  "summary_result": {
    "confidence_score": 0.0,
    "document_type": "",
    "document_title": "",
    "document_language": "",
    "document_summary": "I couldn't extract any text from that document, so I can't summarize it.",
    "key_facts_extracted": [],
    "ocr_text": "",
    "source_reference": "summary_json"
  },
  "token_usage": null
}
```

**Insight** — e.g. `"insight_json": { "no": 4 }` only:

```json
{
  "reply": "I couldn't find usable data to generate insights.",
  "insight_result": {
    "insights": [],
    "insights_count": 4,
    "source_reference": "insight_json"
  },
  "token_usage": null
}
```

---

## Quick reference

| | Summary | Insight |
|---|---|---|
| `intent` | `"summary"` | `"insight"` |
| Data field | `payload.summary_json` | `payload.insight_json` |
| Count via `no` | default **6** | default **4** |
| Count via payload | `key_facts_count` | `insights_count` |
| Area / dashboard | — | `insight_area` (optional) |
| Result field | `summary_result` | `insight_result` |
| Same response shape for all JSON requests? | **Yes** | **Yes** |
