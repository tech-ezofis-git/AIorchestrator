"""SQLite store for tenant skill/rule extras + append-only change log.

Default SKILL.md / rules stay on disk. Tenants ADD / UPDATE / DISABLE custom
.md skills (tenant_skills) and .mdc rules (tenant_rules); active extras merge
into the Summary LLM prompt.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_APP_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _APP_DIR / "schema.sql"
_DEFAULT_DB_PATH = _APP_DIR / "data" / "tenant_skills.sqlite"
_AGENT = "summary"
_MAX_BODY_CHARS = 64 * 1024
_CUSTOM_SLUG_RE = re.compile(r"^custom(\d+)$")
_TABLE_SKILL = "tenant_skills"
_TABLE_RULE = "tenant_rules"

SUMMARY_RULE_FILES: tuple[tuple[str, str], ...] = (
    ("default1", "output-contract.mdc"),
    ("default2", "highlights.mdc"),
    ("default3", "narrative.mdc"),
)


def default_sqlite_path() -> Path:
    return _DEFAULT_DB_PATH


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class TenantSkillStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ensured = False

    def ensure(self) -> None:
        if self._ensured and self.db_path.is_file():
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(schema)
                self._migrate_log_action_constraint(conn)
                conn.commit()
            finally:
                conn.close()
        self._ensured = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _migrate_log_action_constraint(conn: sqlite3.Connection) -> None:
        row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'tenant_skill_rule_logs'
            """
        ).fetchone()
        table_sql = (row["sql"] if row and row["sql"] else "").upper()
        if "'DELETE'" in table_sql:
            return

        conn.executescript(
            """
            CREATE TABLE tenant_skill_rule_logs__new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT NOT NULL,
                agent       TEXT NOT NULL,
                item_type   TEXT NOT NULL CHECK (item_type IN ('skill', 'rule')),
                item_id     INTEGER NOT NULL,
                action      TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE', 'DELETE')),
                old_value   TEXT,
                new_value   TEXT,
                changed_by  TEXT,
                changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            INSERT INTO tenant_skill_rule_logs__new (
                id, tenant_id, agent, item_type, item_id, action,
                old_value, new_value, changed_by, changed_at
            )
            SELECT
                id, tenant_id, agent, item_type, item_id, action,
                old_value, new_value, changed_by, changed_at
            FROM tenant_skill_rule_logs;

            DROP TABLE tenant_skill_rule_logs;

            ALTER TABLE tenant_skill_rule_logs__new RENAME TO tenant_skill_rule_logs;

            CREATE INDEX IF NOT EXISTS tenant_skill_rule_logs_lookup_idx
                ON tenant_skill_rule_logs (tenant_id, agent, changed_at DESC);
            """
        )

    def list_defaults(self, *, pack_dir: Path) -> dict[str, Any]:
        skill_path = pack_dir / "SKILL.md"
        skill = None
        if skill_path.is_file():
            skill = {
                "slug": "default1",
                "source_file": "SKILL.md",
                "readonly": True,
                "body": skill_path.read_text(encoding="utf-8"),
            }
        rules: list[dict[str, Any]] = []
        for slug, filename in SUMMARY_RULE_FILES:
            path = pack_dir / "rules" / filename
            if not path.is_file():
                continue
            rules.append(
                {
                    "slug": slug,
                    "source_file": filename,
                    "readonly": True,
                    "body": path.read_text(encoding="utf-8"),
                }
            )
        return {"skill": skill, "rules": rules}

    def list_custom_skills(self, *, tenant_id: str, agent: str = _AGENT) -> list[dict[str, Any]]:
        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, agent, slug, source_file, is_custom, is_active,
                           body, updated_by, created_at, updated_at
                    FROM tenant_skills
                    WHERE tenant_id = ? AND agent = ? AND is_custom = 1
                    ORDER BY id ASC
                    """,
                    (tenant_id, agent),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_dict(r) for r in rows]

    def list_active_custom_skills(
        self, *, tenant_id: str, agent: str = _AGENT
    ) -> list[dict[str, Any]]:
        return [
            r for r in self.list_custom_skills(tenant_id=tenant_id, agent=agent) if r["is_active"]
        ]

    def list_custom_rules(self, *, tenant_id: str, agent: str = _AGENT) -> list[dict[str, Any]]:
        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, agent, slug, source_file, is_custom, is_active,
                           always_apply, body, updated_by, created_at, updated_at
                    FROM tenant_rules
                    WHERE tenant_id = ? AND agent = ? AND is_custom = 1
                    ORDER BY id ASC
                    """,
                    (tenant_id, agent),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_dict(r) for r in rows]

    def list_active_custom_rules(
        self, *, tenant_id: str, agent: str = _AGENT
    ) -> list[dict[str, Any]]:
        return [
            r for r in self.list_custom_rules(tenant_id=tenant_id, agent=agent) if r["is_active"]
        ]

    def list_logs(
        self, *, tenant_id: str, agent: str = _AGENT, limit: int = 20
    ) -> list[dict[str, Any]]:
        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, agent, item_type, item_id, action,
                           old_value, new_value, changed_by, changed_at
                    FROM tenant_skill_rule_logs
                    WHERE tenant_id = ? AND agent = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (tenant_id, agent, limit),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_dict(r) for r in rows]

    def migrate_legacy_md_rules_to_skills(
        self, *, tenant_id: str, agent: str = _AGENT, changed_by: str = "migrate"
    ) -> int:
        """Move legacy .md uploads stored in tenant_rules into tenant_skills."""
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            return 0
        self.ensure()
        moved = 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                rows = conn.execute(
                    """
                    SELECT * FROM tenant_rules
                    WHERE tenant_id = ? AND agent = ? AND is_custom = 1
                      AND source_file IS NOT NULL
                      AND lower(source_file) LIKE '%.md'
                    """,
                    (tenant_id, agent),
                ).fetchall()
                for row in rows:
                    slug = row["slug"]
                    existing_skill = conn.execute(
                        """
                        SELECT id FROM tenant_skills
                        WHERE tenant_id = ? AND agent = ? AND slug = ?
                        """,
                        (tenant_id, agent, slug),
                    ).fetchone()
                    if existing_skill:
                        conn.execute("DELETE FROM tenant_rules WHERE id = ?", (row["id"],))
                        moved += 1
                        continue
                    cur = conn.execute(
                        """
                        INSERT INTO tenant_skills (
                            tenant_id, agent, slug, source_file, is_custom, is_active,
                            body, updated_by, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            agent,
                            slug,
                            row["source_file"],
                            row["is_active"],
                            row["body"],
                            changed_by,
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )
                    skill_id = int(cur.lastrowid)
                    conn.execute("DELETE FROM tenant_rules WHERE id = ?", (row["id"],))
                    self._insert_log(
                        conn,
                        tenant_id=tenant_id,
                        agent=agent,
                        item_type="skill",
                        item_id=skill_id,
                        action="CREATE",
                        old_value=None,
                        new_value=row["body"],
                        changed_by=changed_by,
                    )
                    moved += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return moved

    def add_custom_skill(
        self,
        *,
        tenant_id: str,
        body: str,
        slug: Optional[str] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._add_custom(
            table=_TABLE_SKILL,
            item_type="skill",
            tenant_id=tenant_id,
            body=body,
            slug=slug,
            source_file=source_file,
            changed_by=changed_by,
            agent=agent,
        )

    def add_custom_rule(
        self,
        *,
        tenant_id: str,
        body: str,
        slug: Optional[str] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._add_custom(
            table=_TABLE_RULE,
            item_type="rule",
            tenant_id=tenant_id,
            body=body,
            slug=slug,
            source_file=source_file,
            changed_by=changed_by,
            agent=agent,
        )

    def update_custom_skill(
        self,
        *,
        item_id: int,
        tenant_id: str,
        body: Optional[str] = None,
        is_active: Optional[bool] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._update_custom(
            table=_TABLE_SKILL,
            item_type="skill",
            item_id=item_id,
            tenant_id=tenant_id,
            body=body,
            is_active=is_active,
            source_file=source_file,
            changed_by=changed_by,
            agent=agent,
        )

    def update_custom_rule(
        self,
        *,
        item_id: int,
        tenant_id: str,
        body: Optional[str] = None,
        is_active: Optional[bool] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._update_custom(
            table=_TABLE_RULE,
            item_type="rule",
            item_id=item_id,
            tenant_id=tenant_id,
            body=body,
            is_active=is_active,
            source_file=source_file,
            changed_by=changed_by,
            agent=agent,
        )

    def delete_custom_skill(
        self,
        *,
        item_id: int,
        tenant_id: str,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._delete_custom(
            table=_TABLE_SKILL,
            item_type="skill",
            item_id=item_id,
            tenant_id=tenant_id,
            changed_by=changed_by,
            agent=agent,
        )

    def delete_custom_rule(
        self,
        *,
        item_id: int,
        tenant_id: str,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        return self._delete_custom(
            table=_TABLE_RULE,
            item_type="rule",
            item_id=item_id,
            tenant_id=tenant_id,
            changed_by=changed_by,
            agent=agent,
        )

    def _delete_custom(
        self,
        *,
        table: str,
        item_type: str,
        item_id: int,
        tenant_id: str,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    f"""
                    SELECT * FROM {table}
                    WHERE id = ? AND tenant_id = ? AND agent = ? AND is_custom = 1
                    """,
                    (item_id, tenant_id, agent),
                ).fetchone()
                if row is None:
                    raise KeyError(f"custom {item_type} not found")
                snapshot = _row_to_dict(row)
                self._insert_log(
                    conn,
                    tenant_id=tenant_id,
                    agent=agent,
                    item_type=item_type,
                    item_id=item_id,
                    action="DELETE",
                    old_value=row["body"],
                    new_value=None,
                    changed_by=changed_by,
                )
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return snapshot

    def _add_custom(
        self,
        *,
        table: str,
        item_type: str,
        tenant_id: str,
        body: str,
        slug: Optional[str] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        text = (body or "").strip()
        if not text:
            raise ValueError("body is required")
        if len(text) > _MAX_BODY_CHARS:
            raise ValueError(f"body must be at most {_MAX_BODY_CHARS} characters")
        if agent != _AGENT:
            raise ValueError("only the summary agent supports extras in this sample")

        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                next_slug = slug.strip() if slug else self._next_custom_slug(conn, tenant_id, agent, table)
                self._validate_custom_slug(next_slug)
                existing = conn.execute(
                    f"SELECT id FROM {table} WHERE tenant_id = ? AND agent = ? AND slug = ?",
                    (tenant_id, agent, next_slug),
                ).fetchone()
                if existing:
                    raise ValueError(f"slug '{next_slug}' already exists for this tenant")
                if table == _TABLE_RULE:
                    cur = conn.execute(
                        f"""
                        INSERT INTO {table} (
                            tenant_id, agent, slug, source_file, is_custom, is_active,
                            always_apply, body, updated_by
                        ) VALUES (?, ?, ?, ?, 1, 1, 1, ?, ?)
                        """,
                        (tenant_id, agent, next_slug, source_file, text, changed_by),
                    )
                else:
                    cur = conn.execute(
                        f"""
                        INSERT INTO {table} (
                            tenant_id, agent, slug, source_file, is_custom, is_active,
                            body, updated_by
                        ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                        """,
                        (tenant_id, agent, next_slug, source_file, text, changed_by),
                    )
                item_id = int(cur.lastrowid)
                self._insert_log(
                    conn,
                    tenant_id=tenant_id,
                    agent=agent,
                    item_type=item_type,
                    item_id=item_id,
                    action="CREATE",
                    old_value=None,
                    new_value=text,
                    changed_by=changed_by,
                )
                row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return _row_to_dict(row)

    def _update_custom(
        self,
        *,
        table: str,
        item_type: str,
        item_id: int,
        tenant_id: str,
        body: Optional[str] = None,
        is_active: Optional[bool] = None,
        source_file: Optional[str] = None,
        changed_by: str = "console",
        agent: str = _AGENT,
    ) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if body is None and is_active is None and source_file is None:
            raise ValueError("nothing to update")
        if body is not None:
            text = body.strip()
            if not text:
                raise ValueError("body is required")
            if len(text) > _MAX_BODY_CHARS:
                raise ValueError(f"body must be at most {_MAX_BODY_CHARS} characters")
        else:
            text = None

        self.ensure()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    f"""
                    SELECT * FROM {table}
                    WHERE id = ? AND tenant_id = ? AND agent = ? AND is_custom = 1
                    """,
                    (item_id, tenant_id, agent),
                ).fetchone()
                if row is None:
                    raise KeyError(f"custom {item_type} not found")
                old_body = row["body"]
                old_active = int(row["is_active"])
                new_body = text if text is not None else old_body
                new_active = old_active if is_active is None else (1 if is_active else 0)
                new_source = source_file if source_file is not None else row["source_file"]
                if (
                    new_body == old_body
                    and new_active == old_active
                    and new_source == row["source_file"]
                ):
                    conn.rollback()
                    return _row_to_dict(row)

                conn.execute(
                    f"""
                    UPDATE {table}
                    SET body = ?, is_active = ?, source_file = ?, updated_by = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (new_body, new_active, new_source, changed_by, item_id),
                )
                if is_active is not None and new_active != old_active:
                    action = "ENABLE" if new_active else "DISABLE"
                else:
                    action = "UPDATE"
                self._insert_log(
                    conn,
                    tenant_id=tenant_id,
                    agent=agent,
                    item_type=item_type,
                    item_id=item_id,
                    action=action,
                    old_value=old_body,
                    new_value=new_body,
                    changed_by=changed_by,
                )
                updated = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return _row_to_dict(updated)

    @staticmethod
    def _insert_log(
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        agent: str,
        item_type: str,
        item_id: int,
        action: str,
        old_value: Optional[str],
        new_value: Optional[str],
        changed_by: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO tenant_skill_rule_logs (
                tenant_id, agent, item_type, item_id, action,
                old_value, new_value, changed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, agent, item_type, item_id, action, old_value, new_value, changed_by),
        )

    def _next_custom_slug(
        self, conn: sqlite3.Connection, tenant_id: str, agent: str, table: str
    ) -> str:
        rows = conn.execute(
            f"""
            SELECT slug FROM {table}
            WHERE tenant_id = ? AND agent = ? AND is_custom = 1
            """,
            (tenant_id, agent),
        ).fetchall()
        used = set()
        for row in rows:
            match = _CUSTOM_SLUG_RE.match(row["slug"] or "")
            if match:
                used.add(int(match.group(1)))
        n = 1
        while n in used:
            n += 1
        return f"custom{n}"

    @staticmethod
    def _validate_custom_slug(slug: str) -> None:
        if not _CUSTOM_SLUG_RE.match(slug):
            raise ValueError("slug must look like custom1, custom2, …")


@lru_cache(maxsize=8)
def get_store(db_path: str) -> TenantSkillStore:
    store = TenantSkillStore(Path(db_path))
    store.ensure()
    return store


def reset_store() -> None:
    get_store.cache_clear()


def store_from_settings(settings: Optional[object] = None) -> TenantSkillStore:
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    raw = getattr(settings, "tenant_skills_sqlite_path", None)
    path = Path(str(raw)).expanduser() if raw else default_sqlite_path()
    return get_store(str(path.resolve()))
