# Summary Agent — End to End

How document Summary works on **`POST /chat` only** (there is no `/summary` URL).  
From request in → hallway → text source → LLM → locked `summary_result` out.

**Demo URLs (local)**

| What | URL |
|---|---|
| Test console | http://localhost:8010/console |
| Swagger | http://localhost:8010/docs |
| Health | http://localhost:8010/health |
| API | `POST` http://localhost:8010/chat |

---

## 1. What it does

The caller sends `intent=summary` plus **one** document source. The orchestrator turns that into a short status `reply` plus a **locked JSON** `summary_result`.

Three document sources (document jobs):

| Source | What happens |
|---|---|
| **Blob filepath** | Download from Azure Blob → Paddle `/api/extract_text` → LLM (`.docx` skips Paddle, see below) |
| **Multipart file** | PDF/image → Paddle; **`.docx` → local Word text extract** (no Paddle) → LLM |
| **`ocr_text`** | Skip blob + Paddle → LLM only |

Plus a **legacy** path: `summarize document DOC-123` (no file). EZOFIS `fetch_document` → prose `reply` only. **No** `summary_result`.

---

## 2. Input → output (full path)

```
Client
  POST /chat   (JSON or multipart)
        │
        ▼
  parse request  (ChatRequest + optional file bytes)
        │
        ▼
  Hallway (same as every /chat call)
    1. content filter
    2. rate limit
    3. load session history
    4. intent = "summary"  (explicit, or keyword "summarize …")
    5. permission check
        │
        ▼
  Build document_job?  (see §4 precedence)
        │
        ├─ YES (file / filepath / ocr_text)
        │     SummaryAgent._handle_document_job
        │           │
        │           ├─ ocr_text present?  → use it, skip blob + Paddle
        │           └─ else               → run_ocr
        │                 ├─ .docx → unzip Word XML, skip Paddle
        │                 ├─ .doc  → not supported (fail closed)
        │                 └─ pdf/image → Paddle extract_text
        │           │
        │           ├─ no text?  FAIL CLOSED (no LLM, empty summary_result)
        │           └─ text?     LLM  (model cascade §6)
        │                           → unwrap / lock JSON
        │                           → reply + summary_result
        │
        └─ NO (legacy keyword only)
              extract DOC-123 → fetch_document → prose reply
              summary_result = null
        │
        ▼
  ChatResponse envelope  (§8)
```

---

## 3. Request schema (`ChatRequest`)

Always the same `/chat` body. `message` is optional when `intent=summary` and a document source is present.

```json
{
  "session_id": "string (required)",
  "intent": "summary",
  "message": "optional free text",
  "instruction": "ignored for summary (OCR-only hint)",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "folder/file.pdf  OR  https://….blob.core.windows.net/…",
    "pageno": "1  |  -1  |  omit",
    "ocr_text": "pre-extracted text (optional)",
    "model": "optional LLM override, e.g. qwen3.5-9b",
    "parameters": [],
    "tableparameters": []
  }
}
```

| Field | Required? | Notes |
|---|---|---|
| `session_id` | yes | Session key for rate limit + history |
| `intent` | yes for document jobs | Must be `"summary"`. Unknown values → 400 |
| `message` | no if file / filepath / `ocr_text` | Used only for legacy `summarize DOC-123` |
| `payload.tenant_id` | yes for relative blob | Tenant UUID. Container is `ezts` + id without hyphens |
| `payload.filepath` | one of three sources | Folder+file inside that container, or a full blob URL |
| `payload.ocr_text` | one of three sources | Skips blob + Paddle. Wins over file/filepath |
| multipart `file` | one of three sources | Upload wins over filepath (loses to `ocr_text`) |
| `payload.pageno` | no | `"1"` = one page; `"-1"` = pages 1–`OCR_MAX_PAGES` (default 5). Ignored when `ocr_text` is used |
| `payload.model` | no | First hop in the 3-model cascade |
| `payload.parameters` | no | OCR-only; unused by Summary |
| `instruction` | no | OCR-only; unused by Summary |

