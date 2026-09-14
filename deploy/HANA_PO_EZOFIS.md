# HANA Cloud Purchase Order — EZOFIS tenant (Orchestrator AP)

From `HANA_Cloud_Purchase_Order_API.docx`:

| Item | Value |
|------|--------|
| Tenant | `b843b988-00ec-44e3-aca2-b8470133ef63` |
| Connector | `f7636e21-1a0c-457c-a2b4-e28430705477` |
| Lookup | `POST /api/connector/{connectorId}/hana/purchase-orders` `{ "poNumber": "…" }` |
| Match | `POST /api/connector/{connectorId}/hana/purchase-orders/match` |

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
