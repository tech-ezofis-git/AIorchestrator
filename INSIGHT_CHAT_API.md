# Insight Agent — All Request Formats

**Endpoint:** `POST /chat` only (no `/insight` URL)  
**Intent:** `"insight"`  
**Local:** http://localhost:8010/docs · http://localhost:8010/console

---

## What you get

A short status `reply` plus locked `insight_result`. Other agent fields (`summary_result`, `ocr_result`, `ap_result`, …) are always `null` for document jobs.

---

## Input precedence (one source wins)

```
1. payload.insight_json   → skip blob, Paddle, and ocr_text
2. payload.ocr_text       → skip blob and Paddle
3. multipart file         → upload (PDF/image → Paddle; .docx → local extract)
4. payload.filepath       → Azure blob download + Paddle
```

If multiple sources are sent, only the highest row above is used.

**Transport:** Each input type below works as **JSON** (`Content-Type: application/json`) **or** **multipart** (`multipart/form-data`).

---

## Shared request envelope

JSON body shape:

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": { }
}



## 1. JSON input (`insight_json`)

Best when you already have structured dashboard or report data. No file upload, no OCR.

### Minimal request

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "insight_json": {
      "open_invoices": 120,
      "overdue_invoices": 18,
      "total_outstanding": 245000
    }
  }
}
```

### Full request (all optional keys)

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "insights_count": 4,
    "insight_area": "AP Aging Dashboard",
    "model": "qwen3.5-9b",
    "insight_json": {
      "no": 4,
      "dashboard": "AP Aging Dashboard",
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

### cURL (JSON)

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo",
    "intent": "insight",
    "payload": {
      "insights_count": 4,
      "insight_area": "AP Aging",
      "insight_json": {
        "open_invoices": 50,
        "overdue_invoices": 12,
        "total_outstanding": 90000
      }
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=insight \
  -F insights_count=4 \
  -F insight_area="AP Aging" \
  -F 'insight_json={"open_invoices":50,"overdue_invoices":12,"total_outstanding":90000}'
```

---

## 2. OCR text input (`ocr_text`)

Best when text was extracted elsewhere (another service, manual paste, prior `/chat` OCR call).

### Minimal request

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "ocr_text": "Open invoices: 50\nOverdue: 12\nOutstanding: $90,000"
  }
}
```

### Full request (all optional keys)

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "ocr_text": "Open invoices: 50\nOverdue: 12\nOutstanding: $90,000\n90+ bucket: $25,000",
    "insights_count": 4,
    "insight_area": "AP Aging",
    "model": "qwen3.5-9b"
  }
}
```

### cURL (JSON)

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo",
    "intent": "insight",
    "payload": {
      "ocr_text": "Open invoices: 50\nOverdue: 12",
      "insights_count": 3,
      "insight_area": "AP Aging"
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=insight \
  -F insights_count=3 \
  -F insight_area="AP Aging" \
  -F ocr_text="Open invoices: 50\nOverdue: 12"
```

---

## 3. Blob path input (`filepath` + `tenant_id`)

Best when the file already lives in Azure Blob Storage for the tenant.

### Minimal request

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "aging-report.pdf"
  }
}
```

### Full request (all optional keys)

```json
{
  "session_id": "demo",
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

### Full blob URL (no `tenant_id`)

```json
{
  "session_id": "demo",
  "intent": "insight",
  "payload": {
    "filepath": "https://youraccount.blob.core.windows.net/ezts2e3b7b37.../aging-report.pdf",
    "pageno": "-1",
    "insights_count": 5,
    "insight_area": "Cash Flow"
  }
}
```

### cURL (JSON)

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo",
    "intent": "insight",
    "payload": {
      "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
      "filepath": "aging-report.pdf",
      "pageno": "1",
      "insights_count": 4,
      "insight_area": "AP Aging"
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=insight \
  -F tenant_id=2e3b7b37-38a3-4f94-878e-a006dad93230 \
  -F filepath=aging-report.pdf \
  -F pageno=1 \
  -F insights_count=4 \
  -F insight_area="AP Aging"
```

---

## 4. Multipart file upload (`file`)

Best for direct upload from a browser or script. Send `multipart/form-data` (not JSON body).

### Minimal request

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=insight \
  -F file=@report.pdf
```

### Full request (all applicable form fields)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=insight \
  -F pageno=1 \
  -F insights_count=4 \
  -F insight_area="AP Aging" \
  -F model=qwen3.5-9b \
  -F file=@report.pdf
```

Supported file types: PDF, PNG, JPG, TIFF, `.docx`, `.txt`. `.docx` is extracted locally (no Paddle).

---

## Response envelope (`ChatResponse`)

Same shape for **every** input type above.

```json
{
  "session_id": "demo",
  "reply": "Insights generated successfully.",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "latency_ms": 9200.12,
  "token_usage": {
    "prompt_tokens": 400,
    "completion_tokens": 180,
    "total_tokens": 580
  },
  "document_id": null,
  "chunk_ids": null,
  "cited_data_points": null,
  "ocr_result": null,
  "summary_result": null,
  "insight_result": { },
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null
}
```



## `insight_result` (locked output)

Identical structure regardless of whether input was JSON, OCR text, blob, or upload.

```json
{
  "insights": [
    "Overdue invoices represent <mark>15%</mark> of open AP.",
    "The 90+ day bucket holds the largest share of outstanding balance.",
    "Total outstanding exceeds $200K with concentration in the 31–60 day range."
  ],
  "insights_count": 4,
  "insight_area": "AP Aging Dashboard",
  "source_reference": "insight_json"
}
```



## Fail-closed (empty source)

No LLM call when the chosen source has no usable content.

```json
{
  "session_id": "demo",
  "reply": "I couldn't find usable data to generate insights.",
  "correlation_id": "…",
  "latency_ms": 12.5,
  "token_usage": null,
  "document_id": null,
  "insight_result": {
    "insights": [],
    "insights_count": 4,
    "source_reference": "insight_json"
  },
  "summary_result": null,
  "ocr_result": null,
  "ap_result": null
}
```

