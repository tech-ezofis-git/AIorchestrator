"""Tests for tenant upload parsing."""
import pytest

from app.tenant_skills.upload import parse_tenant_upload, upload_kind


def test_parse_mdc_strips_frontmatter():
    source, body = parse_tenant_upload(
        filename="privacy.mdc",
        raw="---\ndescription: x\n---\n\nHello rule\n",
    )
    assert source == "privacy.mdc"
    assert body == "Hello rule"


def test_parse_md_skill():
    source, body = parse_tenant_upload(
        filename="test-skill.md",
        raw="---\nname: t\n---\n\nSkill body here.\n",
    )
    assert source == "test-skill.md"
    assert "Skill body" in body


def test_upload_kind():
    from app.tenant_skills.upload import upload_kind

    assert upload_kind("foo.md") == "skill"
    assert upload_kind("bar.mdc") == "rule"
    with pytest.raises(ValueError, match="only .md"):
        upload_kind("notes.txt")
