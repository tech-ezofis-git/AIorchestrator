# Restore agent packs (pre Catalog+Tenant DB migration)

## Git tag

```powershell
cd D:\ezofis\v6\orchestrator
git checkout backup/agent-packs-pre-db-20260916-171225
```

Or reset files only to that tree without switching branch:

```powershell
git checkout backup/agent-packs-pre-db-20260916-171225 -- skills app/skills app/tenant_skills
```

## Zip archive

```powershell
Expand-Archive deploy\backups\agent-packs-20260916-171225.zip -DestinationPath deploy\backups\restore-tmp -Force
# Copy skills/, app/skills/, app/tenant_skills/ back to repo root as needed
```

## Notes

- Platform defaults originally lived on disk under `skills/{agent}/`.
- Tenant Summary extras lived in SQLite (`app/tenant_skills/data/tenant_skills.sqlite` when present).
- After migration, Catalog tables hold platform + tenant-scoped packs; disk remains seed/fallback when `AGENT_PACKS_FROM_DB` is off or Catalog is empty.
