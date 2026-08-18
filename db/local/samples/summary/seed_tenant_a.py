#!/usr/bin/env python3
"""Seed tenant-a Summary extras from SKILL.md + rules/*.mdc sample files."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.agent_skills.loader import _parse_frontmatter  # noqa: E402
from app.tenant_skills.store import store_from_settings  # noqa: E402

_PACK = Path(__file__).resolve().parent / "tenant-a"
TENANT_ID = "tenant-a"

SAMPLES: tuple[tuple[str, Path, str], ...] = (
    ("custom1", _PACK / "SKILL.md", "skill"),
    ("custom1", _PACK / "rules" / "privacy.mdc", "rule"),
)


def _file_body(path: Path) -> str:
    _, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return body.strip()


def _upsert(store, slug: str, body: str, kind: str) -> None:
    if kind == "skill":
        existing = next(
            (r for r in store.list_custom_skills(tenant_id=TENANT_ID) if r["slug"] == slug),
            None,
        )
        if existing:
            store.update_custom_skill(
                item_id=existing["id"],
                tenant_id=TENANT_ID,
                body=body,
                is_active=True,
                changed_by="seed",
            )
            print(f"updated skill {slug} (id={existing['id']})")
        else:
            row = store.add_custom_skill(
                tenant_id=TENANT_ID,
                body=body,
                slug=slug,
                changed_by="seed",
            )
            print(f"created skill {slug} (id={row['id']})")
        return

    existing = next(
        (r for r in store.list_custom_rules(tenant_id=TENANT_ID) if r["slug"] == slug),
        None,
    )
    if existing:
        store.update_custom_rule(
            item_id=existing["id"],
            tenant_id=TENANT_ID,
            body=body,
            is_active=True,
            changed_by="seed",
        )
        print(f"updated rule {slug} (id={existing['id']})")
    else:
        row = store.add_custom_rule(
            tenant_id=TENANT_ID,
            body=body,
            slug=slug,
            changed_by="seed",
        )
        print(f"created rule {slug} (id={row['id']})")


def main() -> None:
    store = store_from_settings()
    for slug, path, kind in SAMPLES:
        if not path.is_file():
            raise SystemExit(f"missing sample file: {path}")
        _upsert(store, slug, _file_body(path), kind)
        print(f"  from {path.name} ({kind})")
    print(f"done — db: {store.db_path}")


if __name__ == "__main__":
    main()
