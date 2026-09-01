"""Unit tests for duplicate_detect — code-review finding #11 (the
near-duplicate branch used to compute a vendor-similarity score and then
discard it via a bare `continue`, never actually flagging anything)."""
from types import SimpleNamespace

from app.ap_skills.duplicate_detect import run as duplicate_detect_run
from app.ap_skills.types import ApContext


class _FakeStore:
    def __init__(self, artifacts: list[dict]):
        self._artifacts = artifacts

    async def list_skill_artifacts(self, *, tenant_id, skill_id):
        return self._artifacts


class _FakeEzofis:
    async def lookup_invoice_history(self, *, tenant_id, invoice_number=None):
        return []


def _ctx(*, invoice_number: str, vendor: str, history: list[dict]) -> ApContext:
    return ApContext(
        tenant_id="t-1",
        item_key="doc-new",
        run_id="run-1",
        session_id="s-1",
        invoice_json={"invoice_number": invoice_number, "vendor": vendor},
        artifacts={},
        settings=SimpleNamespace(),
        ezofis=_FakeEzofis(),
        store=_FakeStore(history),
    )


async def test_exact_invoice_number_match_is_a_hard_duplicate():
    history = [{"item_key": "doc-old", "invoice": {"invoice_number": "INV-100", "vendor": "Acme"}}]
    result = await duplicate_detect_run(_ctx(invoice_number="INV-100", vendor="Acme", history=history))
    assert result.data["is_duplicate_invoice"] is True
    assert result.data["duplicate_score"] == 1.0


async def test_same_vendor_similar_invoice_number_is_flagged_as_possible_duplicate():
    """A typo'd/reformatted invoice number from the same vendor used to be
    silently dropped (the dead `continue` branch) — now it's a distinct,
    weaker signal."""
    history = [{"item_key": "doc-old", "invoice": {"invoice_number": "INV-1000", "vendor": "Acme Supplies"}}]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-1001", vendor="Acme Supplies", history=history)
    )
    assert result.data["is_duplicate_invoice"] is False
    assert result.data["possible_duplicate_of"] == "INV-1000"
    assert result.data["possible_duplicate_score"] > 0


async def test_different_vendor_is_never_flagged():
    history = [{"item_key": "doc-old", "invoice": {"invoice_number": "INV-1000", "vendor": "Beta Corp"}}]
    result = await duplicate_detect_run(
        _ctx(invoice_number="INV-1001", vendor="Acme Supplies", history=history)
    )
    assert result.data["is_duplicate_invoice"] is False
    assert result.data["possible_duplicate_of"] is None
