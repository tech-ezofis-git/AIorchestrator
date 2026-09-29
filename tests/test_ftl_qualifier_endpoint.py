"""Tests for FTL RFQ Qualifier REST endpoints and agent integration."""
import pytest
from unittest.mock import patch


def test_qualifier_skill_endpoints(client, monkeypatch, tmp_path):
    # Keep the live skill.json intact. This test used to write the stub
    # "Updated test qualification instructions." into that file.
    monkeypatch.setattr("app.ftl.qualifier.skill_store.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.ftl.qualifier.skill_store.SKILL_PATH", str(tmp_path / "skill.json"))

    # GET skill
    res = client.get("/api/ftl/qualifier/skill")
    assert res.status_code == 200
    data = res.json()
    assert "instructions" in data
    assert "references" in data

    # PUT skill
    update_res = client.put(
        "/api/ftl/qualifier/skill",
        json={"instructions": "Updated test qualification instructions."},
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert "Updated test qualification instructions" in updated_data.get("instructions", "")


def test_qualifier_pricelist_status(client):
    res = client.get("/api/ftl/qualifier/pricelist/status")
    assert res.status_code == 200
    data = res.json()
    assert "indexed" in data
    assert "total_chunks" in data


def test_qualifier_runs_list(client):
    res = client.get("/api/ftl/qualifier/runs")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_qualifier_direct_endpoint(client, monkeypatch):
    mock_decision = {
        "qualify": "qualify",
        "project_type": "modernization",
        "matched_items": [
            {
                "item": "Wittur Hydra Plus 2-Panel Center Opening Door Operator",
                "category": "door_operator",
                "match": "exact",
                "catalog_ref": "HYDRA-PLUS-CO-42",
                "note": "Standard 42 inch opening",
            }
        ],
        "excluded_items": [],
        "flags": ["Standard lead time 4 weeks"],
        "deadline": "2026-10-15",
        "project_name": "Bloor St Modernization",
        "reasoning": "Standard modernization scope matching Wittur catalog items.",
        "confidence": 0.95,
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 1250

    monkeypatch.setattr(
        "app.ftl.qualifier.agent.run_qualification", fake_run_qualification
    )

    # Test POST /api/ftl/qualify with candidate text
    res = client.post(
        "/api/ftl/qualify",
        json={
            "filename": "rfq_spec.pdf",
            "candidate_text": "Sample modernization tender text for elevator door operators.",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["decision"]["Qualify"] == "Qualify"
    assert body["decision"]["Project"] == "Bloor St Modernization"
    assert body["decision"]["Project type"] == "Modernization"
    assert "Company Name" in body["decision"]
    assert body["decision"]["Confidence"] == 95
    assert "Ai Insight" in body["decision"]
    assert "Excluded items" in body["decision"]
    item = body["decision"]["Matched items"][0]
    assert item["Category"] == "Door Operator"
    assert item["Match"] == "Exact"
    assert item["Catalog Ref"] == "HYDRA-PLUS-CO-42"
    assert body["total_tokens"] == 1250
    assert "run_id" in body


def test_chat_ftl_qualifier_intent(client, monkeypatch):
    mock_decision = {
        "qualify": "needs_review",
        "project_type": "modernization",
        "matched_items": [],
        "excluded_items": [{"item": "Custom Cab Interior", "reason": "Not sold by FTL"}],
        "flags": ["Special glass panels requested"],
        "deadline": None,
        "project_name": "Riverdale Tower",
        "reasoning": "Mixed scope requiring manual review.",
        "confidence": 0.75,
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 980

    monkeypatch.setattr(
        "app.ftl.qualifier.agent.run_qualification", fake_run_qualification
    )

    res = client.post(
        "/chat",
        json={
            "session_id": "session-ftl-qual-1",
            "intent": "ftl_qualifier",
            "payload": {
                "candidate_text": "Riverdale Tower elevator modernization specs.",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "session-ftl-qual-1"
    assert "qualifier_result" in body and body["qualifier_result"] is not None
    assert body["qualifier_result"]["Qualify"] == "Needs Review"
    assert body["qualifier_result"]["Confidence"] == 75
    assert "NEEDS REVIEW" in body["reply"]


def test_policy_overrides_follow_git_backstops():
    from app.ftl.qualifier.agent import _apply_policy_overrides

    new_construction = _apply_policy_overrides(
        {"qualify": "qualify", "project_type": "modernization", "matched_items": [], "reasoning": "items match", "flags": []},
        "## Detected structure signal: new_construction_single_spec\n",
    )
    assert new_construction["qualify"] == "disqualify"
    assert new_construction["project_type"] == "new_construction"

    unknown_with_governor = _apply_policy_overrides(
        {
            "qualify": "disqualify",
            "project_type": "unknown",
            "matched_items": [{"item": "new governor", "category": "governor", "match": "ambiguous"}],
            "reasoning": "Therefore: disqualify.",
            "flags": [],
        },
        "",
    )
    assert unknown_with_governor["qualify"] == "needs_review"
    assert unknown_with_governor["reasoning"].endswith("pre-override item-matching pass, superseded by the auto-override explained at the top of this field.)")

    lone_detector = _apply_policy_overrides(
        {
            "qualify": "qualify",
            "project_type": "unknown",
            "matched_items": [
                {"item": "infra-red detector", "category": "detector", "match": "exact"},
                {"item": "Formula System Power Supply", "category": "power supply", "match": "exact"},
            ],
            "reasoning": "",
            "flags": [],
        },
        "",
    )
    assert lone_detector["qualify"] == "disqualify"

    ambiguous_governor = _apply_policy_overrides(
        {
            "qualify": "disqualify",
            "project_type": "modernization",
            "matched_items": [{"item": "new governor", "category": "governor", "match": "ambiguous"}],
            "reasoning": "no exact variant",
            "flags": [],
        },
        "",
    )
    assert ambiguous_governor["qualify"] == "qualify"

    coventry = _apply_policy_overrides(
        {
            "qualify": "disqualify",
            "project_type": "modernization",
            "matched_items": [],
            "excluded_items": [],
            "flags": [],
            "reasoning": (
                "Existing door operators are KONE AMDC1C-52, not Wittur SGV2. "
                "The requested door operators are generic ('OEM or Wittur') but the spec does not name "
                "a specific Wittur model (2T/2C/1S). The only grounded in-scope item is the 3D/2D infrared "
                "door protective device. A lone detector cannot qualify the RFQ."
            ),
        },
        "",
    )
    assert coventry["qualify"] == "qualify"
    categories = {item["category"] for item in coventry["matched_items"]}
    assert "door_operator" in categories
    assert "detector" in categories

    harmonic_only = _apply_policy_overrides(
        {
            "qualify": "disqualify",
            "project_type": "modernization",
            "matched_items": [{"item": "infra-red detector", "category": "detector", "match": "exact"}],
            "reasoning": "Door operator row is new harmonic, an unsupported brand. Lone detector cannot qualify.",
            "flags": [],
        },
        "",
    )
    assert harmonic_only["qualify"] == "disqualify"


def test_extract_keeps_short_text_and_split_section_numbers():
    from app.ftl.qualifier.extract import build_candidate_text, detect_structure_signal

    short = "Please quote the machine and any other components for this freight car."
    candidate = build_candidate_text(short)
    assert candidate["used_fallback"] is True
    assert candidate["fallback_text"] == short

    split_lines = "\n".join(f"{n}.1" for n in range(10, 20))
    assert detect_structure_signal(split_lines, subsection_hit_count=0) == "modernization_3section"

    shaw = "2.01 Existing Equipment Information\nType: Hydraulic\nDoor Configuration: single"
    subsections = build_candidate_text(shaw)["subsections"]
    assert "Existing Equipment Information" in subsections


@pytest.mark.asyncio
async def test_eml_candidate_text_includes_email_body_and_structure_signal():
    from app.agents.ftl_qualifier_agent import FtlQualifierAgent
    from app.ftl.qualifier.extract import detect_structure_signal

    eml_content = (
        b"From: francine@ftl-distribution.ca\r\n"
        b"Subject: New Building Tender\r\n"
        b"Date: Wed, 20 Aug 2026 12:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"This is a new construction (new building) tender - we do not quote 95% of the time.\r\n"
    )

    agent = FtlQualifierAgent()
    rendered, email_meta, ext = await agent._build_candidate_from_bytes(eml_content, "rfq.eml")

    assert ext == "eml"
    assert "body_text" in email_meta
    assert "new construction (new building) tender" in email_meta["body_text"]
    assert "## Email" in rendered
    assert "This is a new construction (new building) tender" in rendered
    assert "## Detected structure signal: new_construction_single_spec" in rendered

    new_const_spec = (
        "PART 1 - GENERAL\n1.1 Scope\n"
        "PART 2 - PRODUCTS\n2.1 Elevators\n"
        "PART 3 - EXECUTION\n3.1 Installation\n"
    )
    sig = detect_structure_signal(new_const_spec)
    assert sig == "new_construction_single_spec"


def test_power_supply_with_phrase_without_operator_never_triggers_qualify_override():
    from app.ftl.qualifier.agent import _apply_policy_overrides

    decision = {
        "qualify": "disqualify",
        "project_type": "modernization",
        "matched_items": [
            {
                "item": "3D door detector (infra-red multiple beam)",
                "category": "door protective device/detector",
                "match": "exact",
                "catalog_ref": "VISIONPLUS_3D_DETECTOR",
            },
            {
                "item": "Formula System Power Supply (for 3D detector without operator)",
                "category": "door protective device/detector",
                "match": "exact",
                "catalog_ref": "VISIONPLUS_POWERSUPPLY",
            },
        ],
        "excluded_items": [
            {
                "item": "door operator",
                "reason": "Brand not confirmed Wittur/SGV2",
            }
        ],
        "flags": [],
        "reasoning": "The door-package coupling rule excludes quoting the 3D detector alone without a supported door operator.",
    }

    overridden = _apply_policy_overrides(decision, "## Detected structure signal: modernization_3section\n")
    assert overridden["qualify"] == "disqualify"
    assert "auto_overridden_ambiguous_item_qualify_threshold" not in (overridden.get("flags") or [])


