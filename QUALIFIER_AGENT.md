# FTL Qualifier

Decides whether an elevator-parts RFQ is in FTL's catalog: **qualify**, **disqualify**, or **needs_review**.

Text already in a PDF or DOCX is kept. Image pages, scans, and photo files are sent to Paddle OCR. The model then checks items against the Wittur pricelist.

## Input

**File upload** — `POST http://localhost:8010/chat`  
Body: `form-data`. Do not set a `Content-Type` header.

| Field | Required | Value |
|---|---|---|
| `session_id` | yes | any string, e.g. `test-1` |
| `intent` | yes | `ftl_qualifier` |
| `file` | yes | `.pdf`, `.docx`, `.eml`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.webp`, `.bmp` |

**Plain text** — `POST http://localhost:8010/api/ftl/qualify`  
Body: raw JSON.

```json
{
  "filename": "rfq.txt",
  "raw_text": "Modernization. Center opening door operator 42 inch. Governor and governor ropes."
}
```

`candidate_text` may be sent instead of `raw_text` when the text is already the shrunk spec excerpt.

## Output

```json
{
  "status": "success",
  "decision": {
    "qualify": "qualify",
    "project_type": "modernization",
    "matched_items": [
      {
        "item": "center opening door operator 42 inch",
        "category": "door_operator",
        "match": "exact",
        "catalog_ref": "HYDRA-PLUS-CO-42",
        "note": "Catalog hit"
      }
    ],
    "excluded_items": [
      { "item": "sliding guide", "reason": "Not in the Wittur pricelist" }
    ],
    "flags": [],
    "deadline": "2026-10-15",
    "project_name": "Bloor Street Modernization",
    "reasoning": "In-scope door equipment matches the catalog.",
    "confidence": 0.9
  },
  "run_id": 1,
  "run_record": {},
  "total_tokens": 1200
}
```

| Field | Values |
|---|---|
| `qualify` | `qualify`, `disqualify`, `needs_review` |
| `project_type` | `modernization`, `new_construction`, `unknown` |
| `match` | `exact`, `ambiguous` |
| `confidence` | `0` to `1` |

`run_record.result` is the same decision object. `run_record.candidate_text` is the text that was actually read from the file.

Chat returns the same decision inside `qualifier_result`, plus a markdown `reply`.
