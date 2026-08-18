"""Local SQLite tenant Summary extras + console API."""
from pathlib import Path

import pytest

from app.agent_skills.loader import clear_skill_cache
from app.tenant_skills.overlay import get_summary_skill
from app.tenant_skills.store import TenantSkillStore, reset_store


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_skill_cache()
    reset_store()
    yield
    clear_skill_cache()
    reset_store()


@pytest.fixture
def store(tmp_path: Path) -> TenantSkillStore:
    db = tmp_path / "tenant_skills.sqlite"
    return TenantSkillStore(db)


def test_store_creates_three_tables(store: TenantSkillStore):
    store.ensure()
    with store._connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "tenant_skills" in tables
    assert "tenant_rules" in tables
    assert "tenant_skill_rule_logs" in tables


def test_add_update_disable_custom_rule_writes_log(store: TenantSkillStore):
    created = store.add_custom_rule(
        tenant_id="tenant-a",
        body="Always mention GSTIN when present.",
        changed_by="tester",
    )
    assert created["slug"] == "custom1"
    assert created["is_active"] == 1

    updated = store.update_custom_rule(
        item_id=created["id"],
        tenant_id="tenant-a",
        body="Always mention GSTIN and PAN.",
        changed_by="tester",
    )
    assert updated["body"].startswith("Always mention GSTIN and PAN")

    disabled = store.update_custom_rule(
        item_id=created["id"],
        tenant_id="tenant-a",
        is_active=False,
        changed_by="tester",
    )
    assert disabled["is_active"] == 0
    assert store.list_active_custom_rules(tenant_id="tenant-a") == []

    logs = store.list_logs(tenant_id="tenant-a")
    actions = {log["action"] for log in logs}
    assert actions == {"CREATE", "UPDATE", "DISABLE"}
    assert all(log["item_type"] == "rule" for log in logs)


def test_add_custom_skill_logs_item_type_skill(store: TenantSkillStore):
    created = store.add_custom_skill(
        tenant_id="tenant-a",
        body="Custom skill instructions.",
        changed_by="tester",
    )
    assert created["slug"] == "custom1"
    logs = store.list_logs(tenant_id="tenant-a")
    assert logs[0]["item_type"] == "skill"
    assert logs[0]["item_id"] == created["id"]


def test_second_custom_gets_custom2_slug(store: TenantSkillStore):
    store.add_custom_rule(tenant_id="tenant-a", body="Rule one.")
    second = store.add_custom_rule(tenant_id="tenant-a", body="Rule two.")
    assert second["slug"] == "custom2"


