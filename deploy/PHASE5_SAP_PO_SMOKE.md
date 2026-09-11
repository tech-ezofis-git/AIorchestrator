# Phase 5 — SAP PO smoke checklist (tenant EZOFIS)

Tenant: `b843b988-00ec-44e3-aca2-b8470133ef63`  
SAP connector: `983bddbe-6a1a-4cd8-a024-9b4d84ba9981` (`SAP_XSUAA`, mode=sample)  
Sample PO: `PO-60001` → ACME Supplies / 1500.00 USD

## Deploy (both apps)

- [ ] Deploy **v641Api** (Phases 2–4 + gap closure: SAP lookup, master resolve, PoMaster JSON, start enricher)
- [ ] Deploy **orchestrator** (Phases 3–5: `po_lookup_sap`, finalize `sap_sample`, console connector field)
- [ ] Confirm catalog has `ConnectorProviders` row for `SAP`
- [ ] App Settings: Core `ApAgent:PythonServiceUrl` → Agents `/chat`; Agents `EZOFIS_API_BASE` → Core `/api`

## 1. Connector row

- [ ] `GET /api/connector` shows `983bddbe-…`
- [ ] `providerCode` is `SAP` / `SAP_XSUAA` / `SAP_*`
- [ ] `configJson.mode` = `sample`, `samplePurchaseOrders` includes `PO-60001`
- [ ] Optional: `sampleVendors` present (or vendors derived from POs)

## 2. Lookup API (Core)

```http
POST /api/connector/983bddbe-6a1a-4cd8-a024-9b4d84ba9981/sap/purchase-orders/lookup
{ "poNumber": "PO-60001" }
```

- [ ] Hit → `found: true`, `source: "sap_sample"`
- [ ] Miss → `found: false` + `reason` (no invented PO)
- [ ] Non-SAP connector → 400
- [ ] Optional live gateway: set `ConfigJson.live.purchaseOrderLookupUrl` = `https://…/po/{poNumber}`

## 3. Vendor master resolve (SAP)

```http
GET /api/master/resolve?type=Vendor&source=SAP&connectorId=983bddbe-6a1a-4cd8-a024-9b4d84ba9981&q=ACME
```

- [ ] Returns ACME Supplies (from `sampleVendors` or PO vendors)

## 4. AP `/chat`

- Resource `SAP`, connector `983bddbe-…`, skills include `po_lookup_sap` before `po_match`, `po_number=PO-60001`
- [ ] `po_match.decision` = `MATCHED`, score high when vendor/total align
- [ ] Progress: **Looking up SAP purchase order**

## 5. Non-email PO Master

```json
{
  "masterSource": "SAP",
  "masterConnectorId": "983bddbe-6a1a-4cd8-a024-9b4d84ba9981",
  "workflowJson": {
    "Settings": {
      "General": { "InitiateUsing": { "Type": "DOCUMENT_FORM", "FormId": "…" } },
      "PoMaster": { "masterSource": "SAP", "masterConnectorId": "983bddbe-…" }
    }
  }
}
```

- [ ] Start FormData includes `resource` / `connector_id` / `po_lookup_sap` without email mailbox

## 6. Hangfire email path

- [ ] Mailbox `masterSource=SAP` + `masterConnectorId`
- [ ] Same startPayload enrichment as above

## Closed vs deferred

| Item | Status |
|------|--------|
| Sample PO lookup + AP skill | Done |
| SAP vendor master resolve (sample) | Done |
| Non-email `Settings.PoMaster` | Done |
| Live gateway URL (`ConfigJson.live.purchaseOrderLookupUrl`) | Done (customer/iPaaS) |
| Native SAP OData/BAPI/XSUAA client | **Deferred** — needs SAP landscape + credentials |
| Deploy Core + Agents | Ops checklist above |
