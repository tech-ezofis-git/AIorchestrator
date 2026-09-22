# Report Agent UI: Separate Prompt and Plan Cards

## Objective

Make the Report Agent screen easy to read and use:

- Prompt and Report Plan must be two clearly separate boxes.
- They must never overlap, merge, or be clipped.
- The prompt is editable.
- The plan box visibly shows `Updating plan…` while the prompt is being edited and refreshed.

This document covers the UI only.

## Desktop layout

Use two equal cards with a 20px gap:

```text
REPORT AGENT OPTIONS

┌────────────────────────────── Prompt ──────────────────────────────┐  ┌─────────────────────────── Report plan ───────────────────────────┐
│ Schema & prompt active                                               │  │ Plan is up to date                       [ Refresh plan ]          │
├─────────────────────────────────────────────────────────────────────┤  ├───────────────────────────────────────────────────────────────────┤
│                                                                       │  │                                                                   │
│  Editable prompt textarea                                             │  │  Report title                                                     │
│                                                                       │  │  Description                                                      │
│                                                                       │  │  Filters · Grouping · Sort                                        │
│                                                                       │  │  SQL and data preview                                             │
│                                                                       │  │                                                                   │
└─────────────────────────────────────────────────────────────────────┘  └───────────────────────────────────────────────────────────────────┘
```

## HTML structure

```html
<section class="report-workspace" aria-label="Report prompt and plan">
  <article class="report-workspace-card report-prompt-card">
    <header class="report-workspace-header">
      <div>
        <h3>Prompt</h3>
        <p id="reportPromptStatus">Schema &amp; prompt active</p>
      </div>
    </header>
    <div class="report-workspace-content">
      <textarea id="reportPromptOverride"
        aria-label="Editable report prompt"
        placeholder="Generate a prompt or type report requirements here"></textarea>
    </div>
  </article>

  <article class="report-workspace-card report-plan-card">
    <header class="report-workspace-header">
      <div>
        <h3>Report plan</h3>
        <p id="reportPlanStatus" role="status">Plan is up to date</p>
      </div>
      <button type="button" class="btn-ghost" id="reportPlanRefreshBtn">
        Refresh plan
      </button>
    </header>
    <div class="report-workspace-content report-plan-content">
      <div id="reportPlanPreview" aria-live="polite">
        Generate a prompt to see the report plan.
      </div>
    </div>
  </article>
</section>
```

## CSS

```css
/* Do not put a fixed height or overflow:hidden on #reportPanel or its parent grid. */
#reportPanel .ocr-grid {
  display: block;
  max-height: none;
  overflow: visible;
}

.report-workspace {
  display: grid;
  grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
  gap: 20px;
  width: 100%;
  margin-top: 18px;
  align-items: stretch;
}

.report-workspace-card {
  min-width: 0;
  min-height: 520px;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow);
}

.report-workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 82px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line-strong);
  background: var(--gradient-soft);
}

.report-workspace-header h3 {
  margin: 0;
  font-family: var(--display);
  font-size: 16px;
  color: var(--ink);
}

.report-workspace-header p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--ink-3);
}

.report-workspace-content {
  padding: 16px;
}

#reportPromptOverride {
  display: block;
  width: 100%;
  min-height: 400px;
  padding: 14px;
  resize: vertical;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: #fff;
  color: var(--ink);
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.55;
}

.report-plan-content {
  min-height: 438px;
}

#reportPlanPreview {
  min-height: 400px;
  max-height: 620px;
  overflow: auto;
}

.report-plan-card.is-updating {
  border-color: var(--periwinkle);
}

.report-plan-card.is-updating #reportPlanPreview {
  opacity: 0.55;
}

@media (max-width: 1100px) {
  .report-workspace {
    grid-template-columns: 1fr;
  }

  .report-workspace-card {
    min-height: 440px;
  }
}

@media (max-width: 560px) {
  .report-workspace-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .report-workspace-header .btn-ghost {
    width: 100%;
  }
}
```

## UI interaction states

| State | Prompt card | Plan card |
| --- | --- | --- |
| Before generation | Empty textarea with helpful placeholder | “Generate a prompt to see the report plan.” |
| Prompt generated | Filled, editable textarea | Initial plan appears |
| User is typing | Normal editable textarea | Keep current plan visible |
| Updating | Normal editable textarea | Blue outline, “Updating plan…”, slightly dim current plan |
| Updated | Normal editable textarea | “Plan is up to date” and refreshed content |
| Error | Keep user text untouched | Show a concise error inside the plan card |

## UI acceptance checklist

- Prompt and plan have separate card borders and separate header bars.
- There is at least 20px space between cards on desktop.
- Both cards are at least 520px tall on desktop.
- At 1100px and below, cards stack vertically.
- The textarea has its own border and does not touch the plan card.
- The plan content scrolls inside its card instead of expanding under or over the prompt card.
- No parent element uses a fixed height that hides the lower part of either card.
