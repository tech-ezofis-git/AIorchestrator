# Report Agent — Dynamic Template Prompt Generation

## Goal

Implement the first phase of the Report Agent for **only the first 10 report templates**.

When the user clicks one of the 10 templates:

1. Read the selected template's title, description, domain, and template ID.
2. Do **not** use hardcoded database field names.
3. Ask the Report Agent to inspect the connected database metadata.
4. Discover relevant tables and actual database columns dynamically.
5. Generate a dynamic report prompt containing the discovered field names.
6. Return that prompt to the UI.
7. Do not generate the final Report Builder JSON in this phase.

The database schema must be the source of truth for field names.

---

# 1. Supported Templates

For this first implementation, support only these 10 templates:

| # | Title | Category / Domain | Description | Template ID |
|---|---|---|---|---|
| 1 | Accounts Payable Aging | Accounts Payable | Outstanding AP requests bucketed by age. | `tpl-accounts-payable-aging` |
| 2 | Pending Workflow Requests | Workflow Automation | Open workflow requests awaiting action. | `tpl-pending-workflow-requests` |
| 3 | Workflow SLA Compliance | Workflow Automation | Completed workflows within vs. outside SLA. | `tpl-workflow-sla-compliance` |
| 4 | Documents Without Recent Access | Document Management | Documents with no views in the selected period. | `tpl-documents-without-recent-access` |
| 5 | Document Retention Status | Document Management | Active, archived, and scheduled-deletion counts. | `tpl-document-retention-status` |
| 6 | Document Version Activity | Document Management | Version history and access counts by document. | `tpl-document-version-activity` |
| 7 | Portal Submission Performance | External Portal | Submission volume and completion rate trends. | `tpl-portal-submission-performance` |
| 8 | User Login and Security Activity | User Sessions & Security | Login activity, failures and anomalies. | `tpl-user-login-and-security-activity` |
| 9 | AI Credit Consumption | Report Agent Impact & ROI | Monthly AI credit usage vs. allocation. | `tpl-ai-credit-consumption` |
| 10 | Report Agent ROI | Report Agent Impact & ROI | Net business value and ROI% by report. | `tpl-report-agent-roi` |

Do not add additional templates in this phase.

---

# 2. Core Architecture

Use this flow:

```text
Template Gallery
      |
      | user clicks template
      v
Selected Template
      |
      | templateId + title + description + domain
      v
Report Agent
      |
      v
Database Metadata Discovery
      |
      | tables + columns + data types + relationships
      v
Relevant Field Selection
      |
      v
Dynamic Prompt Generator
      |
      v
Generated Prompt
      |
      v
UI displays prompt
```

Important:

```text
Template = business intent
Database = source of truth for fields
Report Agent = discovers and maps fields
```

Do NOT implement:

```text
Template -> hardcoded fields -> prompt
```

Implement:

```text
Template -> database discovery -> actual fields -> prompt
```

---

# 3. Template Configuration

Create or update the template configuration so that templates contain only business-level information.

Example:

```ts
export interface ReportAgentTemplate {
  id: string;
  title: string;
  domain: string;
  description: string;
}
```

Example:

```ts
{
  id: "tpl-pending-workflow-requests",
  title: "Pending Workflow Requests",
  domain: "Workflow Automation",
  description: "Open workflow requests awaiting action."
}
```

Do NOT add:

```ts
fields: [...]
```

to these template definitions.

Do NOT hardcode database column names.

---

# 4. Template Click Behavior

When a user clicks a template:

```text
onSelectTemplate(template)
```

should NOT directly create the final report definition.

Instead:

```text
onSelectTemplate(template)
        |
        v
generateDynamicReportPrompt(template)
```

Send:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "title": "Pending Workflow Requests",
  "domain": "Workflow Automation",
  "description": "Open workflow requests awaiting action."
}
```

---

# 5. Backend Report Agent Endpoint

Create a backend endpoint similar to:

```http
POST /api/report-agent/generate-prompt
```

Request:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "title": "Pending Workflow Requests",
  "domain": "Workflow Automation",
  "description": "Open workflow requests awaiting action."
}
```

Response:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "title": "Pending Workflow Requests",
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
    }
  ],
  "prompt": "..."
}
```

The exact table and column names must come from the connected database.

---

# 6. Database Metadata Discovery

The Report Agent must inspect database metadata before generating the prompt.

For PostgreSQL, use the database catalog / information schema.

Example:

```sql
SELECT
    table_schema,
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, ordinal_position;
```

Do not expose database credentials to the frontend.

The backend must perform database access.

---

# 7. Relevant Field Discovery

Do not simply return every database column.

The Report Agent should identify fields relevant to the selected template.

For example:

```text
Template:
Pending Workflow Requests
```

The agent should search for concepts such as:

```text
workflow
request
status
pending
stage
initiator
created
SLA
```

Then inspect matching tables and columns.

Possible database result:

```text
workflow_requests

