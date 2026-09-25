# FTL Agents: Qualifier and Quote Estimator

Two agents for elevator-parts RFQs, both reachable through the same `/chat` API.

1. **Qualifier** reads an RFQ and decides whether FTL can bid: `Qualify`, `Disqualify`, or `Needs Review`.
2. **Quote Estimator** prices the in-scope items against the Wittur pricelist and returns a Sales Estimate with a PDF.

Base URL (local): `http://localhost:8010`

## Key format

Every FTL data object uses Title Case keys with spaces: only the first letter of each word is a capital and there are no underscores (`Project Name`, `Matched Items`, `Unit Price`, `Bdm`, `Hst`, `Ai Insight`, `Pdf Base64`).

Short code values use the same format: `Needs Review`, `New Construction`, `Exact`, `Door Operator`. This covers `Qualify`, `Project Type`, `Match`, `Category` (qualifier items and estimator line items) and `Flags`. Free text (`Reasoning`, `Ai Insight`, `Description`, `Note`, `Remarks`, `Assumptions`), `Product Code`, `Estimate Number`, numbers and dates are returned as they are.

The `/chat` envelope keys are unchanged: `session_id`, `intent`, `payload`, `reply`, `qualifier_result`, `quote_result`, `estimate_number`, `pdf_base64`, `pdf_filename`.

Inputs are case-insensitive: `Project Name`, `project_name` and `projectName` are all accepted as keys, and `Needs Review` or `needs_review`, `Door Operator` or `door_operator` as values.

Use a new `session_id` for each fresh test run so an earlier session doesn't mix into the result.

## End-to-end flow

| Step | Call | Send | Get back |
|---|---|---|---|
| 1 | `POST /chat`, `intent=ftl_qualifier` | RFQ file or text | `qualifier_result` |
| 2 | `POST /chat`, `intent=ftl_quote_estimator` | `Qualifier Result` from step 1 | `quote_result` + `pdf_base64` |
| 3 | `POST /chat`, `intent=ftl_quote_estimator` | Edited `Quote Result` + `Template Type` | New `pdf_base64` (no model call) |
| 4 | `POST /api/ftl/base64-to-pdf` | `Pdf Base64` from step 2 or 3 | The PDF file |

---

## 1. Qualifier

### Input: file upload

`POST /chat`, body `form-data`. Do not set a `Content-Type` header yourself.