**Multipart form fields** (same names, plus `file`):  
`session_id`, `intent`, `tenant_id`, `filepath`, `pageno`, `ocr_text`, `model`, `file`.

---

## 4. Which input wins (precedence)

Only **one** source is used:

```
1. payload.ocr_text   (non-empty)     → skip blob, skip Paddle
2. multipart file                     → Paddle on uploaded bytes
3. payload.filepath                   → Azure download + Paddle
4. none of the above                  → legacy "summarize DOC-123"
```

If `ocr_text` and a file/blob are both sent, **text wins**. No download, no OCR.

---

## 5. Scenarios — input and output

### A. Blob path (JSON)

**When:** Document lives in Azure Blob. Connection string from `.env` (`AZURE_STORAGE_CONNECTION_STRING`).

**Input**

```json
{
  "session_id": "demo",
  "intent": "summary",
    "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "INV26-27002140.pdf",
    "pageno": "1",
    "model": "qwen3.5-9b"
  }
}
```

**What runs:** blob download → Paddle `extract_text` → LLM.

**Output (success)**

- `reply`: `"Document summary generated successfully."`
- `document_id` / `summary_result.source_reference`: the filepath
- `summary_result.ocr_text`: Paddle extract (not the model’s rewrite)
- `summary_result`: locked fields (§7)
- `ocr_result`: `null`

---

### B. Multipart file upload

**When:** Caller has the PDF/image locally.

**Input**

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F pageno=1 \
  -F file=@invoice.pdf
```

**What runs:**
- **PDF / image / txt** → Paddle → LLM
- **`.docx`** → local text extract (zip + `word/document.xml`) → LLM. **No Paddle.**
- **`.doc`** (old Word) → not supported → fail closed

Filepath is ignored if both file and filepath are sent.

---

### B2. Word `.docx` upload

**Input**

```bash
curl -X POST http://localhost:8010/chat \
  -F session_id=demo \
  -F intent=summary \
  -F file=@policy.docx
