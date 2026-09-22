# Report Agent — Phase 2: Generate Report Plan Using Database Data

## Objective

Implement Phase 2 of the Report Agent.

Phase 1 already does:

```text
Template
  ↓
Database schema discovery
  ↓
Relevant database fields
  ↓
Dynamic report prompt
```

Phase 2 must take that dynamic prompt and:

```text
Dynamic Prompt
  ↓
Report Agent
  ↓
Understand report requirement
  ↓
Validate discovered database schema
  ↓
Inspect actual database data
  ↓
Create Report Plan
  ↓
Generate safe database query
  ↓
Execute query
  ↓
Validate returned data
  ↓
Return Report Plan + Report Data
```

The goal is NOT just to generate a report definition from the prompt.

The agent must use the **actual database schema and actual database data** to create a realistic report plan.

---

# 1. Scope

For this phase, continue supporting only the first 10 templates:

1. Accounts Payable Aging
2. Pending Workflow Requests
3. Workflow SLA Compliance
4. Documents Without Recent Access
5. Document Retention Status
6. Document Version Activity
7. Portal Submission Performance
8. User Login and Security Activity
9. AI Credit Consumption
10. Report Agent ROI

Do not add new templates in this phase.

---

# 2. End-to-End Flow

```text
User clicks template
        ↓
Phase 1
Dynamic database schema discovery
        ↓
Dynamic prompt
        ↓
Phase 2
Report Agent
        ↓
Schema validation
        ↓
Actual data inspection
        ↓
Field mapping
        ↓
Report planning
        ↓
Safe SQL generation
        ↓
SQL validation
        ↓
Execute query
        ↓
Validate result
        ↓
Report Plan + Data
        ↓
Report Builder
```

---

# 3. Input to Phase 2

Phase 2 receives the output from Phase 1.

Example:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "title": "Pending Workflow Requests",
  "domain": "Workflow Automation",
  "description": "Open workflow requests awaiting action.",
  "discoveredTables": [
    "workflow_requests"
  ],
  "discoveredFields": [
    {
      "table": "workflow_requests",
      "column": "request_id",
      "type": "uuid"
    },
    {
      "table": "workflow_requests",
      "column": "workflow_name",
      "type": "text"
    },
    {
      "table": "workflow_requests",
      "column": "current_status",
      "type": "text"
    }
  ],
  "prompt": "Create a report for Pending Workflow Requests..."
}
```

Do not trust the discovered fields blindly.

The Report Agent must validate them against the live database before querying data.

---

# 4. New API Endpoint

Create:

```http
POST /api/report-agent/generate-report-plan
```

Request:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "prompt": "Create a report for Pending Workflow Requests...",
  "discoveredSchema": {
    "tables": [],
    "fields": []
  }
}
```

Response:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "reportPlan": {},
  "query": {},
  "data": [],
  "validation": {}
}
```

---

# 5. Step 1 — Validate Database Schema

Before creating a plan:

```text
Prompt
  ↓
Discovered table
  ↓
Check actual database
  ↓
Check table exists
  ↓
Check columns exist
  ↓
Check column data types
```

If a discovered field no longer exists:

```json
{
  "field": "current_status",
  "status": "invalid"
}
```

The agent must not query invalid fields.

---

# 6. Step 2 — Inspect Actual Database Data

After schema validation, the agent should inspect a limited amount of real data.

Use safe read-only queries.

Example:

```sql
SELECT
    request_id,
    workflow_name,
    current_status,
    current_step,
    created_at,
    sla_hours
FROM workflow_requests
LIMIT 20;
```

The exact columns must come from the validated schema.

The purpose of this step is to understand:

- actual status values
- actual date values
- actual numeric values
- null frequency
- possible category values
- whether a field is actually useful
- whether the expected report can be generated

Do not retrieve unlimited rows.

---

# 7. Step 3 — Understand Actual Values

The agent should inspect representative values.

For a status field, determine values such as:

```text
Pending
Approved
Rejected
Completed
In Progress
```

These are examples only.

The agent must discover the real values from the database.

For numeric fields:

```text
minimum
maximum
average
count
```

For date fields:

```text
minimum date
maximum date
recent activity
```

Use aggregate queries where appropriate instead of loading large datasets.

---

# 8. Step 4 — Map Business Concepts to Database Fields

The Report Agent must create a mapping.

Example:

```json
{
  "businessConcept": "workflow status",
  "databaseField": {
    "table": "workflow_requests",
    "column": "current_status",
    "type": "text"
  },
  "confidence": 0.96
}
```

Another example:

```json
{
  "businessConcept": "request date",
  "databaseField": {
    "table": "workflow_requests",
    "column": "created_at",
    "type": "timestamp"
  },
  "confidence": 0.94
}
```

The agent must prefer exact semantic matches.

Do not select a field only because its name contains a matching word.

---

# 9. Step 5 — Create Report Plan

The Report Agent should produce a structured plan.

Recommended schema:

```json
{
  "title": "Pending Workflow Requests",
  "description": "Open workflow requests awaiting action.",
  "templateId": "tpl-pending-workflow-requests",

  "source": {
    "table": "workflow_requests"
  },

  "columns": [
    {
      "field": "request_id",
      "label": "Request ID",
      "type": "uuid"
    },
    {
      "field": "workflow_name",
      "label": "Workflow Name",
      "type": "text"
    },
    {
      "field": "current_status",
      "label": "Status",
      "type": "text"
    },
    {
      "field": "current_step",
      "label": "Current Stage",
      "type": "text"
    },
    {
      "field": "created_at",
      "label": "Created Date",
      "type": "timestamp"
    },
    {
      "field": "sla_hours",
      "label": "SLA Hours",
      "type": "number"
    }
  ],

  "filters": [],

  "calculations": [],

  "groupBy": [],

  "orderBy": [],

  "statusRules": []
}
```

The actual fields must come from the database.

---

# 10. Step 6 — Generate Database Query

After the plan is created, generate the SQL required to retrieve the report.

Example:

```sql
SELECT
    request_id,
    workflow_name,
    current_status,
    current_step,
    created_at,
    sla_hours
