"""Unit tests for duplicate_detect — hard vs soft (retry) duplicates."""
from types import SimpleNamespace

from app.ap_skills.duplicate_detect import run as duplicate_detect_run
from app.ap_skills.types import ApContext


class _FakeStore:
    def __init__(self, by_skill: dict[str, list[dict]]):
        self._by_skill = by_skill

    async def list_skill_artifacts(self, *, tenant_id, skill_id):
        return list(self._by_skill.get(skill_id) or [])


class _FakeEzofis:
    def __init__(self, history: list[dict] | None = None):
        self._history = history or []

    async def lookup_invoice_history(self, *, tenant_id, invoice_number=None):
        return list(self._history)


def _ctx(
    *,
    invoice_number: str,
    vendor: str,
    extracts: list[dict] | None = None,
    finalizes: list[dict] | None = None,
    cloud: list[dict] | None = None,
    item_key: str = "doc-new",
) -> ApContext:
    return ApContext(
        tenant_id="t-1",
        item_key=item_key,
        run_id="run-1",
        session_id="s-1",
        invoice_json={"invoice_number": invoice_number, "vendor": vendor},
        artifacts={},
        settings=SimpleNamespace(),
        ezofis=_FakeEzofis(cloud),
        store=_FakeStore(
            {
                "extract_invoice": extracts or [],
                "finalize_decision": finalizes or [],
            }
        ),
    )


async def test_prior_extract_without_matched_is_ignored():
    """Re-ingesting INV-2026-6001 after a Not Matched run must not hard-fail."""
    extracts = [
        {"item_key": "doc-old", "invoice": {"invoice_number": "INV-100", "vendor": "Acme"}},
    ]
    finalizes = [
        {"item_key": "doc-old", "decision": "NOT_MATCHED"},
    ]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-100", vendor="Acme", extracts=extracts, finalizes=finalizes)
    )
    assert result.data["is_duplicate_invoice"] is False
    assert result.data["possible_duplicate_of"] is None


async def test_prior_matched_extract_is_hard_duplicate():
    extracts = [
        {"item_key": "doc-old", "invoice": {"invoice_number": "INV-100", "vendor": "Acme"}},
    ]
    finalizes = [
        {"item_key": "doc-old", "decision": "MATCHED"},
    ]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-100", vendor="Acme", extracts=extracts, finalizes=finalizes)
    )
    assert result.data["is_duplicate_invoice"] is True
    assert result.data["duplicate_of"] == "INV-100"
    assert result.data["duplicate_score"] == 1.0


async def test_cloud_history_exact_match_is_hard_duplicate():
    result = await duplicate_detect_run(
        _ctx(
            invoice_number="INV-100",
            vendor="Acme",
            cloud=[{"invoice_number": "INV-100", "vendor": "Acme"}],
        )
    )
    assert result.data["is_duplicate_invoice"] is True
    assert result.data["duplicate_of"] == "INV-100"


async def test_same_item_key_is_ignored():
    extracts = [
        {"item_key": "doc-new", "invoice": {"invoice_number": "INV-100", "vendor": "Acme"}},
    ]
    finalizes = [
        {"item_key": "doc-new", "decision": "MATCHED"},
    ]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-100", vendor="Acme", extracts=extracts, finalizes=finalizes)
    )
    assert result.data["is_duplicate_invoice"] is False


async def test_same_vendor_similar_invoice_number_is_flagged_as_possible_duplicate():
    extracts = [
        {"item_key": "doc-old", "invoice": {"invoice_number": "INV-1000", "vendor": "Acme Supplies"}},
    ]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-1001", vendor="Acme Supplies", extracts=extracts)
    )
    assert result.data["is_duplicate_invoice"] is False
    assert result.data["possible_duplicate_of"] == "INV-1000"
    assert result.data["possible_duplicate_score"] > 0


async def test_different_vendor_is_never_flagged():
    extracts = [
        {"item_key": "doc-old", "invoice": {"invoice_number": "INV-1000", "vendor": "Beta Corp"}},
    ]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-1001", vendor="Acme Supplies", extracts=extracts)
    )
    assert result.data["is_duplicate_invoice"] is False
    assert result.data["possible_duplicate_of"] is None
