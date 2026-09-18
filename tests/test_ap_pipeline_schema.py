"""Phase 1 AP pipeline Catalog schema — flag + migration wiring (no runner)."""
from __future__ import annotations

from pathlib import Path

from app.catalog.store import _MIGRATIONS, _split_sql
from app.config import Settings


def test_ap_pipeline_from_db_defaults_true() -> None:
    assert Settings().ap_pipeline_from_db is True


def test_ap_pipeline_migration_is_registered_and_splits() -> None:
    names = [p.name for p in _MIGRATIONS]
    assert "0009_create_ap_pipeline_tables.sql" in names
    path = Path(__file__).resolve().parents[1] / "db" / "migrations" / "0009_create_ap_pipeline_tables.sql"
    stmts = _split_sql(path.read_text(encoding="utf-8"))
    joined = "\n".join(stmts)
    assert "CREATE TABLE IF NOT EXISTS platform_ap_pipeline" in joined
    assert "CREATE TABLE IF NOT EXISTS tenant_ap_pipeline" in joined
    assert "CREATE TABLE IF NOT EXISTS tenant_ap_pipeline_logs" in joined
    assert len(stmts) == 6
