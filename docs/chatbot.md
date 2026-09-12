# Chatbot agent (`intent=chatbot`)

Orchestrator Chatbot searches a tenant like Global Search (plus comments/tickets) and can propose confirm-gated Core actions. It does **not** replace the Global Search intent.

## Transport

```http
POST /chat
Content-Type: application/json

{
  "session_id": "…",
  "intent": "chatbot",
  "message": "INV-2026-6001",
  "payload": {
    "tenantId": "<guid>",
    "specificId": "<repository guid optional>",
    "recentHits": [],
    "uploadFile": null,
    "pendingActionId": null,
    "propose_action": null
  }
}
```

Response field: `chatbot_result` — `conversation`, `text.blocks`, `hits`, optional `action` / `pending_action`.

## What it does

| Capability | Notes |
| ---------- | ----- |
| Search | Reuses Global Search tools + `search_comments` / `search_tickets` |
| Intent gate | Greeting / help / out-of-scope without DB |
| Actions | Confirm before execute via `PendingActionStore` + `POST /actions/{id}/confirm` |
| Models | Shared `LLMAdapter`; catalog slug `chatbot` |

### Action tools

| Tool | Purpose |
| ---- | ------- |
| `chatbot_start_workflow` | Start workflow instance |
| `chatbot_upload_repository_file` | Upload file to repository |
| `chatbot_start_ticket_with_attachments` | Start ticket + optional attach |
| `chatbot_create_user` | Create user (email + display_name required) |

Propose via:

1. **NL** — e.g. “start that workflow”, “upload this to Accounts Payable repo”, “create user jane@acme.com Jane Doe”
2. **Structured** — `payload.propose_action: { "tool": "…", "arguments": {…} }`

Then confirm: Console **Confirm action** or `POST /actions/{action_id}/confirm?session_id=…`.

Passwords and `content_base64` are redacted in `pending_action` UI payloads.

## Guardrails

- Content filter + rate limit on `/chat` (same pipeline as other intents)
- Content filter on `/actions/{id}/confirm` `action_id`
- Out-of-scope chatter (weather, jokes, …) refused without search
- Create-user validation: email shape, display_name length, password ≥ 8 when provided
- Live Core calls need `EZOFIS_LOGIN_*`; otherwise tools return mock results

## Console

Use the **Chatbot** button: optional repository lock, optional file for upload/ticket, last hits are resent as `recentHits`.

---

See also: [chatbot smoke checklist](../deploy/CHATBOT_SMOKE.md).