| Field | Required | Value |
|---|---|---|
| `session_id` | yes | any string, e.g. `test-coventry-1` |
| `intent` | yes | `ftl_qualifier` |
| `file` | yes | `.pdf`, `.docx`, `.eml`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.webp`, `.bmp` |

Text already inside a PDF or DOCX is used as is. Scanned pages and images go through OCR.

### Input: plain text

`POST /chat`, raw JSON:

```json
{
  "session_id": "test-coventry-1",
  "intent": "ftl_qualifier",
  "payload": {
    "Candidate Text": "285-295 Coventry modernization. Linear operator that integrates with Kone doors, gate switch or car door interlock assembly, clutch assembly, Wittur hall door interlock assembly."
  }
}
```

The standalone endpoint `POST /api/ftl/qualify` takes `{ "Filename": "rfq.txt", "Raw Text": "..." }` and returns the same object under `decision`, plus `run_id`, `run_record` and `total_tokens`.

### Output

```json
{
  "session_id": "test-coventry-1",
  "reply": "### ✅ QUALIFY\n\n**Project type:** modernization  ·  **Confidence:** 85% ...",
  "qualifier_result": {
    "Qualify": "Qualify",
    "Project Type": "Modernization",
    "Project Name": "285-295 Coventry - Modernization",
    "Deadline": null,
    "Matched Items": [
      {
        "Item": "linear operator that integrates with Kone doors",
        "Category": "Door Operator",
        "Match": "Ambiguous",
        "Note": "Wittur SGV2 door operators line supports various OEM adaptors but not explicitly Kone. Needs confirmation but likely compatible via adaptor."
      },
      {
        "Item": "clutch assembly",
        "Category": "Door Interlock / Clutch",
        "Match": "Exact",
        "Catalog Ref": "SGV2 clutch + car door lock assemblies, model-specific.",
        "Note": "FTL stocks clutches c/w car door interlock for Otis, Westinghouse, GAL etc."
      },
      {
        "Item": "wittur hall door interlock assembly",
        "Category": "Door Interlock / Clutch",
        "Match": "Exact",
        "Catalog Ref": "SGV2 clutch + car door lock assemblies including hall door interlock",
        "Note": "Wittur hall door interlock assemblies are supported and listed."
      }
    ],
    "Excluded Items": [],
    "Flags": [],
    "Reasoning": "The RFQ requests Wittur linear door operator integration with Kone doors (ambiguous but plausible with adaptors) and multiple Wittur clutch and interlock assemblies which are directly supported by FTL's Wittur SGV2 catalog.",
    "Confidence": 85,
    "Ai Insight": "This modernization opportunity fits well with FTL's product catalog. The main question is confirming compatibility of Kone doors with the Wittur linear operator. Recommend quoting with a clarification request on Kone integration."
  },
  "quote_result": null,
  "pdf_base64": null
}
```

| Field | Values |
|---|---|
| `Qualify` | `Qualify`, `Disqualify`, `Needs Review` |
| `Project Type` | `Modernization`, `New Construction`, `Unknown` |
| `Matched Items[].Match` | `Exact`, `Ambiguous` |
| `Matched Items[].Catalog Ref` | Only present when the model found a pricelist reference. |
| `Excluded Items[]` | `Item`, `Reason` |
| `Confidence` | `0` to `100` |
| `Ai Insight` | Short sales-facing summary: how attractive the opportunity is, the main risk, and the recommended next step. |

---

## 2. Quote Estimator

### Input: qualifier result (recommended)

Paste `qualifier_result` from step 1 into `Qualifier Result`, unchanged or edited. Only `Matched Items` are priced; anything in `Excluded Items` is never added as a line item. To hold back an uncertain item, move it to `Excluded Items` with a `Reason`.

`POST /chat`, raw JSON:

```json
{
  "session_id": "test-coventry-2",
  "intent": "ftl_quote_estimator",
  "payload": {
    "Template Type": "inflow",
    "Qualifier Result": {
      "Qualify": "Qualify",
      "Project Type": "Modernization",
      "Project Name": "285-295 Coventry - Modernization",
      "Deadline": null,
      "Matched Items": [
        {
          "Item": "linear operator that integrates with Kone doors",
          "Category": "Door Operator",
          "Match": "Ambiguous",
          "Note": "Likely compatible via adaptor; confirm."
        },
        {
          "Item": "clutch assembly",
          "Category": "Door Interlock / Clutch",
          "Match": "Exact",
          "Catalog Ref": "SGV2 clutch + car door lock assemblies, model-specific."
        },
        {
          "Item": "wittur hall door interlock assembly",
          "Category": "Door Interlock / Clutch",
          "Match": "Exact"
        }
      ],
      "Excluded Items": [],
      "Flags": [],
      "Reasoning": "Wittur door operator and clutch/interlock items match the SGV2 catalog.",
      "Confidence": 85,
      "Ai Insight": "Good modernization fit; confirm Kone compatibility."
    }
  }
}
```

`qualifier_result` and `template_type` also work as the payload field names. With `form-data`, send `session_id`, `intent`, `Template Type`, and `Qualifier Result` as a JSON string.

You can also skip the qualifier and send the RFQ directly: `POST /chat`, `form-data` with `session_id`, `intent=ftl_quote_estimator`, `file`, and optionally `Template Type`.

### Output

```json
{
  "session_id": "test-coventry-2",
  "reply": "### 📋 Sales Estimate: EST-900106 ...",
  "quote_result": {
    "Estimate Number": "EST-900106",
    "Project Name": "285-295 Coventry - Modernization",
    "Customer Name": "",
    "Contact Name": "",
    "Contact Phone": "",
    "Billing Address": "",
    "Shipping Address": "",
    "Bdm": "",
    "Payment Terms": "",
    "Line Items": [
      {
        "Product Code": "SGV2_DOOR_OP_1S42_RH",
        "Description": "SGV2 - 1/SPEED DOOR OPERATOR (42\") - Compatible with various OEMs, likely Kone via adaptor",
        "Category": "Door Operator",
        "Qty": 1.0,
        "Unit Price": 4955.28,
        "Subtotal": 4955.28,
        "Note": "Ambiguous compatibility with Kone - likely via adaptor; confirm before release",
        "Needs Engineering Review": true
      },
      {
        "Product Code": "SGV2_CLUTCH_OTIS_RH",
        "Description": "CLUTCH + CAR DOOR LOCK ASSEMBLY (RIGHT HAND) compatible with Wittur SGV2 operators",
        "Category": "Clutch",
        "Qty": 1.0,
        "Unit Price": 1010.4,
        "Subtotal": 1010.4,
        "Note": "",
        "Needs Engineering Review": false
      },
      {
        "Product Code": "SGV2_CLUTCH_HALL_DOOR_INTERLOCK",
        "Description": "Wittur Hall Door Interlock Assembly for SGV2 system",
        "Category": "Clutch",
        "Qty": 1.0,
        "Unit Price": 0.0,
        "Subtotal": 0.0,
        "Note": "Price included with clutch assembly; verify code before release",
        "Needs Engineering Review": true
      }
    ],
    "Freight Estimate": 975.0,
    "Freight Note": "Flat freight estimate of $975.00 (temporary default; confirm at order).",
    "Remarks": "Quote based on consultant specification review.",
    "Assumptions": [
      "Door operator compatibility with Kone doors is assumed via adaptor; confirm before release",
      "Door-programming tool (SGV2_DOOR_TOOLS) not included; verify before release"
    ],
    "Subtotal": 5965.68,
    "Freight": 0.0,
    "Hst": 902.29,
    "Total": 7842.97
  },
  "estimate_number": "EST-900106",
  "rendered_html": "<div ...>...</div>",
  "pdf_download_url": "/api/ftl/quote/pdf/EST-900106?template_type=inflow",
  "pdf_base64": "JVBERi0xLjQK...",
  "pdf_filename": "EST-900106.pdf"
}
```

| Field | Notes |
|---|---|
| `Line Items[].Category` | `Door Operator`, `Clutch`, `Panel Adaptor`, `Car Door Panel`, `Roller Guide`, `Governor`, `Car Safety`, `Door Protective Device`, `Door Tools`, `Other` |
| `Line Items[].Needs Engineering Review` | `true` when the line has an open question; flagged on the `internal_review` PDF. |
| `Freight Estimate` | The freight used in the totals. The separate `Freight` key is currently always `0`. |
| `Subtotal`, `Hst`, `Total` | Subtotal of all lines; HST is 13% of subtotal + freight; Total = subtotal + freight + HST. |

---

## 3. Quote JSON to PDF base64

Edit `quote_result` from step 2 (prices, quantities, remarks) and send it back as `Quote Result`. The model is not called. `Subtotal`, `Hst` and `Total` are recalculated from `Qty`, `Unit Price` and `Freight Estimate`, so you don't need to send them.

The same quote can be rendered in two PDF formats, chosen with `Template Type`.

| `Template Type` | Format | `pdf_filename` |
|---|---|---|
| `inflow` (default) | Customer-facing Sales Estimate: FTL logo, company address, 5-column line table (Product, Description, Quantity, Unit Price, Subtotal), navy totals box, standard delivery/exclusions/warranty terms. No internal notes. | `EST-900106.pdf` |
| `internal_review` (alias `internal`) | Internal checking copy: numbered line table with each line's `Note` and a red "⚠ needs engineering review" flag, `Freight Note` under the freight total, a Remarks box, and a yellow Internal Review Notes box listing the `Assumptions`. | `EST-900106_internal_review.pdf` |

### Sample A: customer PDF (`inflow`)

`POST /chat`, raw JSON:

```json
{
  "session_id": "test-coventry-pdf-inflow",
  "intent": "ftl_quote_estimator",
  "payload": {
    "Template Type": "inflow",
    "Quote Result": {
      "Estimate Number": "EST-900106",
      "Project Name": "285-295 Coventry - Modernization",
      "Customer Name": "ATTA Elevators",
      "Contact Name": "John Smith",
      "Contact Phone": "416-555-0100",
      "Billing Address": "100 King St W, Toronto, ON",
      "Shipping Address": "285 Coventry Rd, Ottawa, ON",
      "Bdm": "Seth",
      "Payment Terms": "Net 30",
      "Line Items": [
        {
          "Product Code": "SGV2_DOOR_OP_1S42_RH",
          "Description": "SGV2 - 1/SPEED DOOR OPERATOR (42\")",
          "Category": "Door Operator",
          "Qty": 1,
          "Unit Price": 4955.28
        },
        {
          "Product Code": "SGV2_CLUTCH_OTIS_RH",
          "Description": "CLUTCH + CAR DOOR LOCK ASSEMBLY (RIGHT HAND)",
          "Category": "Clutch",
          "Qty": 2,
          "Unit Price": 1010.4
        },
        {
          "Product Code": "SGV2_CLUTCH_HALL_DOOR_INTERLOCK",
          "Description": "Wittur Hall Door Interlock Assembly for SGV2 system",
          "Category": "Clutch",
          "Qty": 1,
          "Unit Price": 0.0
        }
      ],
      "Freight Estimate": 975.0
    }
  }
}
```

Output:

```json
{
  "session_id": "test-coventry-pdf-inflow",
  "reply": "PDF generated for EST-900106 (inflow).",
  "quote_result": {
    "Estimate Number": "EST-900106",
    "Project Name": "285-295 Coventry - Modernization",
    "Customer Name": "ATTA Elevators",
    "Line Items": [
      { "Product Code": "SGV2_DOOR_OP_1S42_RH", "Category": "Door Operator", "Qty": 1, "Unit Price": 4955.28, "Subtotal": 4955.28 },
      { "Product Code": "SGV2_CLUTCH_OTIS_RH", "Category": "Clutch", "Qty": 2, "Unit Price": 1010.4, "Subtotal": 2020.8 },
      { "Product Code": "SGV2_CLUTCH_HALL_DOOR_INTERLOCK", "Category": "Clutch", "Qty": 1, "Unit Price": 0.0, "Subtotal": 0.0 }
    ],
    "Freight Estimate": 975.0,
    "Subtotal": 6976.08,
    "Hst": 1033.64,
    "Total": 8984.72
  },
  "estimate_number": "EST-900106",
  "pdf_base64": "JVBERi0xLjQK...",
  "pdf_filename": "EST-900106.pdf"
}
```

### Sample B: internal review PDF (`internal_review`)

Same quote, plus the fields that only the internal format shows (`Note`, `Needs Engineering Review`, `Freight Note`, `Remarks`, `Assumptions`).

`POST /chat`, raw JSON:

```json
{
  "session_id": "test-coventry-pdf-internal",
  "intent": "ftl_quote_estimator",
  "payload": {
    "Template Type": "internal_review",
    "Quote Result": {
      "Estimate Number": "EST-900106",
      "Project Name": "285-295 Coventry - Modernization",
      "Customer Name": "ATTA Elevators",
      "Contact Name": "John Smith",
      "Contact Phone": "416-555-0100",
      "Billing Address": "100 King St W, Toronto, ON",
      "Shipping Address": "285 Coventry Rd, Ottawa, ON",
      "Bdm": "Seth",
      "Payment Terms": "Net 30",
      "Line Items": [
        {
          "Product Code": "SGV2_DOOR_OP_1S42_RH",
          "Description": "SGV2 - 1/SPEED DOOR OPERATOR (42\")",
          "Category": "Door Operator",
          "Qty": 1,
          "Unit Price": 4955.28,
          "Note": "Kone compatibility via adaptor; confirm before release",
          "Needs Engineering Review": true
        },
        {
          "Product Code": "SGV2_CLUTCH_OTIS_RH",
          "Description": "CLUTCH + CAR DOOR LOCK ASSEMBLY (RIGHT HAND)",
          "Category": "Clutch",
          "Qty": 2,
          "Unit Price": 1010.4,
          "Note": "",
          "Needs Engineering Review": false
        },
        {
          "Product Code": "SGV2_CLUTCH_HALL_DOOR_INTERLOCK",
          "Description": "Wittur Hall Door Interlock Assembly for SGV2 system",
          "Category": "Clutch",
          "Qty": 1,
          "Unit Price": 0.0,
          "Subtotal Override": 0,
          "Note": "Bundled with clutch; verify code before release",
          "Needs Engineering Review": true
        }
      ],
      "Freight Estimate": 975.0,
      "Freight Note": "Flat freight estimate of $975.00 (temporary default; confirm at order).",
      "Remarks": "Quote based on consultant specification review. Clutches and interlocks are exact SGV2 matches.",
      "Assumptions": [
        "Door operator compatibility with Kone doors is assumed via adaptor; confirm before release",
        "Door-programming tool (SGV2_DOOR_TOOLS) not included; verify before release",
        "No car door panel line included; confirm whether one is needed"
      ]
    }
  }
}
```

Output:

```json
{
  "session_id": "test-coventry-pdf-internal",
  "reply": "PDF generated for EST-900106 (internal_review).",
  "quote_result": {
    "Estimate Number": "EST-900106",
    "Line Items": [ "... same lines, with Note and Needs Engineering Review ..." ],
    "Freight Estimate": 975.0,
    "Freight Note": "Flat freight estimate of $975.00 (temporary default; confirm at order).",
    "Remarks": "Quote based on consultant specification review. Clutches and interlocks are exact SGV2 matches.",
    "Assumptions": [ "..." ],
    "Subtotal": 6976.08,
    "Hst": 1033.64,
    "Total": 8984.72
  },
  "estimate_number": "EST-900106",
  "pdf_base64": "JVBERi0xLjQK...",
  "pdf_filename": "EST-900106_internal_review.pdf"
}
```

### Fields that drive the PDF

| Field | Required | Notes |
|---|---|---|
| `Estimate Number` | recommended | Used for the PDF title and filename. Defaults to `ESTIMATE`. |
| `Line Items[].Product Code`, `Description` | yes | Shown on each line. |
| `Line Items[].Qty` | yes | Must be `Qty`, not `Quantity`. |
| `Line Items[].Unit Price` | yes | Line subtotal = `Qty × Unit Price`. |
| `Line Items[].Subtotal Override` | no | Forces a line subtotal, e.g. `0` for a bundled item. |
| `Freight Estimate` | no | Added before tax. |
| `Customer Name`, `Contact Name`, `Contact Phone`, `Billing Address`, `Shipping Address`, `Bdm`, `Payment Terms`, `Project Name` | no | Header fields. |
| `Line Items[].Note`, `Line Items[].Needs Engineering Review`, `Freight Note`, `Remarks`, `Assumptions` | no | Shown on the `internal_review` format only. |

---

## 4. Base64 to PDF

Turns the `pdf_base64` from step 2 or 3 into the PDF file. Works for both formats; the format is already inside the base64.

### Input

`POST /api/ftl/base64-to-pdf`, raw JSON:

```json
{
  "Pdf Base64": "JVBERi0xLjQKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYg...JSVFT0YK",
  "Filename": "EST-900106",
  "Download": true
}
```

For the internal copy, send the base64 from Sample B and e.g. `"Filename": "EST-900106_internal_review"`.

| Field | Required | Notes |
|---|---|---|
| `Pdf Base64` | yes | The full `pdf_base64` value (starts with `JVBERi0`). A `data:application/pdf;base64,` prefix is accepted. `pdf_base64` and `pdfBase64` also work. |
| `Filename` | no | Defaults to `quote.pdf`. `.pdf` is added if missing. |
| `Download` | no | `true` downloads as a file; `false` opens inline in the browser. |

### Output

The PDF file itself, not JSON:

| Header | Value |
|---|---|
| `Content-Type` | `application/pdf` |
| `Content-Disposition` | `attachment; filename="EST-900106.pdf"` when `Download` is `true`, `inline; filename="EST-900106.pdf"` when `false` |

Missing, truncated or non-PDF base64 returns `400`. In Postman, use **Send and Download** to save the file.

In a browser you can also skip this call and open the base64 directly:

```js
const bytes = Uint8Array.from(atob(pdf_base64), c => c.charCodeAt(0));
const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
window.open(url);
```

---

## Other endpoints

| Method and path | Input | Output |
|---|---|---|
| `POST /api/ftl/quote` | JSON or form-data: `file` / `Raw Text` / `Qualifier Result`, `Template Type` | `estimate_number`, `quote_result`, `rendered_html`, `pdf_download_url` |
| `POST /api/ftl/quote/pdf` | JSON: `Quote Result`, `Template Type` | PDF file directly (no base64) |
| `GET /api/ftl/quote/pdf/{estimate_number}?template_type=inflow` | none | PDF file for a saved estimate (same server only) |

## Common errors

- **`json_invalid` / "Expecting property name enclosed in double quotes"**: the body is not valid JSON. In Postman choose Body, raw, JSON, and use straight double quotes on every key and string.
- **Totals look wrong**: check that line items use `Qty`, not `Quantity`, and that freight is in `Freight Estimate`.
- **Estimator output doesn't match your qualifier result**: use a new `session_id` for each fresh test run.
- **Base64 to PDF returns 400**: the `Pdf Base64` value is incomplete; copy the whole string.
