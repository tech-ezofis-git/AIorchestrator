# HANA Cloud Purchase Order — EZOFIS tenant (Orchestrator AP)

From `HANA_Cloud_Purchase_Order_API.docx`:

| Item | Value |
|------|--------|
| Tenant | `b843b988-00ec-44e3-aca2-b8470133ef63` |
| Connector | `f7636e21-1a0c-457c-a2b4-e28430705477` |
| Lookup | `POST /api/connector/{connectorId}/hana/purchase-orders` `{ "poNumber": "…" }` |
| Match | `POST /api/connector/{connectorId}/hana/purchase-orders/match` (full invoice + PO line payload) |

### Match request body (orchestrator → Core)

After **Matched** / **Partially Matched**, `workflow_move_next` sends:

```json
{
  "instanceId": "<workflow instance guid>",
  "poNumber": "4500069456",
  "invoiceNumber": "56700989",
  "supplierName": "EV Parts Inc.",
  "invoiceDate": "2026-09-11",
  "currency": "USD",
  "totalAmount": 368.94,
  "status": "Matched",
  "invoiceStatus": "Open",
  "items": [
    {
      "itemNumber": 10,
      "itemCategory": "Standard",
      "materialId": "MZ-RM-R100-02",
      "materialDescription": "BKR-100 Handle Bars",
      "materialGroup": "ZHANDLE",
      "plant": "1710",
      "orderQuantity": 129,
      "unitOfMeasure": "PC",
      "netPrice": 2.86,
      "priceUnit": 1,
      "netValue": 368.94
    }
  ]
}
```

`items` come from HANA lookup `match_items` (passthrough from Core lookup response). Invoice header fields come from extracted invoice JSON; `invoiceStatus` defaults to `Open`.

## Orchestrator AP

- Skill: `po_lookup_sap` (routes to HANA when connector is the GUID above, resource contains `HANA`, or EZOFIS tenant with no connector id).
- After `workflow_move_next` on **Matched** / **Partially Matched**, saves `PO_INVOICE_MATCH` via Core match API.

### Console / payload

```json
{
  "intent": "ap",
  "payload": {
    "tenantId": "b843b988-00ec-44e3-aca2-b8470133ef63",
    "resource": "HANA",
    "connector_id": "f7636e21-1a0c-457c-a2b4-e28430705477",
    "skills": ["extract_invoice", "po_lookup_sap", "po_match", "finalize_decision", "workflow_move_next"],
    "instance_id": "<workflow instance guid>",
    "invoice_json": {
      "po_number": "4500069456",
      "vendor": "EV Parts Inc.",
      "total": 368.94,
      "invoice_number": "INV-1001"
    }
  }
}
```

Sample PO in doc: `4500069456` (EV Parts Inc., total 368.94 USD).

Legacy SAP sample connector (`983bddbe-…`, `PO-60001`) still uses `/sap/purchase-orders/lookup`.
