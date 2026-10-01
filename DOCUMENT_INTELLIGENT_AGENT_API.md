# Document Intelligent Agent — API Request & Response Specification (Updated)

**Endpoint:** `POST /chat`  
**Intent:** `"document_intelligent"`  
**Local:** `http://localhost:8010` · Swagger `/docs` · Console `/console`

---

## 1. Overview

The Document Intelligent Agent reads a document and suggests which **existing tenant folder (repository)** it belongs in.

1. Loads the tenant's folders and their field names from the tenant database.
2. Extracts the document text (OCR), unless text is supplied directly.
3. Asks the model to pick the best folder from that list only.
4. Locks the result: unknown folders are dropped, every candidate gets a one-line rationale, and closest folders are suggested when the model returns too few.

The agent never invents a folder. Every `repository_id` / `repository_name` in the response exists in the tenant catalog.

---

## 2. Request structure

```json
{
  "session_id": "demo",
  "intent": "document_intelligent",
  "message": "optional note",
  "payload": {
    "tenant_id": "b843b988-00ec-44e3-aca2-b8470133ef63",
    "filepath": "folder/file.pdf",
    "pageno": "1"
  }
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `session_id` | **Yes** | Conversation / request session |
| `intent` | **Yes** | Must be `"document_intelligent"` |
| `message` | No | Optional note |
| `payload.tenant_id` | **Yes** | Tenant whose folders are matched |
| `payload.file` / `payload.filepath` / `payload.ocr_text` | **One of** | Document source (see §3) |
| `payload.pageno` | No | Single page `"1"`…`"5"`, or `"-1"` for pages 1–5. Default `"1"` |
| `payload.model` | No | Model override for this request |

**Headers**

| Transport | Header |
|-----------|--------|
| JSON | `Content-Type: application/json` |
| Multipart | `multipart/form-data` |

---

## 3. Supported input sources (precedence)

| Priority | Source | Use when | OCR runs |
|----------|--------|----------|----------|
| 1 | `ocr_text` | Text is already extracted | No |
| 2 | `file` (multipart) | Direct browser/client upload | Yes |
| 3 | `filepath` | Document in Azure Blob Storage | Yes |

**Relative blob path:** uses `tenant_id` → container `ezts{tenant_id}` (hyphens stripped).  
**Full blob URL:** may be passed as `filepath`.

---

## 4. Examples

### 4.1 Pre-extracted text (`ocr_text`)

```bash
curl -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "di-demo-001",
    "intent": "document_intelligent",
    "payload": {
      "tenant_id": "b843b988-00ec-44e3-aca2-b8470133ef63",
      "ocr_text": "VESSEL CALL ENQUIRY FORM ... IMO NUMBER 9648712 ... CARGO TYPE Bulk Steel"
    }
  }'
```

### 4.2 Azure Blob document (`filepath`)

```json
{
  "session_id": "di-demo-002",
  "intent": "document_intelligent",
  "payload": {
    "tenant_id": "b843b988-00ec-44e3-aca2-b8470133ef63",
    "filepath": "shipping/VESSEL_call_enquiry_form.pdf",
    "pageno": "1"
  }
}
```

### 4.3 File upload (multipart)

```bash
curl -X POST http://localhost:8010/chat \
  -F "session_id=di-demo-003" \
  -F "intent=document_intelligent" \
  -F "tenant_id=b843b988-00ec-44e3-aca2-b8470133ef63" \
  -F "pageno=1" \
  -F "file=@MJB-00483.pdf"
