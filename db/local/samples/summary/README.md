# Summary tenant sample pack (tenant-a)

Same layout as the real Summary pack:

```
tenant-a/
  SKILL.md              ← sample tenant skill extra (→ custom1 in SQLite)
  rules/
    privacy.mdc         ← sample tenant rule extra (→ custom2 in SQLite)
```

Defaults under `skills/summary/` stay read-only. These files are **tenant extras** for local testing.

## Seed SQLite

```bash
python db/local/samples/summary/seed_tenant_a.py
```

Then in `/console` set `tenant_id` = **tenant-a** and run Summary.
