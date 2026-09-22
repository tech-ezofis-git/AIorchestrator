# Report Builder — Templates & JSON Schema (for AI agent development)

This document describes the report-definition schema used by the app's Report
Builder and the "Suggested templates" that were added to the Report Builder
wizard, so an AI planning agent can generate a valid report-builder JSON from
a user's natural-language prompt.

Source of truth in code:
- Type definitions: `src/pages/report-builder/types.ts`
- Domain/field catalog: `src/pages/report-builder/constants.ts`
- Template catalog (new): `src/pages/report-builder/data/reportTemplates.ts`
- Template gallery UI (new): `src/pages/report-builder/components/steps/ReportTemplateGallery.tsx`
- Wiring (new): `src/pages/report-builder/components/steps/AskAiStep.tsx` (the "Build with AI" step of the Report Builder wizard, under Settings → Reports → Report Builder)

## 1. Suggested templates added

These 10 templates now render as a row of single-line pill buttons (icon +
title) inside the Report Builder wizard itself — on its first step ("Build
with AI", Settings → Reports → Report Builder → step 1), directly below the
AI prompt box (`AskAiStep` → `ReportTemplateGallery`). Clicking one seeds
the Report Builder draft (name, description, domain, fields, field
settings, filters) in place; the user then clicks "Continue" through the
wizard as usual.

| # | Title | Category / Domain | Description | Template id |
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

`Report Agent Impact & ROI` is a **new domain** added to `REPORT_DOMAINS` /
`DOMAIN_FIELDS` in `constants.ts` to support templates 9 and 10, since it
didn't previously exist in the report builder's domain catalog. The other 8
templates reuse the existing domains: `Accounts Payable`, `Document
Management`, `Workflow Automation`, `External Portal`, `User Sessions &
Security`.

Not yet built (out of scope for this pass, flagged for a follow-up if
wanted): the top-row entry cards from the reference image ("Ask the AI
Report Agent", "Duplicate an existing report", "Import a report definition")
— "Start from a template" is effectively the new gallery, and "Build
manually" already exists as a link inside the wizard.

## 2. Domain / field catalog

Every report is scoped to one **domain**, and each domain exposes a fixed
set of **fields** the report can select from (`DomainField[]`):

```ts
type ReportFieldType = 'Text' | 'Number' | 'Choice' | 'User' | 'Date'

interface DomainField {
  id: string          // stable field key, e.g. "invoiceNumber"
  label: string        // display label, e.g. "Invoice Number"
  type: ReportFieldType
}
```

Current domains and their fields:

- **Accounts Payable**: `invoiceNumber` (Text), `vendorName` (Text), `invoiceAmount` (Number), `invoiceStatus` (Choice), `approver` (User), `dueDate` (Date), `poNumber` (Text), `costCenter` (Choice)
- **Document Management**: `documentName` (Text), `repository` (Choice), `fileSize` (Number), `documentStatus` (Choice), `owner` (User), `uploadedDate` (Date), `version` (Text)
- **Workflow Automation**: `workflowName` (Text), `requestNumber` (Text), `initiator` (User), `currentStage` (Choice), `workflowStatus` (Choice), `slaHours` (Number), `startedDate` (Date)
- **External Portal**: `portalName` (Text), `visitorName` (Text), `submissionCount` (Number), `portalStatus` (Choice), `contactOwner` (User), `lastVisit` (Date)
- **User Sessions & Security**: `userName` (User), `ipAddress` (Text), `sessionDuration` (Number), `sessionStatus` (Choice), `device` (Choice), `loginTime` (Date)
- **Report Agent Impact & ROI** *(new)*: `reportName` (Text), `creditsUsed` (Number), `allocatedCredits` (Number), `netValue` (Number), `roiPercent` (Number), `month` (Text)

An agent picking a domain should only reference field ids from that domain's
list above (or, when `sourceType` is `Workflow`/`Folder`, from that source's
actual form fields — see §4).

## 3. Report definition JSON schema

This is the canonical shape a saved/edited report takes (`Report` in
`types.ts`), and what "Build manually" / "Import a report definition" should
produce or consume:

```ts
type ReportSourceType = 'Workflow' | 'Folder' | 'Master' | ''
type ReportStatus = 'Draft' | 'Published'
type ReportVisibility = 'Private' | 'Selected Users' | 'Selected Groups'
type ReportCalc = 'None' | 'Sum' | 'Average' | 'Count' | 'Min' | 'Max'
type ReportColumnType = 'value' | 'status'
type ReportFilterOperator =
  | 'equals' | 'notEquals' | 'contains'
  | 'greaterThan' | 'lessThan' | 'between'
  | 'isEmpty' | 'isNotEmpty'
type ReportScheduleFormat = 'PDF' | 'Excel' | 'CSV'
type ReportScheduleRecurrence = 'Daily' | 'Weekly' | 'Monthly'

interface ReportFilter {
  id: string
  field: string               // a DomainField.id
  operator: ReportFilterOperator
  value: string
}

interface ReportStatusRule {
  id: string
  label: string                // display label, e.g. "Overdue"
  match: string                 // exact value to match against the field
  color: string                 // e.g. "red" | "green" | "orange" | "gray" | "blue"
}

interface ReportFieldSetting {
  label: string
  width: number                 // column width in px, e.g. 160
  colType: ReportColumnType      // "value" | "status"
  calc: ReportCalc                // aggregation shown in the footer
  isCalculated?: boolean
  formulaTokens?: FormulaToken[]  // only when isCalculated is true
  statusBase?: string              // field id the status color is derived from
  statusDefault?: string           // fallback color, e.g. "gray"
  statusRules?: ReportStatusRule[] // only when colType === "status"
}

interface ReportSchedule {
  recurrence: ReportScheduleRecurrence
  day: string                    // e.g. "Monday" (used when recurrence = Weekly)
  time: string                    // "HH:mm", e.g. "09:00"
  timezone: string                 // e.g. "UTC" | "GST" | "IST" | "EST"
  format: ReportScheduleFormat
  recipients: string[]
  cc: string[]
  subject: string
  message: string
}

// The full report definition
interface Report {
  id: string
  name: string
  description: string
  domain: string                  // one of REPORT_DOMAINS, or a workflow/folder name
  sourceType: ReportSourceType     // "" for a freeform domain report
  sourceId?: string                  // workflow/folder id when sourceType is set
  sourceFormId?: string               // resolved form id for Workflow sources

  fields: string[]                    // selected DomainField ids, in column order
  fieldSettings: Record<string, ReportFieldSetting>
  customFields: Question[]             // ad-hoc computed/custom columns (form-builder Question shape)
  filters: ReportFilter[]

  scheduled: boolean
  schedule: ReportSchedule

  visibility: ReportVisibility
  sharedUsers: string[]
  sharedGroups: string[]

  status: ReportStatus
  owner: string
  runs: number
  createdAt: string                    // ISO datetime
  modified: string                      // ISO datetime
}
```

### Minimal "plan" shape for the AI agent to emit

For a planning agent that only needs to produce enough to seed the builder
(not the full persisted `Report`, which also needs server-assigned `id`,
`owner`, `createdAt`, etc.), emit this subset — it matches what
`buildFieldSettingsForTemplate` / the template `onSelectTemplate` handler
consumes:

```json
{
  "name": "Outstanding Invoices",
  "description": "Overview of outstanding vendor invoices and approval status.",
  "domain": "Accounts Payable",
  "sourceType": "",
  "fields": ["invoiceNumber", "vendorName", "invoiceAmount", "invoiceStatus", "dueDate"],
  "fieldSettings": {
    "invoiceNumber": { "label": "Invoice Number", "width": 160, "colType": "value", "calc": "None" },
    "vendorName": { "label": "Vendor Name", "width": 180, "colType": "value", "calc": "None" },
    "invoiceAmount": { "label": "Invoice Amount", "width": 160, "colType": "value", "calc": "Sum" },
    "invoiceStatus": {
      "label": "Status",
      "width": 160,
      "colType": "status",
      "calc": "None",
      "statusBase": "invoiceStatus",
      "statusDefault": "gray",
      "statusRules": [
        { "id": "r1", "label": "Overdue", "match": "Overdue", "color": "red" },
        { "id": "r2", "label": "Approved", "match": "Approved", "color": "green" },
        { "id": "r3", "label": "Pending", "match": "Pending Approval", "color": "orange" }
      ]
    },
    "dueDate": { "label": "Due Date", "width": 160, "colType": "value", "calc": "None" }
  },
  "filters": [
    { "id": "f1", "field": "invoiceStatus", "operator": "notEquals", "value": "Approved" }
  ],
  "scheduled": false,
  "schedule": {
    "recurrence": "Weekly",
    "day": "Monday",
    "time": "09:00",
    "timezone": "UTC",
    "format": "PDF",
    "recipients": [],
    "cc": [],
    "subject": "",
    "message": ""
  },
  "visibility": "Private",
  "sharedUsers": [],
  "sharedGroups": []
}
```

### Rules the agent should follow when generating a plan from a prompt

1. Pick exactly one `domain` from the catalog in §2 (or a real
   Workflow/Folder name when the prompt clearly names one — in that case set
   `sourceType` to `"Workflow"` or `"Folder"` and `sourceId` to its id).
2. Only reference field ids that exist for the chosen domain.
3. Every field in `fields` should have a matching entry in `fieldSettings`.
4. Use `colType: "status"` + `statusRules` only for `Choice` fields where the
   prompt implies color-coded states (e.g. "Approved/Overdue/Pending").
5. Use `calc` other than `"None"` only on `Number` fields, when the prompt
   implies aggregation ("total", "sum", "average", "count").
6. Build `filters` from explicit conditions in the prompt (e.g. "not yet
   approved" → `{ field: "invoiceStatus", operator: "notEquals", value: "Approved" }`).
7. Set `scheduled: true` and populate `schedule` only when the prompt asks
   for a recurring/emailed report.
