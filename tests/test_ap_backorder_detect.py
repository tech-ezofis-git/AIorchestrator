"""Unit tests for backorder_detect — code-review findings #12/#17
(key-based, not positional, line matching; a missing invoice line is
distinct from a confirmed zero-quantity short-ship)."""
from types import SimpleNamespace

from app.ap_skills.backorder_detect import run as backorder_run
from app.ap_skills.types import ApContext

_PO = {
    "vendor": "Acme",
    "lines": [
        {"id": "1", "description": "Widget A", "qty": 10},
        {"id": "2", "description": "Widget B", "qty": 5},
    ],
}


def _ctx(*, invoice_lines: list, po=None) -> ApContext:
    return ApContext(
        tenant_id="t-1",
        item_key="doc-1",
        run_id="run-1",
        session_id="s-1",
        invoice_json={"line_items": invoice_lines},
        artifacts={"po_match": {"decision": "MATCHED", "po": po or _PO}},
        settings=SimpleNamespace(),
        ezofis=None,
    )


async def test_reordered_lines_are_matched_by_description_not_position():
    """The invoice lists Widget B before Widget A — positional matching
    would wrongly pair PO line 1 (Widget A, qty 10) with invoice line 0
    (Widget B, qty 5) and vice versa. Description-based matching must get
    both pairs right."""
    invoice_lines = [
        {"description": "Widget B", "qty": 5},
        {"description": "Widget A", "qty": 10},
    ]
    result = await backorder_run(_ctx(invoice_lines=invoice_lines))
    assert result.data["detected"] is False
    assert result.data["missing_qty_by_item"] == []


async def test_short_shipped_line_is_flagged_with_correct_pairing():
    invoice_lines = [
        {"description": "Widget B", "qty": 5},
        {"description": "Widget A", "qty": 4},  # short by 6, not 10
    ]
    result = await backorder_run(_ctx(invoice_lines=invoice_lines))
    assert result.data["detected"] is True
    [entry] = result.data["missing_qty_by_item"]
    assert entry["description"] == "Widget A"
    assert entry["remaining"] == 6
    assert entry["missing_line"] is False


async def test_po_line_with_no_matching_invoice_line_is_flagged_distinctly():
    invoice_lines = [{"description": "Widget A", "qty": 10}]  # Widget B never invoiced at all
    result = await backorder_run(_ctx(invoice_lines=invoice_lines))
    assert result.data["detected"] is True
    [entry] = result.data["missing_qty_by_item"]
    assert entry["description"] == "Widget B"
    assert entry["missing_line"] is True
    assert entry["invoice_qty"] is None
    assert entry["reason"] == "NO_MATCHING_INVOICE_LINE"


async def test_no_descriptions_falls_back_to_positional_matching():
    """Preserves the pre-fix behavior when the invoice genuinely has no
    line descriptions to compare against."""
    invoice_lines = [{"qty": 10}, {"qty": 5}]
    result = await backorder_run(_ctx(invoice_lines=invoice_lines))
    assert result.data["detected"] is False
