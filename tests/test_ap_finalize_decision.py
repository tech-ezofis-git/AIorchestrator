"""Unit tests for finalize_decision — code-review finding #4 (mock master
data must not drive an auto-approved MATCHED decision in a live
deployment, but trial/dev deployments running against mock masters by
design must be unaffected)."""
from types import SimpleNamespace

from app.ap_skills.finalize_decision import run as finalize_decision_run
from app.ap_skills.types import ApContext

_INVOICE = {"invoice_number": "INV-1", "vendor": "Acme", "doc_type": "invoice"}


def _ctx(*, ezofis_env: str, artifacts: dict) -> ApContext:
    return ApContext(
        tenant_id="t-1",
        item_key="doc-1",
        run_id="run-1",
        session_id="s-1",
        invoice_json=_INVOICE,
        artifacts=artifacts,
        settings=SimpleNamespace(ezofis_env=ezofis_env),
        ezofis=None,
    )


async def test_trial_env_mock_match_is_not_capped():
    """Default/trial deployments run against mock masters by design (no
    live EZOFIS login configured) — a mock MATCHED stays MATCHED."""
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": True}},
        "vendor_validate": {"status": "ACTIVE", "mock": True},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="trial", artifacts=artifacts))
    assert result.data["decision"] == "MATCHED"
    assert result.data["used_mock_data"] is False


async def test_live_env_mock_po_caps_matched_to_partially_matched():
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": True}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme"},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert result.data["used_mock_data"] is True
    assert "mock" in result.data["reason"].lower()


async def test_live_env_mock_vendor_caps_matched_to_partially_matched():
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": False}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme", "mock": True},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert result.data["used_mock_data"] is True


async def test_live_env_real_data_is_not_capped():
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": False}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme", "mock": False},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "MATCHED"
    assert result.data["used_mock_data"] is False


async def test_live_env_mock_data_does_not_upgrade_a_worse_decision():
    """Capping only ever pulls MATCHED down to PARTIALLY_MATCHED — it must
    never improve an already-worse decision."""
    artifacts = {
        "po_match": {"decision": "NOT_MATCHED", "po": {"mock": True}},
        "vendor_validate": {"status": "MISMATCH", "mock": True},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "NOT_MATCHED"
    assert result.data["used_mock_data"] is True


async def test_live_env_mock_grn_caps_matched_to_partially_matched():
    """ultrareview fix: grn_match's mock GRN record (EzofisClient.lookup_grn
    falls back to a fabricated GRN identically to lookup_po/lookup_vendor)
    used to be invisible to used_mock_data — only po_match/vendor_validate
    were checked, so a live deployment with only GRN credentials unset
    could still auto-approve MATCHED off fabricated GRN data."""
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": False}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme", "mock": False},
        "grn_match": {"decision": "MATCHED", "grn": {"mock": True}},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert result.data["used_mock_data"] is True


async def test_live_env_mock_matter_caps_matched_to_partially_matched():
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": False}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme", "mock": False},
        "matter_validate": {"status": "MATCHED", "matter_master_match": {"mock": True}},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert result.data["used_mock_data"] is True


async def test_mock_data_and_possible_duplicate_caps_combine_into_one_reason():
    """The unified capping mechanism (ultrareview altitude fix) reports
    every applicable reason, not just whichever check happened to run
    first."""
    artifacts = {
        "po_match": {"decision": "MATCHED", "po": {"mock": True}},
        "vendor_validate": {"status": "ACTIVE", "expected": "Acme"},
        "duplicate_detect": {"possible_duplicate_of": "INV-0", "possible_duplicate_score": 0.9},
    }
    result = await finalize_decision_run(_ctx(ezofis_env="live", artifacts=artifacts))
    assert result.data["decision"] == "PARTIALLY_MATCHED"
    assert "mock" in result.data["reason"].lower()
    assert "duplicate" in result.data["reason"].lower()
