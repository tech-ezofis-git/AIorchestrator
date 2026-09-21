# Phase 3 — Workflow PO Master is the only source of truth

**Exit:** Switching PoMaster in Workflow alone changes the AP validation path (no Agents tenant GUID / HANA connector hardcodes).

## Payload contract (Core → Agents)

| Workflow PoMaster | Start payload fields |
|-------------------|----------------------|
| **InternalForm** | `master_source=InternalForm`, `master_form_id=<guid>` (no `resource` / connector skills) |
| **SAP** | `master_source=SAP`, `resource=SAP`, `connector_id=<guid>`, `skills` include `po_lookup_sap` before `po_match` |
| **HANA** | `master_source=HANA`, `resource=HANA`, `connector_id=<guid>`, `skills` include `po_lookup_sap` before `po_match` |
| **QuickBooks** | `master_source=QuickBooks`, `resource=QUICKBOOKS`, `connector_id=<guid>`, `skills` include `po_lookup_quickbooks` before `po_match` |

Invoice `formId` remains the **write-back** form. PO master form is only `master_form_id`.

## Agents behavior

- `resolve_connector_id` returns payload/threshold only — **never** invents `HANA_PO_CONNECTOR_ID` from tenant.
- `ensure_ezofis_hana_po_lookup` injects only when Catalog `force_hana_po_lookup` is on **and** Workflow asks SAP/HANA (any tenant).
- `po_match`: InternalForm → `/masters/po` + ezfb; connector masters → connector artifacts only (no form fallback, no EZOFIS HANA safety net).

## Smoke matrix

| Case | Setup | Expect |
|------|--------|--------|
| InternalForm | Settings.PoMaster = InternalForm + master form id | `po_match` uses `master_form_id` / ezfb; no `po_lookup_sap` |
| SAP | PoMaster = SAP + connector | skills include `po_lookup_sap`; match from SAP sample/live |
| QB | PoMaster = QuickBooks + connector | `po_lookup_quickbooks` then match |
| HANA (optional) | PoMaster/resource = HANA + connector GUID | `po_lookup_sap` with HANA backend; move-next may post HANA match |

## Deploy note

Ship **Core** (enricher) and **Agents** together. After deploy, restart both; cancel stuck AP AGENT tickets; smoke one ticket per matrix row.
