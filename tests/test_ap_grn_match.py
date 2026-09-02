"""Unit tests for grn_match — ultrareview altitude finding: line matching
used to pair invoice lines to GRN lines purely by array position, the same
bug backorder_detect.py had before finding #12's fix. Both now share
app.ap_skills.types.match_lines_by_description."""
from types import SimpleNamespace

from app.ap_skills.grn_match import run as grn_match_run
from app.ap_skills.types import ApContext


class _FakeEzofis:
    def __init__(self, grn: dict):
        self._grn = grn

    async def lookup_grn(self, *, tenant_id, grn_number=None, po_number=None):
        return self._grn


def _ctx(*, invoice_lines: list, grn_lines: list) -> ApContext:
    return ApContext(
        tenant_id="t-1",
        item_key="doc-1",
        run_id="run-1",
        session_id="s-1",
        invoice_json={
            "vendor": "Acme",
            "po_number": "PO-1",
            "line_items": invoice_lines,
        },
        artifacts={},
        settings=SimpleNamespace(ap_approved_threshold=80, ap_partial_threshold=50),
        ezofis=_FakeEzofis({"vendor": "Acme", "lines": grn_lines}),
    )


async def test_reordered_grn_lines_are_matched_by_description_not_position():
    """Invoice lists Widget B before Widget A; GRN lists them in PO order.
    Positional pairing would compare invoice Widget B's qty against the
    GRN's Widget A quantity (and vice versa) — description-based matching
    must pair them correctly instead."""
    invoice_lines = [
        {"description": "Widget B", "qty": 5},
        {"description": "Widget A", "qty": 10},
    ]
    grn_lines = [
        {"description": "Widget A", "qty": 10, "received_qty": 10},
        {"description": "Widget B", "qty": 5, "received_qty": 5},
    ]
    result = await grn_match_run(_ctx(invoice_lines=invoice_lines, grn_lines=grn_lines))
    assert "2/2 line qty matched GRN" in result.data["reason"]