def test_get_summary_skill_appends_active_custom_skills(store: TenantSkillStore, monkeypatch):
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(store.db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    store.add_custom_skill(
        tenant_id="tenant-a",
        body="TENANT_CUSTOM_SKILL_BLOCK",
    )
    store.add_custom_rule(
        tenant_id="tenant-a",
        body="TENANT_CUSTOM_GSTIN_RULE",
    )

    prompt = get_summary_skill(tenant_id="tenant-a").system_prompt
    assert "TENANT_CUSTOM_SKILL_BLOCK" in prompt
    assert "TENANT_CUSTOM_GSTIN_RULE" in prompt
    assert "key_facts_extracted" in prompt

    get_settings.cache_clear()


def test_get_summary_skill_appends_active_custom_rules(store: TenantSkillStore, monkeypatch):
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(store.db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    store.add_custom_rule(
        tenant_id="tenant-a",
        body="TENANT_CUSTOM_GSTIN_RULE",
    )
    store.add_custom_rule(
        tenant_id="tenant-a",
        body="TENANT_CUSTOM_DISABLED",
    )
    store.update_custom_rule(
        item_id=store.list_custom_rules(tenant_id="tenant-a")[1]["id"],
        tenant_id="tenant-a",
        is_active=False,
    )

    prompt = get_summary_skill(tenant_id="tenant-a").system_prompt
    assert "TENANT_CUSTOM_GSTIN_RULE" in prompt
    assert "TENANT_CUSTOM_DISABLED" not in prompt
    assert "key_facts_extracted" in prompt

    get_settings.cache_clear()


def test_migrate_legacy_md_rule_to_skills(store: TenantSkillStore):
    rule = store.add_custom_rule(
        tenant_id="tenant-a",
        body="Legacy skill body.",
        source_file="test-skill.md",
        slug="custom1",
        changed_by="tester",
    )
    assert rule["source_file"] == "test-skill.md"

    moved = store.migrate_legacy_md_rules_to_skills(tenant_id="tenant-a")
    assert moved == 1
    assert store.list_custom_rules(tenant_id="tenant-a") == []
    skills = store.list_custom_skills(tenant_id="tenant-a")
    assert len(skills) == 1
    assert skills[0]["slug"] == "custom1"
    assert skills[0]["source_file"] == "test-skill.md"
    logs = store.list_logs(tenant_id="tenant-a")
    assert any(log["item_type"] == "skill" for log in logs)


def test_get_summary_skill_without_tenant_is_default_only():
    prompt = get_summary_skill(tenant_id="").system_prompt
    assert "key_facts_extracted" in prompt.lower()


def test_console_summary_defaults_without_tenant(client):
    res = client.get("/console/summary-skills/defaults")
    assert res.status_code == 200
    body = res.json()
    assert body["defaults"]["skill"]["source_file"] == "SKILL.md"
    assert len(body["defaults"]["rules"]) >= 3


def test_console_summary_skills_without_tenant_returns_defaults_only(client):
    res = client.get("/console/summary-skills")
    assert res.status_code == 200
    body = res.json()
    assert body["tenant_id"] is None
    assert body["defaults"]["skill"]["source_file"] == "SKILL.md"
    assert body["custom_skills"] == []
    assert body["custom_rules"] == []


def test_console_summary_skills_api(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    listed = client.get("/console/summary-skills", params={"tenant_id": "tenant-a"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["defaults"]["skill"]["source_file"] == "SKILL.md"
    assert len(body["defaults"]["rules"]) >= 3
    assert body["custom_skills"] == []
    assert body["custom_rules"] == []

    created = client.post(
        "/console/summary-skills/custom-rules",
        json={"tenant_id": "tenant-a", "body": "Prefer insurance wording."},
    )
    assert created.status_code == 200
    rule_id = created.json()["rule"]["id"]

    patched = client.patch(
        f"/console/summary-skills/custom-rules/{rule_id}",
        json={"tenant_id": "tenant-a", "is_active": False},
    )
    assert patched.status_code == 200
    assert patched.json()["rule"]["is_active"] == 0

    get_settings.cache_clear()


def test_upload_summary_custom_rule_mdc(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    mdc = (
        "---\ndescription: uploaded test rule\nalwaysApply: true\n---\n\n"
        "UPLOADED_RULE_TOKEN\n"
    )
    res = client.post(
        "/console/summary-skills/custom-rules/upload",
        data={"tenant_id": "tenant-a"},
        files={"file": ("test-rule.mdc", mdc, "text/plain")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "rule"
    assert body["source_file"] == "test-rule.mdc"
    assert "UPLOADED_RULE_TOKEN" in body["rule"]["body"]

    get_settings.cache_clear()


def test_upload_md_goes_to_skills_table(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    md = "---\nname: test\n---\n\nUPLOADED_SKILL_TOKEN\n"
    res = client.post(
        "/console/summary-skills/custom-rules/upload",
        data={"tenant_id": "tenant-a"},
        files={"file": ("test-skill.md", md, "text/plain")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "skill"
    assert body["source_file"] == "test-skill.md"
    assert "UPLOADED_SKILL_TOKEN" in body["skill"]["body"]

    listed = client.get("/console/summary-skills", params={"tenant_id": "tenant-a"})
    payload = listed.json()
    assert len(payload["custom_skills"]) == 1
    assert payload["custom_rules"] == []
    assert payload["logs"][0]["item_type"] == "skill"

    get_settings.cache_clear()


def test_replace_summary_custom_rule_upload(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    created = client.post(
        "/console/summary-skills/custom-rules",
        json={"tenant_id": "tenant-a", "body": "Original body."},
    )
    assert created.status_code == 200
    rule_id = created.json()["rule"]["id"]

    mdc = "---\ndescription: replaced\n---\n\nREPLACED_BODY_TOKEN\n"
    replaced = client.post(
        f"/console/summary-skills/custom-rules/{rule_id}/upload",
        data={"tenant_id": "tenant-a"},
        files={"file": ("replaced-rule.mdc", mdc, "text/plain")},
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["source_file"] == "replaced-rule.mdc"
    assert "REPLACED_BODY_TOKEN" in body["rule"]["body"]

    get_settings.cache_clear()


def test_delete_custom_rule_removes_record(store: TenantSkillStore):
    created = store.add_custom_rule(
        tenant_id="tenant-a",
        body="Rule to be deleted.",
        changed_by="tester",
    )
    rule_id = created["id"]
    assert created["slug"] == "custom1"

    deleted = store.delete_custom_rule(item_id=rule_id, tenant_id="tenant-a", changed_by="tester")
    assert deleted["id"] == rule_id

    remaining = store.list_custom_rules(tenant_id="tenant-a")
    assert remaining == []

    logs = store.list_logs(tenant_id="tenant-a")
    actions = [l["action"] for l in logs]
    assert "DELETE" in actions
    delete_log = next(l for l in logs if l["action"] == "DELETE")
    assert delete_log["item_type"] == "rule"
    assert delete_log["item_id"] == rule_id


def test_delete_custom_skill_removes_record(store: TenantSkillStore):
    created = store.add_custom_skill(
        tenant_id="tenant-a",
        body="Skill to be deleted.",
        changed_by="tester",
    )
    skill_id = created["id"]

    deleted = store.delete_custom_skill(item_id=skill_id, tenant_id="tenant-a", changed_by="tester")
    assert deleted["id"] == skill_id

    remaining = store.list_custom_skills(tenant_id="tenant-a")
    assert remaining == []

    logs = store.list_logs(tenant_id="tenant-a")
    delete_log = next(l for l in logs if l["action"] == "DELETE")
    assert delete_log["item_type"] == "skill"


def test_delete_custom_rule_api(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    created = client.post(
        "/console/summary-skills/custom-rules",
        json={"tenant_id": "tenant-a", "body": "Delete me via API."},
    )
    assert created.status_code == 200
    rule_id = created.json()["rule"]["id"]

    res = client.delete(
        f"/console/summary-skills/custom-rules/{rule_id}",
        params={"tenant_id": "tenant-a"},
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "rule"

    listed = client.get("/console/summary-skills", params={"tenant_id": "tenant-a"})
    assert listed.json()["custom_rules"] == []
    assert any(l["action"] == "DELETE" for l in listed.json()["logs"])

    get_settings.cache_clear()


def test_delete_nonexistent_returns_404(client, tmp_path: Path, monkeypatch):
    db = tmp_path / "tenant_skills.sqlite"
    monkeypatch.setenv("TENANT_SKILLS_SQLITE_PATH", str(db))
    from app.config import get_settings

    get_settings.cache_clear()

    res = client.delete(
        "/console/summary-skills/custom-rules/9999",
        params={"tenant_id": "tenant-a"},
    )
    assert res.status_code == 404

    get_settings.cache_clear()


def test_ensure_migrates_old_log_constraint(tmp_path: Path):
    db = tmp_path / "tenant_skills.sqlite"
    with TenantSkillStore(db)._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE tenant_skill_rule_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT NOT NULL,
                agent       TEXT NOT NULL,
                item_type   TEXT NOT NULL CHECK (item_type IN ('skill', 'rule')),
                item_id     INTEGER NOT NULL,
                action      TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE')),
                old_value   TEXT,
                new_value   TEXT,
                changed_by  TEXT,
                changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO tenant_skill_rule_logs (
                tenant_id, agent, item_type, item_id, action, old_value, new_value, changed_by
            ) VALUES ('tenant-a', 'summary', 'rule', 1, 'CREATE', NULL, 'body', 'tester');
            """
        )
        conn.commit()

    store = TenantSkillStore(db)
    store.ensure()
    created = store.add_custom_rule(
        tenant_id="tenant-a",
        body="Rule to be deleted after migration.",
        changed_by="tester",
    )
    deleted = store.delete_custom_rule(
        item_id=created["id"],
        tenant_id="tenant-a",
        changed_by="tester",
    )
    assert deleted["id"] == created["id"]
    logs = store.list_logs(tenant_id="tenant-a")
    assert any(log["action"] == "DELETE" for log in logs)