FROM workflow_requests
WHERE current_status IN ('Pending', 'In Progress')
ORDER BY created_at DESC;
```

Important:

`'Pending'` and `'In Progress'` are examples.

The agent must use actual values discovered from the database.

---

# 11. SQL Safety

The Report Agent must only generate read-only SQL.

Allowed:

```text
SELECT
WITH
GROUP BY
ORDER BY
WHERE
HAVING
JOIN
COUNT
SUM
AVG
MIN
MAX
CASE
```

Not allowed:

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
CREATE
GRANT
REVOKE
```

Reject any generated query containing data-modification statements.

---

# 12. SQL Validation Before Execution

Before executing the generated SQL:

1. Verify all tables exist.
2. Verify all columns exist.
3. Verify referenced aliases are valid.
4. Verify the query is read-only.
5. Apply a maximum row limit where appropriate.
6. Apply query timeout.
7. Prevent arbitrary schema access outside the permitted database scope.

Only execute after validation succeeds.

---

# 13. Step 7 — Execute Query

Execute the validated query using the backend database connection.

Return:

```json
{
  "rowCount": 25,
  "columns": [
    "request_id",
    "workflow_name",
    "current_status",
    "current_step",
    "created_at",
    "sla_hours"
  ],
  "rows": []
}
```

Do not send database credentials to the frontend.

---

# 14. Step 8 — Validate Returned Data

Compare the result with the Report Plan.

Check:

```text
Are all planned fields present?
Are returned values compatible with the expected types?
Did the filter work?
Are calculations valid?
Are grouping fields present?
```

Example:

```json
{
  "valid": true,
  "checks": {
    "columns": true,
    "types": true,
    "filters": true,
    "calculations": true
  }
}
```

If validation fails, the agent should revise the plan/query and retry within a strict limit.

Do not create infinite agent loops.

Recommended maximum:

```text
3 attempts
```

---

# 15. Template-Specific Planning Intent

The agent should use the selected template's business intent.

## Accounts Payable Aging

Determine:

```text
Outstanding AP records
Vendor
Invoice/reference
Amount
Due date
Status
Age
Aging bucket
```

The agent should dynamically identify the appropriate date and status fields.

If aging is required, calculate:

```text
current date - due date
```

Possible buckets:

```text
0-30
31-60
61-90
90+
```

Only use these buckets when appropriate to the report intent.

---

## Pending Workflow Requests

Determine:

```text
Request
Workflow
Initiator
Current stage
Status
Created date
SLA
```

Use actual status values from the database.

---

## Workflow SLA Compliance

Determine:

```text
Workflow
Start time
Completion time
SLA
Status
Duration
```

Determine whether completed workflows are:

```text
Within SLA
Outside SLA
```

based on actual database values and available dates.

---

## Documents Without Recent Access

Determine:

```text
Document
Last access/view date
Owner
Repository
Status
```

The report should identify documents that have not been accessed during the requested/defined period.

Do not invent a last-access field if it does not exist.

---

## Document Retention Status

Determine:

```text
Document
Retention status
Archive status
Deletion/scheduled deletion date
Expiry
```

Group/count by the actual status values available.

---

## Document Version Activity

Determine:

```text
Document
Version
Revision
Created/updated date
Access activity
Owner
```

Use actual version/access fields discovered in the database.

---

## Portal Submission Performance

Determine:

```text
Portal
Submission
Submission date
Completion status
Completion date
Visitor/user
```

Calculate volume and completion trends using actual data.

---

## User Login and Security Activity

Determine:

```text
User
Login time
Session duration
Login status
IP
Device
Failure/success information
```

Only include fields that actually exist.

---

## AI Credit Consumption

Determine:

```text
Report/Agent
Credits used
Allocated credits
Date/month
Cost if available
```

Calculate usage versus allocation when the required fields exist.

