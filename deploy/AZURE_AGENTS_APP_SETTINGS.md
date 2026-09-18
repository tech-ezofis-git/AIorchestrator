# Azure agents — required App Settings for live AP

AP extraction runs without these, but **ezfb form rows stay empty** and metadata PATCH is skipped until login is configured.

Set on the **agents** container (App Service multi-container or `docker-compose.azure.yml` host `.env.azure`).

## Required for ezfb metadata + move-next

| App Setting | Example | Purpose |
|-------------|---------|---------|
| `EZOFIS_API_BASE` | `http://app/api` | Internal nginx → API. Do **not** use `https://cloud.ezofis.com/api` (hairpin) or `http://api:5000`. |
| `EZOFIS_LOGIN_EMAIL` | service account email | JWT for `X-Tenant-Id` calls |
| `EZOFIS_LOGIN_PASSWORD` | (secret) | Same |
| `EZOFIS_ENV` | `live` | Auth env (`trial` for demo stacks) |

## Required for AP pipeline (Catalog-driven — Phase 2)

| App Setting | Example | Purpose |
|-------------|---------|---------|
| `AP_PIPELINE_FROM_DB` | `true` | Runner reads `platform_ap_pipeline` / `tenant_ap_pipeline` for skill order, enable, thresholds, flags. Set `false` only to roll back to code defaults. |
| `DATABASE_URL` | Postgres host | Tenant AP tables in `ezofis_Tenant_{first8}` |
| `CATALOG_DATABASE_URL` | Catalog DB | Pipeline + agent packs + tenant connection strings |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob | Download for `filepath` OCR |
| `REDIS_URL` | `redis://redis:6379/0` | Sessions / rate limit |

Agents image default is `AP_PIPELINE_FROM_DB=true`; still set it explicitly on Azure so App Settings stay the source of truth.

See [docs/AP_PIPELINE_FROM_DB.md](../docs/AP_PIPELINE_FROM_DB.md).

## GHCR pull (agents/api/app images)

App Service pulls `ghcr.io/tech-ezofis-git/ezofis-app-v6/*:latest`. If `DOCKER_REGISTRY_SERVER_PASSWORD` is empty, new images fail to pull and agents often never start.

Set with **merge** (`az webapp config appsettings set`), never a partial ARM PUT:

| App Setting | Value |
|-------------|-------|
| `DOCKER_REGISTRY_SERVER_URL` | `https://ghcr.io` |
| `DOCKER_REGISTRY_SERVER_USERNAME` | GitHub org or user that can read the packages |
| `DOCKER_REGISTRY_SERVER_PASSWORD` | PAT with `read:packages` |

## After changing settings

1. Restart the **agents** container (not only API).
2. Re-run one AP invoice.
3. Check `ap_skill_artifacts.metadata_push` — should have `ezfbFieldsUpdated > 0`, not `login_not_configured`.

## docker-compose.azure.yml

Copy `/.env.azure.example` to `.env.azure` on the VM, fill secrets, then:

```bash
docker compose -f docker-compose.azure.yml up -d agents
```
