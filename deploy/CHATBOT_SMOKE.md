# Chatbot smoke checklist (Phases 0–5)

Use Orchestrator Console (**Chatbot**) or `POST /chat` with `intent: "chatbot"`.  
Tenant id is required on every turn. Live Core actions need `EZOFIS_API_BASE` + `EZOFIS_LOGIN_*`; without login, confirm returns **mock** ids (still useful for pipe checks).

## 0. Contract

- [ ] Console shows **Chatbot** mode
- [ ] `POST /chat` without `tenantId` → 400 mentioning tenantId
- [ ] Catalog seed includes agent slug `chatbot`

## 1. Search + blocks

- [ ] Message `hello` → greeting, no search tools
- [ ] Message `help` → capabilities bullets (search + actions)
- [ ] Keyword / invoice / REQ → `chatbot_result.hits` + `text.blocks` cards
- [ ] Optional `specificId` locks one repository

## 2. Comments + tickets

- [ ] Query that matches a workflow comment → hit `type: comment`
- [ ] Query that matches request / ticket text → hit `type: ticket` with ids

## 3. Action foundation (structured propose)

```json
{
  "session_id": "smoke-cb-3",
  "intent": "chatbot",
  "payload": {
    "tenantId": "<tenant>",
    "propose_action": {
      "tool": "chatbot_start_workflow",
      "arguments": { "workflow_id": "<guid>" }
    }
  }
}
```

- [ ] Reply includes pending `action_id` / Confirm card
- [ ] Password on `chatbot_create_user` appears as `***` in `pending_action`
- [ ] `POST /actions/{id}/confirm?session_id=smoke-cb-3` → `status: executed` + result ids

## 4. NL workflow / upload / ticket

- [ ] Search once so Console keeps hits (or pass `recentHits`)
- [ ] “start that workflow” → propose `chatbot_start_workflow` with resolved `workflow_id`
- [ ] Attach a file → “upload this to that repo” → propose upload (bytes from `uploadFile`)
- [ ] “start ticket with attachments” → propose ticket tool; confirm → `instanceId`
- [ ] Confirm button in Console shows success links (`instanceId` / `itemId`)

## 5. Create user + harden

- [ ] “create user jane.smoke@example.com Jane Smoke” → propose `chatbot_create_user`
- [ ] Invalid email / short password → clear validation error, no pending action
- [ ] Confirm → `userId` (mock or live); password never echoed in UI
- [ ] “what's the weather today” → out_of_scope, no hits
- [ ] Injection phrasing on `/chat` → 400 content filter (no agent run)
- [ ] Rapid spam → 429 rate limit (same session)

## Closed

| Item | Status |
|------|--------|
| Intent + Console shell | Done |
| GS-parity search + comments/tickets | Done |
| Confirm-gated Core wrappers | Done |
| NL start / upload / ticket+attach | Done |
| NL create user + validation + docs | Done |
| Native SAP OAuth / replacing Global Search | Out of scope |
