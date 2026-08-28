# Azure agents — required App Settings for live AP

AP extraction runs without these, but **ezfb form rows stay empty** and metadata PATCH is skipped until login is configured.

Set on the **agents** container (App Service multi-container or `docker-compose.azure.yml` host `.env.azure`).

## Required for ezfb metadata + move-next

| App Setting | Example | Purpose |
|-------------|---------|---------|
| `EZOFIS_API_BASE` | `https://cloud.ezofis.com/api` | V6 API base for metadata / move-next / credits |
| `EZOFIS_LOGIN_EMAIL` | service account email | JWT for `X-Tenant-Id` calls |
| `EZOFIS_LOGIN_PASSWORD` | (secret) | Same |
| `EZOFIS_ENV` | `live` | Auth env (`trial` for demo stacks) |

## Required for AP pipeline (already needed on live)

| App Setting | Purpose |
|-------------|---------|
| `DATABASE_URL` | Postgres host; tenant AP tables in `ezofis_Tenant_{first8}` |
| `CATALOG_DATABASE_URL` | Catalog DB (tenant connection strings, models) |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob download for `filepath` OCR |
| `REDIS_URL` | Sessions / rate limit (`redis://redis:6379/0` in compose) |

## After changing settings

1. Restart the **agents** container (not only API).
2. Re-run one AP invoice.
3. Check `ap_skill_artifacts.metadata_push` — should have `ezfbFieldsUpdated > 0`, not `login_not_configured`.

## docker-compose.azure.yml

Copy `/.env.azure.example` to `.env.azure` on the VM, fill secrets, then:

```bash
docker compose -f docker-compose.azure.yml up -d agents
```
