"""workflow_move_next treats Core 'not advanced' responses as failure."""
import pytest

from app.ap_skills.types import ApContext
from app.ap_skills import workflow_move_next as move_mod


class _FakeEzofis:
    def __init__(self, result):
        self._result = result

    async def workflow_move_next(self, **kwargs):
        return self._result


@pytest.mark.asyncio
async def test_move_next_marks_not_advanced_as_failure(monkeypatch):
    async def fake_activity(ctx, job, workflow_id):
        return "AP_ACTIVITY"

    monkeypatch.setattr(move_mod, "_resolve_activity_id", fake_activity)

    ctx = ApContext(
        tenant_id="t1",
        item_key="item-1",
        run_id="run-1",
        session_id="s1",
        invoice_json={"doc_type": "invoice", "po_number": "PO-1"},
        artifacts={
            "finalize_decision": {
                "decision": "PARTIALLY_MATCHED",
                "reason": "Vendor differs.",
                "used_mock_data": False,
            }
        },
        settings=type("S", (), {})(),
        ezofis=_FakeEzofis(
            {
                "ok": True,
                "success": True,
                "message": "AP agent review recorded; workflow not advanced (review is not Approve).",
            }
        ),
        document_job={
            "instance_id": "inst-1",
            "workflow_id": "wf-1",
            "activity_id": "AP_ACTIVITY",
        },
    )
    result = await move_mod.run(ctx)
    assert result.data["ok"] is False
    assert "not advanced" in str(result.data.get("reason") or "").lower()