request_id
workflow_name
created_by
current_status
current_step
created_at
sla_hours
```

These are examples only.

Never assume these columns exist.

---

# 8. Dynamic Prompt Generation

After database discovery, generate a prompt using:

```text
Template title
+
Template description
+
Domain
+
Discovered database tables
+
Discovered database columns
+
Column data types
+
Relevant relationships
+
Report requirements
```

Example generated prompt:

```text
You are a Report Agent.

Create a report for:

Title:
Pending Workflow Requests

Description:
Open workflow requests awaiting action.

Domain:
Workflow Automation

Database schema discovered:

Table:
workflow_requests

Fields:
- request_id (uuid)
- workflow_name (text)
- created_by (uuid)
- current_status (text)
- current_step (text)
- created_at (timestamp)
- sla_hours (integer)

Instructions:

1. Use the discovered database fields to determine the fields
   required for the report.
2. Identify the field representing the workflow/request status.
3. Identify the field representing the current workflow stage.
4. Identify the field representing the requester/initiator.
5. Identify the request creation date field.
6. Identify the SLA-related field if available.
7. Create a report showing open/pending workflow requests.
8. Use only fields that actually exist in the discovered schema.
9. Do not invent or assume database column names.
10. If a required field cannot be found, report it as missing.
11. Return the selected fields and their database mappings.
```

The actual prompt must use the real discovered schema.

---

# 9. Prompt Generation Rules for All 10 Templates

## 1. Accounts Payable Aging

Intent:

```text
Find outstanding accounts payable items and organize them
according to their age.
```

Search concepts:

```text
invoice
vendor
payable
amount
due
date
status
approval
purchase order
```

---

## 2. Pending Workflow Requests

Intent:

```text
Find workflow requests that are currently open/pending
and awaiting action.
```

Search concepts:

```text
workflow
request
status
pending
open
stage
initiator
created
SLA
```

---

## 3. Workflow SLA Compliance

Intent:

```text
Find completed workflows and determine whether they were
completed within or outside the SLA.
```

Search concepts:

```text
workflow
completed
completion
status
SLA
duration
started
ended
deadline
```

---

## 4. Documents Without Recent Access

Intent:

```text
Find documents that have not been viewed/accessed during
the selected period.
```

Search concepts:

```text
document
access
view
viewed
last_access
last_view
owner
repository
```

The selected period may later be supplied by the user/report configuration.

---

## 5. Document Retention Status

Intent:

```text
Show document counts by active, archived, and scheduled
deletion/retention state.
```

Search concepts:

```text
document
status
active
archived
retention
deletion
scheduled
expiry
```

---

## 6. Document Version Activity

Intent:

```text
Show document version history and activity/access information.
```

Search concepts:

```text
document
version
version_number
revision
access
view
created
updated
```

---

## 7. Portal Submission Performance

Intent:

```text
Show submission volume and completion trends for external
portal submissions.
```

Search concepts:

```text
portal
submission
submitted
completed
status
count
date
visitor
```

---

## 8. User Login and Security Activity

Intent:

```text
Show login activity, failed login attempts, sessions,
devices, IP information, and possible anomalies when
available in the database.
```

Search concepts:

```text
user
login
authentication
failed
success
session
IP
device
login_time
duration
```

---

## 9. AI Credit Consumption

Intent:

```text
Show AI credit consumption compared with allocated credits
over time.
```

Search concepts:

```text
AI
credit
usage
consumption
allocated
allocation
month
cost
usage_date
```

---

## 10. Report Agent ROI

Intent:

```text
Show business value and ROI percentage generated by reports
or the Report Agent.
```

Search concepts:

```text
report
AI
agent
value
net_value
ROI
cost
credits
revenue
benefit
month
```

These are semantic search concepts, NOT hardcoded database field names.

---

# 10. Missing Field Handling

If the database does not contain a field needed for the selected report:

DO NOT invent it.

Return:

```json
{
  "field": "ROI percentage",
  "status": "missing",
  "reason": "No database field or calculable source was found."
}
```

The prompt should clearly indicate missing information.

Example:

```text
The database does not contain a directly identifiable ROI field.
Do not invent one. Determine whether ROI can be calculated from
available value and cost fields. If it cannot, mark the field
as unavailable.
```

---

# 11. Database Safety

The Report Agent should initially perform metadata discovery only.

Do not allow the prompt-generation endpoint to execute arbitrary
user-provided SQL.

The agent should not modify data.

For this phase, database access should be:

```text
READ metadata
READ schema information
READ allowed metadata
```

No:

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

---

# 12. Frontend UI Behavior

When the user clicks a template:

Show a loading state:

```text
Discovering database fields...
```

Then show:

```text
Database Fields Discovered

