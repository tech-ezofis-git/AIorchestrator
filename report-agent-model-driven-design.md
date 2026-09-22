# Model-Driven Report Agent

## Goal

Enhance the Report Agent so that a configured AI model can interpret a business request, predict the most relevant database tables and fields, create a grounded report prompt, and produce a report plan. The backend must remain responsible for schema access, query execution, and SQL safety.

## Current Capability

The existing Report Agent already performs these server-side steps:

1. Discovers live database tables and columns.
2. Ranks relevant tables and fields using report templates and rules.
3. Generates a schema-grounded prompt.
4. Builds a report plan and safe read-only SQL.
5. Executes the query and returns a report-data preview.

The catalog has a `report` agent entry and supports a per-tenant model assignment, but the Report Agent does not currently invoke that configured model. Its selection is deterministic.

## Target Workflow

```text
User business request
        |
        v
Discover permitted live schema
        |
        v
Model selects relevant tables, fields, filters, joins, and report intent
        |
        v
Validate every model selection against the discovered schema
        |
        v
Create report plan -> generate read-only SQL -> validate SQL
        |
        v
Execute query with row/time limits -> return report preview
```

## Model Input

The model should receive only the user request and approved schema metadata. Never send database credentials or unrestricted database contents.

Example prompt payload:

```json
{
  "request": "Show overdue supplier invoices by vendor, largest balance first.",
  "availableSchema": [
    {
      "table": "dbo.supplier_invoices",
      "columns": [
        {"name": "invoice_number", "type": "text"},
        {"name": "vendor_name", "type": "text"},
        {"name": "due_date", "type": "date"},
        {"name": "outstanding_amount", "type": "numeric"},
        {"name": "status", "type": "text"}
      ]
    }
  ],
  "instructions": [
    "Use only the tables and columns supplied.",
    "Return JSON only; do not write SQL.",
    "If the request cannot be supported, explain the missing business field."
  ]
}
```

## Required Model Output

The model should return a constrained JSON object. It predicts the report structure; it must not be trusted as an authority for database identifiers.

```json
{
  "title": "Overdue Supplier Invoices by Vendor",
  "description": "Open supplier invoices past their due date.",
  "source": {
    "table": "dbo.supplier_invoices"
  },
  "columns": [
    "invoice_number",
    "vendor_name",
    "due_date",
    "outstanding_amount",
    "status"
  ],
  "filters": [
    {"field": "due_date", "operator": "<", "value": "CURRENT_DATE"},
    {"field": "status", "operator": "IN", "value": ["Open", "Pending"]}
  ],
  "groupBy": ["vendor_name"],
  "orderBy": [
    {"field": "outstanding_amount", "direction": "DESC"}
  ],
  "calculations": [
    {"label": "Total Outstanding", "operation": "SUM", "field": "outstanding_amount"}
  ],
  "warnings": []
}
```

## Validation Rules

Before using model output, the backend must:

1. Verify each table and column exists in the live permitted schema.
2. Reject system/internal tables unless the report domain explicitly allows them.
3. Verify joins use known, allowed matching keys.
4. Allow only an approved set of filters, aggregations, sort directions, and operators.
5. Generate SQL in the backend; do not run SQL supplied by the model.
6. Require a read-only `SELECT` statement and enforce table, row, and query-time limits.
7. Return clear warnings when the requested information is unavailable.

## Integration Changes

### 1. Resolve the Report Agent model

When handling `POST /api/report-agent/generate-report-plan`, resolve the tenant's configured model using the existing catalog mapping for agent slug `report`. Fall back to the tenant default model when no report-specific mapping exists.

### 2. Add a model-planning service

Create a service such as `app/report_agent/model_planner.py` that:

- receives the business request and sanitized schema metadata;
- calls the resolved language model;
- parses and validates the structured JSON response;
- returns a normalized `ReportPlan` candidate or a structured unavailable-field result.

### 3. Keep deterministic planning as a fallback

If the model is unavailable, returns invalid JSON, or selects fields that do not exist, use the current rule-based planner. This maintains report availability and avoids a model outage breaking the feature.

### 4. Expand the API response

Add optional audit fields to the report-plan response:

```json
{
  "planningMode": "model",
  "model": "configured-model-name",
  "modelFallbackUsed": false,
  "selectionWarnings": []
}
```

## Recommended User Experience

1. User enters a plain-language report request.
2. The UI shows predicted tables and fields before querying data.
3. The user can adjust selected fields, filters, grouping, and sorting.
4. The backend validates the revised plan, returns a live preview, and then enables export or saving the report definition.

## Security Boundary

The AI model may recommend a report plan, but it must never receive credentials, execute SQL, or bypass tenant/database permissions. Schema validation and query generation remain exclusively on the backend.

## Acceptance Criteria

- A tenant can configure a model specifically for the `report` agent.
- A natural-language request produces a validated table/field prediction.
- The generated report plan uses only live, approved schema identifiers.
- Invalid model output is rejected or safely falls back to the deterministic planner.
- SQL is generated and validated server-side as read-only.
- The response returns a report preview plus clear warnings when required data is unavailable.