---

## Report Agent ROI

Determine:

```text
Report
Cost
Credits
Net value
Business value
Revenue/benefit
ROI
Date/month
```

If ROI is not directly stored but can be safely calculated from available fields, calculate it.

If required inputs are missing:

```text
Do not invent ROI.
Mark the calculation as unavailable.
```

---

# 16. Report Plan Output

The complete Phase 2 response should have this structure:

```json
{
  "templateId": "tpl-pending-workflow-requests",

  "reportPlan": {
    "title": "Pending Workflow Requests",
    "description": "Open workflow requests awaiting action.",

    "source": {
      "table": "workflow_requests"
    },

    "columns": [],
    "filters": [],
    "calculations": [],
    "groupBy": [],
    "orderBy": [],
    "statusRules": []
  },

  "databaseSchema": {
    "tables": [],
    "fields": []
  },

  "dataQuery": {
    "sql": "SELECT ...",
    "readOnly": true
  },

  "data": {
    "rowCount": 0,
    "columns": [],
    "rows": []
  },

  "validation": {
    "valid": true,
    "errors": [],
    "warnings": []
  }
}
```

---

# 17. Important Difference Between Prompt and Plan

Do not confuse these two stages.

## Phase 1

Produces:

```text
Dynamic Prompt
```

Example:

```text
Create a Pending Workflow Requests report using the following
database fields...
```

## Phase 2

Produces:

```text
Report Plan + Query + Data
```

Example:

```text
Source:
workflow_requests

Columns:
request_id
workflow_name
current_status
created_at

Filter:
current_status = Pending

Query:
SELECT ...

Data:
25 rows
```

---

# 18. Frontend Flow

After Phase 1 returns the dynamic prompt:

```text
Generated Prompt
      ↓
[Generate Report]
```

When the user clicks:

```text
Generate Report
```

call:

```http
POST /api/report-agent/generate-report-plan
```

Show:

```text
Analyzing database data...
```

Then:

```text
Report Plan Generated
```

Display:

```text
Report Title
Data Source
Selected Fields
Filters
Calculations
Grouping
Sorting
```

Then display the actual returned report data in a preview table.

---

# 19. Do Not Generate Final Saved Report Yet

Phase 2 should create:

```text
Report Plan
+
Database Query
+
Database Data
```

It should NOT yet persist the final report as a published report.

The next phase can convert the validated Report Plan into the existing Report Builder definition.

---

# 20. Suggested Service Structure

Use a structure similar to:

```text
report-agent/
│
├── templateService
│
├── databaseMetadataService
│
├── databaseDataService
│
├── reportAgentService
│
├── reportPlannerService
│
├── sqlGeneratorService
│
├── sqlValidatorService
│
└── reportValidationService
```

Responsibilities:

### databaseMetadataService

```text
Discover tables
Discover columns
Discover types
Discover relationships
```

### databaseDataService

```text
Execute approved read-only queries
```

### reportAgentService

```text
Understand report intent
Map business concepts to database fields
```

### reportPlannerService

```text
Create structured Report Plan
```

### sqlGeneratorService

```text
Convert Report Plan to SQL
```

### sqlValidatorService

```text
Validate SQL safety/schema
```

### reportValidationService

```text
Validate query results against plan
```

---

# 21. Implementation Order for Antigravity

Implement in this exact order:

### Step 1
Review the existing Phase 1 implementation.

Do not break the existing 10-template dynamic prompt generation.

### Step 2
Create the `generate-report-plan` backend endpoint.

### Step 3
Create schema validation.

### Step 4
Create safe database sample-data inspection.

### Step 5
Add actual-value analysis.

### Step 6
Create business-concept → database-field mapping.

### Step 7
Create the Report Plan schema.

### Step 8
Implement Report Plan generation.

### Step 9
Implement SQL generation.

### Step 10
Implement SQL safety validation.

### Step 11
Execute the validated query.

### Step 12
Validate returned data.

### Step 13
Return:

```text
Report Plan
+
SQL
+
Data
+
Validation
```

### Step 14
Add the `[Generate Report]` button to the UI.

### Step 15
Display the generated report preview.

### Step 16
Test all 10 templates.

---

# 22. Acceptance Criteria

Phase 2 is complete only when:

- [ ] The dynamic prompt from Phase 1 can be passed to Phase 2.
- [ ] Database schema is validated against the live database.
- [ ] Actual database data is inspected.
- [ ] Actual status/category/date values are discovered.
- [ ] Business concepts are mapped to real database fields.
- [ ] No database field names are invented.
- [ ] A structured Report Plan is generated.
- [ ] SQL is generated from the Report Plan.
- [ ] SQL is read-only.
- [ ] SQL is validated before execution.
- [ ] Query results are returned.
- [ ] Query results are validated against the Report Plan.
- [ ] The UI shows the Report Plan.
- [ ] The UI shows actual database results.
- [ ] All 10 templates work.
- [ ] Final report persistence/publishing is not part of this phase.
