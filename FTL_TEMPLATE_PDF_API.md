# FTL Quote Estimator: Template + Form Data to PDF (base64)

Fill your own [pdfme](https://pdfme.com) template with estimate data and get the finished PDF back as base64. No AI model is called; the template is filled exactly as sent.

**Flow**

1. `POST /chat` with `formData` + `templateJson` returns `pdf_base64`.
2. (Optional) `POST /api/ftl/base64-to-pdf` with that `pdf_base64` returns the PDF file.

**Base URLs**

| Environment | URL |
|---|---|
| Local (Docker) | `http://localhost:8010` |
| Cloud | `https://cloud.ezofis.com` |

---

## Step 1: Generate the PDF as base64

`POST /chat`
Header: `Content-Type: application/json`

### Request body

```json
{
  "session_id": "ftl-template-pdf-001",
  "intent": "ftl_quote_estimator",
  "payload": {
    "formData": {
      "Invoice Type": "Quotation",
      "Order Number": "EST-261041",
      "Company Name": "ATTA Elevators",
      "Shipping Address": "3040 Wonderland Rd S, London, ON",
      "Line Item": [
        { "Product": "SGV2 Door Operator", "Qty": 1, "Price": "3,450.00", "Subtotal": "3,450.00" }
      ],
      "Subtotal": "3,450.00",
      "Freight Estimate": "0.00",
      "Total": "3,898.50"
    },
    "templateJson": {
      "basePdf": { "width": 210, "height": 297, "padding": [15, 15, 15, 15] },
      "schemas": [
        [
          { "name": "CompanyLogo", "type": "image", "dataKey": "company_logo",
            "position": { "x": 15, "y": 12 }, "width": 40, "height": 15,
            "content": "data:image/png;base64,iVBORw0KGgo..." },
          { "name": "Title", "type": "text", "readOnly": true,
            "position": { "x": 120, "y": 14 }, "width": 75, "height": 8,
            "content": "Estimate {{estimate_number}}", "fontSize": 14 },
          { "name": "CustomerName", "type": "text", "dataKey": "Company Name",
            "position": { "x": 15, "y": 35 }, "width": 90, "height": 6 },
          { "name": "LineItems", "type": "table", "dataKey": "Line Item",
            "position": { "x": 15, "y": 60 }, "width": 180, "height": 40,
            "head": ["Product", "Qty", "Price", "Subtotal"] },
          { "name": "Totals", "type": "container", "backgroundColor": "#F2F5F9",
            "position": { "x": 120, "y": 110 }, "width": 75, "height": 24,
            "children": [
              { "name": "Subtotal", "type": "key_value", "label": "Subtotal", "dataKey": "Subtotal" },
              { "name": "Freight", "type": "key_value", "label": "Freight", "dataKey": "Freight Estimate", "hideIfZero": true },
              { "name": "Total", "type": "key_value", "label": "Total", "dataKey": "Total" }
            ] }
        ]
      ]
    }
  }
}
```

A complete, ready-to-send body with a real embedded logo is in `ftl_template_test_request.json`.

### Request fields

| Field | Required | Notes |
|---|---|---|
| `session_id` | yes | Any string. Use a new one per test run. |
| `intent` | yes | Must be `"ftl_quote_estimator"`. |
| `payload.templateJson` | yes | pdfme template (`basePdf` + `schemas`). Accepted as a JSON object, a JSON string, a base64-encoded JSON string, or a `data:application/json;base64,...` URL. `template_json` also works. |
| `payload.formData` | yes | The estimate values. Any subset of fields. `form_data` also works. |

Multipart form-data also works: send `formData` as a JSON string and `templateJson` as a JSON or base64 string.

### How `formData` fills the template

- **Matching:** each template field with a `dataKey` takes the `formData` value with the same name. Case and spaces are ignored. Internal names work too: `estimate_number` = `Order Number`, `customer_name` = `Company Name`, `line_items` = `Line Item`.
- **Text and table fields** missing from `formData` print blank. The template's sample `content` is never printed for them.
- **Image fields** (e.g. the logo) keep the image embedded in the template (`data:image/png;base64,...` or plain base64), even if they have a `dataKey`. A `formData` value with the same name replaces it.
- **Fixed labels** (no `dataKey`, or `readOnly: true`) print as designed. `{{key}}` placeholders inside them are filled from `formData` (e.g. `{{estimate_number}}`). Placeholders with no value are removed.
- **Tables:** rows are matched to the table's `head` columns by name. Values print exactly as sent; totals are **not** recalculated.
- **`container` + `key_value`:** draws the container's `backgroundColor` box and stacks each `key_value` child as an equal-height row, with the `label` on the left and the `formData` value on the right. Rows support `textColor`, `fontSize`, `fontName` and `backgroundColor`. `hideIfZero: true` skips a row whose value is blank or 0.
- **Long tables:** on a single-page template, overflowing rows continue on extra pages. The header area and the footer (bottom 25 mm) repeat on every page, the block below the table (notes, totals) appears only on the last page, and `Page X of N` is added. Multi-page templates render each page as designed.
- **File name:** taken from `Order Number` (`EST-261041.pdf`). Defaults to `ESTIMATE.pdf`.

### Response (success)

```json
{
  "session_id": "ftl-template-pdf-001",
  "reply": "PDF generated for EST-261041 (1 page(s)).",
  "pdf_base64": "JVBERi0xLjQKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYg...JSVFT0YK",
  "pdf_filename": "EST-261041.pdf"
}
```

The full `/chat` response also contains the standard fields (`correlation_id`, `latency_ms`, and other agents' result keys set to `null`); only the ones above matter here.

| Field | Notes |
|---|---|
| `reply` | Short status message with the estimate number and page count. |
| `pdf_base64` | The finished PDF, base64-encoded (always starts with `JVBERi0`). |
| `pdf_filename` | Suggested file name, from `Order Number` / `estimate_number` in `formData`, else `ESTIMATE.pdf`. |

### Response (error)

A missing template or missing data still returns HTTP 200, with `pdf_base64: null` and the reason in `reply`:

```json
{
  "session_id": "ftl-template-pdf-001",
  "reply": "### ⚠️ Quote PDF Failed\n\ntemplateJson (a base64-encoded pdfme template) is required with formData.",
  "pdf_base64": null,
  "pdf_filename": null
}
```

| Error | Status | Cause |
|---|---|---|
| `templateJson ... is required with formData` | 200 | `formData` sent without a template. |
| `formData ... is required with templateJson` | 200 | Template sent without data. |
| `templateJson is not valid base64` / `templateJson does not decode to valid JSON` | 422 | The base64 string is incomplete or not JSON. Re-encode with `btoa(JSON.stringify(template))`. |

---

## Step 2 (optional): Convert base64 to the PDF file

`POST /api/ftl/base64-to-pdf`
Header: `Content-Type: application/json`

### Request body

```json
{
  "Pdf Base64": "JVBERi0xLjQKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYg...JSVFT0YK",
  "Filename": "EST-261041",
  "Download": true
}
```

| Field | Required | Notes |
|---|---|---|
| `Pdf Base64` | yes | The full `pdf_base64` from Step 1. A `data:application/pdf;base64,` prefix is accepted. `pdf_base64` and `pdfBase64` also work. |
| `Filename` | no | Defaults to `quote.pdf`; `.pdf` is added if missing. |
| `Download` | no | `true` downloads as a file, `false` opens inline in the browser. |

### Response

The PDF file itself (not JSON):

| Header | Value |
|---|---|
| `Content-Type` | `application/pdf` |
| `Content-Disposition` | `attachment; filename="EST-261041.pdf"` when `Download` is `true`, `inline; filename="EST-261041.pdf"` when `false` |

Missing, truncated or non-PDF base64 returns **400**. In Postman, use **Send and Download** to save the file.

In a browser you can skip Step 2 and open the base64 directly:

```js
const bytes = Uint8Array.from(atob(pdf_base64), c => c.charCodeAt(0));
window.open(URL.createObjectURL(new Blob([bytes], { type: "application/pdf" })));
```