Table: workflow_requests

✓ request_id
✓ workflow_name
✓ created_by
✓ current_status
✓ current_step
✓ created_at
✓ sla_hours
```

Then show:

```text
Generated Report Prompt
```

with the generated prompt.

Do not automatically generate the final report JSON yet.

---

# 13. Error Handling

Handle these cases:

### Template not supported

```json
{
  "error": "Unsupported report template"
}
```

### Database unavailable

```json
{
  "error": "Unable to access database metadata"
}
```

### No relevant tables found

```json
{
  "error": "No relevant database fields were found for this report template"
}
```

### Required field missing

Return the prompt with the missing field clearly identified.

---

# 14. Logging

Log:

```text
templateId
template title
database discovery duration
tables discovered
number of fields discovered
number of relevant fields
prompt generation duration
errors
```

Do NOT log:

```text
database password
connection strings containing credentials
API keys
access tokens
```

---

# 15. Implementation Order

Implement in exactly this order:

### Step 1
Locate the existing 10-template configuration.

### Step 2
Remove dependency on static/hardcoded report field names for these templates.

### Step 3
Create a backend database metadata discovery service.

Example:

```text
services/databaseMetadataService
```

### Step 4
Implement:

```text
getDatabaseSchema()
```

It should return:

```json
{
  "tables": [],
  "columns": [],
  "relationships": []
}
```

### Step 5
Create a Report Agent service.

Example:

```text
services/reportAgentService
```

### Step 6
Implement:

```text
findRelevantFields(template, databaseSchema)
```

### Step 7
Implement:

```text
generateDynamicPrompt(template, relevantFields)
```

### Step 8
Create:

```http
POST /api/report-agent/generate-prompt
```

### Step 9
Connect the template button's `onClick` to this endpoint.

### Step 10
Display the generated prompt in the Report Builder UI.

### Step 11
Test all 10 templates.

### Step 12
Only after this works, implement Phase 2:
dynamic prompt → final Report Builder JSON.

---

# 16. Expected End-to-End Example

User clicks:

```text
[ Pending Workflow Requests ]
```

Frontend sends:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "title": "Pending Workflow Requests",
  "domain": "Workflow Automation",
  "description": "Open workflow requests awaiting action."
}
```

Backend:

```text
1. Load template
2. Connect to database
3. Read database metadata
4. Find relevant workflow tables
5. Find relevant columns
6. Validate columns
7. Build dynamic prompt
8. Return prompt
```

Response:

```json
{
  "templateId": "tpl-pending-workflow-requests",
  "discoveredTables": [
    "workflow_requests"
  ],
  "discoveredFields": [
    "request_id",
    "workflow_name",
    "created_by",
    "current_status",
    "current_step",
    "created_at",
    "sla_hours"
  ],
  "prompt": "..."
}
```

Frontend displays the generated prompt.

---

# 17. Acceptance Criteria

The implementation is complete only when:

- [ ] Exactly 10 templates are supported.
- [ ] Template definitions do not hardcode database field names.
- [ ] Clicking a template triggers database schema discovery.
- [ ] Database field names come from the actual connected database.
- [ ] Relevant tables are identified dynamically.
- [ ] Relevant columns are identified dynamically.
- [ ] Column types are available to the agent.
- [ ] The generated prompt contains actual discovered database fields.
- [ ] The agent never invents missing database fields.
- [ ] Database credentials never reach the frontend.
- [ ] The endpoint is read-only for this phase.
- [ ] The generated prompt is displayed to the user.
- [ ] Final Report JSON generation is NOT part of Phase 1.
- [ ] All 10 templates are tested against the real database.

---

# 18. Important Constraint

Do not implement the old behavior:

```text
Click template
    ↓
Use static DOMAIN_FIELDS
    ↓
Build report JSON
```

Implement:

```text
Click template
    ↓
Title + description
    ↓
Database metadata discovery
    ↓
Dynamic relevant-field discovery
    ↓
Dynamic prompt generation
    ↓
Display prompt
```

The database is the source of truth for field names.

The 10 templates provide the business intent only.

The Report Agent is responsible for discovering and mapping the real database fields.
