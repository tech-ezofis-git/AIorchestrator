# OCR and AP agents — sample `curl`

Live endpoint: `POST https://cloud.ezofis.com/chat`

On Windows use `curl.exe` (not the PowerShell `curl` alias). Save JSON to a file and pass `--data-binary "@file.json"` so quotes survive.

```bash
# health
curl.exe -sS "https://cloud.ezofis.com/health"
```

Every `/chat` call needs a `session_id`. Set `intent` to `ocr` or `ap` (do not rely on keyword routing for document jobs).

---

## OCR agent

Needs a **file upload** or a **blob filepath**. `message` is optional.

### JSON — blob path

```bash
curl.exe -sS -X POST "https://cloud.ezofis.com/chat" ^
  -H "Content-Type: application/json" ^
  --data-binary "@ocr-blob.json"
```

`ocr-blob.json`:

```json
{
  "session_id": "ocr-demo-1",
  "intent": "ocr",
  "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "filepath": "ac40db26306b4d138aebf80a056d9a73/b4df8469e49743379c40609a5690053a.pdf",
    "pageno": "1",
    "parameters": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
    "tableparameters": []
  }
}
```

`pageno`: `"1"` (one page) or `"-1"` (up to max pages). Relative blob paths need `payload.tenant_id` (container `ezts{tenantid}`) and `AZURE_STORAGE_CONNECTION_STRING` on the agents app.

### Multipart — local file (wins over filepath)

```bash
curl.exe -sS -X POST "https://cloud.ezofis.com/chat" ^
  -F "session_id=ocr-demo-2" ^
  -F "intent=ocr" ^
  -F "pageno=1" ^
  -F "instruction=Region: India. Normalize DATE fields to YYYY-MM-DD." ^
  -F "parameters=Invoice No,SHORT_TEXT" ^
  -F "parameters=Due Date,DATE" ^
  -F "file=@invoice.pdf"
```

Success: HTTP 200 with `ocr_result` (extracted fields). Failure examples: `400` missing file/filepath, `502` extract engine error.

---

## AP agent

Set `intent` to `ap` plus one of: `invoice_json`, blob `filepath`, uploaded `file`, or `item_id` (re-run from stored artifacts). Optional `formid` (aliases `form_id` / `formId`) selects PO master table `ezfb_{token}_items` — numeric id, or the first 8 hex chars of a GUID (`29171de4-…` → `ezfb_29171de4_items`). The console AP panel has the same field.

`tenant_id` should be the full tenant UUID. The app keeps App Settings `DATABASE_URL` and opens database `ezofis_Tenant_{first 8}` (example: `2e3b7b37-38a3-4f94-878e-a006dad93230` → `ezofis_Tenant_2e3b7b37`).

If `skills` is omitted, the tenant default plan runs (Phase 1):

`extract_invoice` → `po_match` → `duplicate_detect` → `vendor_validate` → `backorder_detect` → `finalize_decision`

Each skill charges 1 credit (mocked if `EZOFIS_LOGIN_EMAIL` / `PASSWORD` are empty).

### JSON — pre-extracted invoice (skips OCR)

```bash
curl.exe -sS -X POST "https://cloud.ezofis.com/chat" ^
  -H "Content-Type: application/json" ^
  --data-binary "@ap-invoice.json"
```

`ap-invoice.json`:

```json
{
  "session_id": "ap-demo-1",
  "intent": "ap",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "formid": "29171de4-e210-466e-9e90-40fa9fa4354d",
    "item_id": "inv-100",
    "invoice_json": {
      "invoice_number": "INV-100",
      "vendor": "ACME Supplies",
      "po_number": "PO-1",
      "total": 1234.56,
      "currency": "USD",
      "line_items": [{"description": "Widget", "qty": 10, "amount": 1234.56}]
    }
  }
}
```

### JSON — blob + optional skill subset

Use `item_id` so later re-runs can reuse artifacts.

```json
{
  "session_id": "ap-demo-2",
  "intent": "ap",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "item_id": "doc-b4df8469",
    "filepath": "ac40db26306b4d138aebf80a056d9a73/b4df8469e49743379c40609a5690053a.pdf",
    "pageno": "1",
    "skills": ["extract_invoice", "po_match", "finalize_decision"]
  }
}
```

### JSON — re-run one skill from stored artifacts

```json
{
  "session_id": "ap-demo-3",
  "intent": "ap",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "item_id": "inv-100",
    "skills": ["vendor_validate"]
  }
}
```

### Multipart — local invoice file

```bash
curl.exe -sS -X POST "https://cloud.ezofis.com/chat" ^
  -F "session_id=ap-demo-4" ^
  -F "intent=ap" ^
  -F "tenant_id=2e3b7b37-38a3-4f94-878e-a006dad93230" ^
  -F "item_id=upload-inv-100" ^
  -F "pageno=1" ^
  -F "file=@invoice.pdf"
```

### Phase 2 (opt-in — not in the default plan)

Enable on the tenant plan or pass them in `skills`. Extra payload fields as needed:

| Skills | Extra fields |
|---|---|
| `po_lookup_quickbooks`, `po_lookup_sage` | `connector_id`, `resource` (`QUICKBOOKS` or `SAGE`) |
| `gl_match`, `grn_match`, `matter_validate` | `matter_master_id` for matter |
| `workflow_progress`, `workflow_move_next` | `workflow_id`, `instance_id` |

```json
{
  "session_id": "ap-demo-5",
  "intent": "ap",
  "payload": {
    "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
    "item_id": "inv-100",
    "invoice_json": {
      "invoice_number": "INV-100",
      "vendor": "ACME Supplies",
      "po_number": "PO-1",
      "total": 1234.56,
      "currency": "USD",
      "line_items": [{"description": "Widget", "qty": 10, "amount": 1234.56}]
    },
    "skills": ["extract_invoice", "po_match", "finalize_decision", "workflow_progress"],
    "workflow_id": "967f9423-ac93-4c70-93cb-df500f0d4cc9",
    "instance_id": "a96efa0d-28f1-4b48-afc2-c9791a346ce9"
  }
}
```

Success: HTTP 200 with `ap_result` (`run_id`, `skills_run`, `credits_charged`, `decision`, `artifacts`).

| Status | Meaning |
|---|---|
| 400 | Missing file / filepath / `invoice_json` / `item_id`, or skill not enabled |
| 503 | AP store unavailable (wrong DB or tables missing on `ezofis_Tenant_…`) |

---

## Bash (Git Bash / macOS / Linux)

Replace `^` line continuations with `\`:

```bash
curl -sS -X POST "https://cloud.ezofis.com/chat" \
  -H "Content-Type: application/json" \
  --data-binary @ap-invoice.json
```

Interactive UI: [https://cloud.ezofis.com/console](https://cloud.ezofis.com/console)