```

Same as a PDF job, but text comes from the Word file itself. `document_id` / `source_reference` = `policy.docx`.

Legacy **`.doc`** is not supported. Use `.docx`, PDF, or paste `ocr_text`.

**Output (PDF or .docx):** same envelope as A.  
`document_id` / `source_reference` = upload filename (e.g. `invoice.pdf` or `policy.docx`).

---

### C. Direct OCR text (no blob, no Paddle)

**When:** Caller already has extracted text (or pasted it in the console).

**Input**

```json
{
  "session_id": "demo",
  "intent": "summary",
  "payload": {
    "ocr_text": "Niss Internet Services Private Limited\nInvoice Number: INV/26-27/002140\nTotal: 1770.00",
    "model": "qwen3.5-9b"
  }
}
```

Multipart equivalent: `-F ocr_text="…"` (still `intent=summary`).

**What runs:** LLM only. No Azure, no `OCR_EXTRACT_URL`.

**Output:** same envelope.  
`document_id` / `source_reference` = `"ocr_text"`.  
`summary_result.ocr_text` = the exact supplied text.

---

### D. Fail closed (no usable text)

**When:** Paddle returns empty / blob missing / extract throws. **Not** used when real `ocr_text` was supplied.

**Output** — still HTTP 200, still locked shape, **no LLM call**:

```json
{
  "reply": "I couldn't extract any text from that document, so I can't summarize it.",
  "summary_result": {
    "confidence_score": 0,
    "document_type": "",
    "document_title": "",
    "document_language": "",
    "document_summary": "I couldn't extract any text from that document, so I can't summarize it.",
    "key_facts_extracted": [],
    "ocr_text": "",
    "source_reference": "container/missing.pdf"
  },
  "ocr_result": null
}
```

---

### E. Legacy keyword (no file)

**Input**

```json
{ "session_id": "demo", "message": "summarize document DOC-123" }
```

`intent` omitted → keyword router picks Summary.

**What runs:** parse `DOC-123` → mocked EZOFIS `fetch_document` → prose LLM summary.

**Output**

- `reply`: free-text summary (not the status line)
- `document_id`: `"DOC-123"`
- `summary_result`: **`null`**
- `ocr_result`: `null`

This path is **not** the document-job contract. Use A/B/C for `summary_result`.

---

## 6. Model cascade (same 3-step as OCR)

Used only on document jobs that have text (A/B/C success).

| Order | Source | Typical value | Where it lives |
|---|---|---|---|
| 1 | `payload.model` | `qwen3.5-9b` | Request |
| 2 | Console / startup default | preset `ezofis-gpu-box` → `openai/qwen3.5-9b` | Redis + startup |
| 3 | Console fallback, else `OCR_FALLBACK_MODEL` | Azure `gpt-4.1-mini` | Redis / `.env` |

If step 1/2 throws, step 3 runs. If all fail → 502.

**Presets** (`app/llm/model_presets.py`) — UI picks an **id**, never a key:

| Preset id | Model string | Region / host |
|---|---|---|
| `ezofis-gpu-box` (default) | `openai/qwen3.5-9b` | Qwen ACI Canada Central |
| `gpt-4.1-nano` | `azure/gpt-4.1-nano` | Azure South India |
| `gpt-4.1-mini` | `azure/gpt-4.1-mini` | Azure South India |
| `gpt-4o-mini` | `azure/gpt-4o-mini` | Azure East US |

Keys stay in `.env` only: `QWEN_MAC_API_KEY`, `AZURE_SOUTH_INDIA_API_KEY`, `AZURE_EAST_US_API_KEY`, `AZURE_STORAGE_CONNECTION_STRING`. Paddle URL: `OCR_EXTRACT_URL`.

---

## 7. LLM output schema (locked)

The model is asked for **exactly** this JSON (no `ocr_text` from the model):

```json
{
  "confidence_score": 82.0,
  "document_type": "Invoice",
  "document_title": "Internet Service Invoice",
  "document_language": "English",
  "document_summary": "2–4 sentences naming the real document type, parties, purpose",
  "key_facts_extracted": ["short fact", "…"]
}
```

The agent then **locks** the object and injects:

| Field | Type | Rule |
|---|---|---|
| `confidence_score` | number 0–100 | From model; 0 on fail-closed |
| `document_type` | string | Short label from the text: Invoice, Insurance Policy, Letter, … |
| `document_title` | string | Short title supported by the text |
| `document_language` | string | Language of the OCR text (English, Arabic, …) |
| `document_summary` | string | Plain prose, **not** a JSON string. Names the **actual** type |
| `key_facts_extracted` | string[] | Type-specific facts only from OCR |
| `ocr_text` | string | Paddle extract **or** caller `ocr_text` — **never** the model’s copy |
| `source_reference` | string | filepath, filename, or `"ocr_text"` |

Removed (do not emit): `compliance_and_risk_assessment`, `ai_recommendations`, `supplier_trend_insight`.

JSON **keys never change** by document type. Only the **values** change.

Qwen sometimes wraps the object as a string inside `document_summary` and/or drops a final `}`. The agent unwraps that and brace-closes truncated JSON before locking.

---

## 8. `/chat` response envelope (`ChatResponse`)

Same envelope for every intent. Summary fills `reply`, `document_id`, `token_usage`, `summary_result`. Everything else is `null`.

```json
{
  "session_id": "demo",
  "reply": "Document summary generated successfully.",
  "correlation_id": "…",
  "latency_ms": 18113.94,
  "token_usage": {
    "prompt_tokens": 959,
    "completion_tokens": 605,
    "total_tokens": 1564
  },
  "document_id": "INV26-27002140.pdf",
  "chunk_ids": null,
  "cited_data_points": null,
  "ocr_result": null,
  "summary_result": { },
  "forecast_result": null,
  "invoice_reference": null,
  "mail_draft": null
}
```

| Envelope field | Summary document job | Legacy DOC-123 |
|---|---|---|
| `reply` | Status line (success or fail-closed) | Prose summary |
| `summary_result` | Locked object | `null` |
| `ocr_result` | always `null` | `null` |
| `document_id` | filepath / filename / `"ocr_text"` | `DOC-123` |

---

## 9. Content is type-dynamic (keys are not)

The model **infers type from the text first** (invoice, insurance policy/claim/certificate, PO, contract, letter, report, ID, receipt, other). It must **not** call the file an invoice unless the text supports that.

**Invoice file**

```json
{
  "document_type": "Invoice",
  "document_title": "Internet Service Invoice",
  "document_language": "English",
  "document_summary": "This is an invoice from Niss Internet Services to EZOFIS for internet charges.",
  "key_facts_extracted": [
    "Issuer: Niss Internet Services Private Limited",
    "Invoice Number: INV/26-27/002140",
    "Total Amount: 1770.00"
  ]
}
```

**Insurance file** (same keys)

```json
{
  "document_type": "Insurance Policy",
  "document_title": "Motor Insurance Policy",
  "document_language": "English",
  "document_summary": "This is a motor insurance policy issued by ABC General Insurance covering the insured vehicle.",
  "key_facts_extracted": [
    "Insurer: ABC General Insurance",
    "Policy Number: POL-77821",
    "Coverage: Own damage and third party"
  ]
}
```

---

## 10. Languages

No separate language flag or pipeline.

| Step | Behavior |
|---|---|
| Extract (Paddle or `.docx`) | Unicode as-is (Arabic, English, mixed, …) |
| Store | `ocr_text` keeps original script (`ensure_ascii=False`) |
| Analyze | LLM writes English structured fields; names/IDs/dates copied, not translated away |

Empty/garbage OCR → fail closed (scenario D).

---

## 11. Summary vs OCR (same hallway, different job)

| | Summary | OCR |
|---|---|---|
| URL | `POST /chat` | `POST /chat` |
| `intent` | `summary` | `ocr` |
| Sources | file (PDF/image/**docx**), filepath, or `ocr_text` | file or filepath (docx also extracts locally) |
| After text | LLM **summary** JSON | LLM **field** JSON (`ocrResult`) |
| Result field | `summary_result` | `ocr_result` |
| Other result | `ocr_result` = null | `summary_result` = null |
| `ocr_text` input | supported (skip Paddle) | not used |

---

## 12. Test console

http://localhost:8010/console  (Ctrl+F5 if the page looks stale)

- Default: chat box + **Send** only.
- Chip **Summarize document** → file (PDF, image, **.docx**), blob path, pageno, model, **OCR text**, then **Send**.
- Chip **OCR document** → OCR fields only (**.docx** also accepted; text extracted locally).
- `.docx` and pasted OCR text skip Paddle. PDF/image still run Paddle + LLM (~15–30s).
- Legacy `.doc` is not supported.

Swagger: http://localhost:8010/docs → `POST /chat` → examples `summary_blob` and `summary_ocr_text`.

---

## 13. Files

| Area | File |
|---|---|
| Route / document_job wiring | `app/main.py` |
| Agent (blob / upload / ocr_text / legacy) | `app/agents/summary_agent.py` |
| LLM prompt, lock, unwrap | `app/core/response_composer.py` |
| Request / response models | `app/models/chat.py`, `app/models/chat_request_parser.py` |
| Paddle + blob download | `app/integrations/ocr_engine.py`, `app/tools/run_ocr.py` |
| Word `.docx` text | `app/integrations/docx_text.py` |
| Model presets | `app/llm/model_presets.py` |
| Console | `app/static/console.html` |
| Tests | `tests/test_summary_endpoint.py` |

---

## 14. What we did not change

- No `/summary` URL — Summary is `/chat` only (same as OCR).
- OCR agent path and `ocr_result` shape are unchanged.
- Secrets stay in `.env`, not in the UI.
- Legacy `summarize DOC-123` still uses EZOFIS fetch + prose reply (no `summary_result`).
- JSON **keys** on `summary_result` stay fixed for every document type.