```

---

## 5. Response structure

```json
{
  "session_id": "di-demo-001",
  "reply": "Document repository inferred successfully.",
  "document_id": "VESSEL_call_enquiry_form_sample_HW-1.png",
  "token_usage": { "prompt_tokens": 1452, "completion_tokens": 253, "total_tokens": 1705 },
  "document_intelligent_result": {
    "repository_id": "E7596082-D04A-4233-A842-C4C6C3138B79",
    "repository_name": "Shipping Agency Files",
    "candidates": [
      {
        "repository_id": "E7596082-D04A-4233-A842-C4C6C3138B79",
        "repository_name": "Shipping Agency Files",
        "score": 85.0,
        "rationale": "This document has Vessel, Customer and IMO Number details that match Shipping Agency Files."
      },
      {
        "repository_id": "D049F1AA-C89D-4386-AEEC-1EE9DB870C02",
        "repository_name": "Trade Finance Documents",
        "score": 45.0,
        "rationale": "This document shares no specific details with Trade Finance Documents; it was suggested from its overall content."
      },
      {
        "repository_id": "9F522761-FF15-4F38-A32A-B6CC01B9690A",
        "repository_name": "FTL",
        "score": 30.0,
        "rationale": "This document has Date details that match FTL."
      }
    ],
    "ocr_text": "SHIPPER/CHARTERER/BROKER VESSEL NAME ...",
    "source_reference": "VESSEL_call_enquiry_form_sample_HW-1.png"
  }
}
```

### 5.1 `document_intelligent_result` keys

| Key | Type | Notes |
|-----|------|-------|
| `repository_id` | string \| null | The chosen folder. `null` when no folder is a clear fit |
| `repository_name` | string \| null | Name of the chosen folder |
| `candidates` | array | Up to 5 folders, best first (see §5.2) |
| `ocr_text` | string | Text the match was based on |
| `source_reference` | string | File name, blob path, or `ocr_text` |

There is **no** top-level `confidence_score` or `rationale`. Both live only inside each candidate.

### 5.2 Candidate keys

| Key | Type | Notes |
|-----|------|-------|
| `repository_id` | string | Folder id from the tenant catalog |
| `repository_name` | string | Folder name from the tenant catalog |
| `score` | number | 0–100 |
| `rationale` | string | One plain line on why this folder is suggested (see §6) |

---

## 6. Rationale

Built in code (not by the model), from the folder's field names that appear in the document text. At most 3 details are named, in the order they appear in the document.

| Situation | Rationale |
|-----------|-----------|
| Folder fields found in the text | `This document has Vessel, Customer and IMO Number details that match Shipping Agency Files.` |
| No folder field found | `This document shares no specific details with Trade Finance Documents; it was suggested from its overall content.` |

Field names are matched loosely: `IMO Number`, `IMONumber`, `imo_number` and `IMO-NUMBER` all match "IMO NUMBER" in the text.

---

## 7. Selection and suggestions

| Rule | Behaviour |
|------|-----------|
| Chosen folder | Set only when the model's confidence is **≥ 55** and the folder exists in the catalog |
| Model returns < 3 candidates | Code adds folders whose field names (or a folder-name word, e.g. "Invoices" ↔ "Invoice") appear in the text |
| Score of code-added folders | Share of the folder's fields found in the text, always **below 55** |
| Nothing in common with any folder | `candidates` is empty |
| Folder not in catalog | Dropped |

---

## 8. `reply` values

| Case | `reply` |
|------|---------|
| A folder was chosen, or candidates exist | `Document repository inferred successfully.` |
| Text found but no candidates at all | `This document doesn't match any of your folders.` |
| No readable text | `This document has no readable text, so it can't be matched to a folder.` |

---

## 9. Errors (HTTP 400)

| `detail` | Cause |
|----------|-------|
| `payload.tenant_id is required for intent=document_intelligent.` | `tenant_id` missing |
| `Could not load repositories for this tenant.` | Tenant database unreachable (e.g. server out of connections) or the folder query failed |
| `No repositories found for this tenant.` | Tenant has no active folders |

---

## 10. Where folders and fields come from

Read from the tenant database (connection string from `catalog."Tenants"` in the catalog DB):

| Data | Table |
|------|-------|
| Folders | `repository."Repositories"` (`IsDeleted = false`) |
| Field names | `repository."RepositoryFields"` (`Name`, `SqlColumnName`, `IsDeleted = false`) |

Older layouts (`repository."Fields"`, `dbo.wrepositoryfield`) are tried as fallbacks. Up to 80 folders and 24 fields per folder are used.

---

## 11. Changes in this update

| Change | Before | Now |
|--------|--------|-----|
| Field loading | Queried tables that don't exist; no fields loaded | Reads `repository."RepositoryFields"` |
| Rationale | One top-level, technical sentence | One plain line on **each** candidate |
| Top-level `confidence_score` | Returned | Removed; score is per candidate |
| Top-level `rationale` | Returned | Removed |
| No clear match | Often empty `candidates` | Closest folders still suggested |
| `reply` with suggestions only | "doesn't match any of your folders" | `Document repository inferred successfully.` |
| Console card | Overall confidence bar | Each candidate shows score and rationale; title "Suggested folders" when none is chosen |
