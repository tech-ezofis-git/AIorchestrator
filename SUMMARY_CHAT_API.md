# Summary Agent — All Request Formats

**Endpoint:** `POST /chat` only (no `/summary` URL)  
**Intent:** `"summary"`  
**Local:** http://localhost:8010/docs · http://localhost:8010/console

---

## What you get

A short status `reply` plus locked `summary_result`. Other agent fields (`ocr_result`, `insight_result`, `ap_result`, …) are always `null` for document jobs.

---

## Input precedence (one source wins)

```
1. payload.summary_json   → skip blob, Paddle, and ocr_text
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
  "intent": "summary",
  "payload": { }
}





---

## 1. JSON input (`summary_json`)

Best when you already have structured data (invoice fields, API response, dashboard row, etc.). No file upload, no OCR.

### Minimal request

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

### Full request (all optional keys)

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "key_facts_count": 4,
    "model": "qwen3.5-9b",
    "summary_json": {
      "no": 4,
      "vendor": "Niss Internet Services",
      "invoice_no": "INV/26-27/002140",
      "invoice_date": "2026-03-15",
      "due_date": "2026-04-14",
      "line_items": [
        { "description": "Internet charges", "amount": 1500.00 },
        { "description": "GST 18%", "amount": 270.00 }
      ],
      "total": 1770.00,
      "currency": "INR",
      "customer": "Acme Corp"
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
    "intent": "summary",
    "payload": {
      "key_facts_count": 4,
      "summary_json": {
        "vendor": "Acme",
        "total": 100,
        "currency": "USD"
      }
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F key_facts_count=4 \
  -F 'summary_json={"vendor":"Acme","total":100,"currency":"USD"}'
```

---

## 2. OCR text input (`ocr_text`)

Best when OCR already ran elsewhere (another service, manual paste, prior `/chat` OCR call).

### Minimal request

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "ocr_text": "Niss Internet Services\nInvoice Number: INV/26-27/002140\nTotal: 1770.00 INR"
  }
}
```

### Full request (all optional keys)

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "ocr_text": "Niss Internet Services\nInvoice Number: INV/26-27/002140\nInvoice Date: 15-Mar-2026\nTotal: 1770.00 INR\nGST: 270.00",
    "key_facts_count": 6,
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
    "intent": "summary",
    "payload": {
      "ocr_text": "Vendor: Acme\nTotal: 500 USD",
      "key_facts_count": 5
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F key_facts_count=5 \
  -F ocr_text="Vendor: Acme\nTotal: 500 USD"
```

---

## 3. Blob path input (`filepath` + `tenant_id`)

Best when the file already lives in Azure Blob Storage for the tenant.

### Minimal request

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "INV26-27002140.pdf"
  }
}
```

### Full request (all optional keys)

```json
{
  "session_id": "demo",
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

### Full blob URL (no `tenant_id`)

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "filepath": "https://youraccount.blob.core.windows.net/ezts2e3b7b37.../invoice.pdf",
    "pageno": "-1",
    "key_facts_count": 8
  }
}
```

### cURL (JSON)

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo",
    "intent": "summary",
    "payload": {
      "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
      "filepath": "INV26-27002140.pdf",
      "pageno": "1",
      "key_facts_count": 6
    }
  }'
```

### cURL (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F tenant_id=2e3b7b37-38a3-4f94-878e-a006dad93230 \
  -F filepath=INV26-27002140.pdf \
  -F pageno=1 \
  -F key_facts_count=6
```

---

## 4. Multipart file upload (`file`)

Best for direct upload from a browser or script. Send `multipart/form-data` (not JSON body).

### Minimal request

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F file=@invoice.pdf
```

### Full request (all applicable form fields)

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F pageno=1 \
  -F key_facts_count=6 \
  -F model=qwen3.5-9b \
  -F file=@invoice.pdf
```

Supported file types: PDF, PNG, JPG, TIFF, `.docx`, `.txt`. `.docx` is extracted locally (no Paddle).

---

## Response envelope (`ChatResponse`)

Same shape for **every** input type above.

```json
{
  "session_id": "demo",
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
  "summary_result": { },
  "insight_result": null,
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null,
  "ap_result": null
}
```



---

## `summary_result` (locked output)

Identical structure regardless of whether input was JSON, OCR text, blob, or upload.

```json
{
  "confidence_score": 82.0,
  "document_type": "Invoice",
  "document_title": "Internet Service Invoice",
  "document_language": "English",
  "document_summary": "This is an invoice from <mark>Niss Internet Services</mark> for internet charges totaling <mark>1770.00 INR</mark>.",
  "key_facts_extracted": [
    "The invoice number is <mark>INV/26-27/002140</mark>.",
    "The vendor is Niss Internet Services.",
    "The total amount due is 1770.00 INR.",
    "GST of 270.00 INR is included in the total."
  ],
  "ocr_text": "{\"vendor\":\"Niss Internet Services\",\"invoice_no\":\"INV/26-27/002140\",\"total\":1770.0}",
  "source_reference": "summary_json"
}
```

## Fail-closed (empty source)

No LLM call when the chosen source has no usable content.

```json
{
  "session_id": "demo",
  "reply": "I couldn't extract any text from that document, so I can't summarize it.",
  "correlation_id": "…",
  "latency_ms": 12.5,
  "token_usage": null,
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
  "ocr_result": null,
  "insight_result": null,
  "ap_result": null
}
```

---



