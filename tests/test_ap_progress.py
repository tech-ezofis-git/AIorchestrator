"""AP Agent instance progress reporter."""
import asyncio

import pytest

from app.ap_skills.ap_progress import (
    FAILED_MESSAGE,
    RUNNER_OWNED_FLAG,
    ApProgressReporter,
    progress_ids,
)
from app.ap_skills.types import ApContext
from app.ap_skills.workflow_progress import run as workflow_progress_run


class _FakeEzofis:
    def __init__(self):
        self.calls = []

    async def report_ap_progress(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "mock": True}


@pytest.mark.asyncio
async def test_progress_ids_read_hangfire_aliases():
    assert progress_ids({"workflowId": "wf", "instanceId": "inst"}) == ("wf", "inst")
    assert progress_ids({}) == ("", "")


@pytest.mark.asyncio
async def test_reporter_disabled_without_ids():
    ez = _FakeEzofis()
    reporter = ApProgressReporter(ezofis=ez, tenant_id="t", workflow_id="", instance_id="")
    await reporter.received()
    await reporter.completed()
    assert ez.calls == []


@pytest.mark.asyncio
async def test_default_skill_track_skips_reading_for_invoice_json():
    ez = _FakeEzofis()
    reporter = ApProgressReporter(ezofis=ez, tenant_id="t", workflow_id="wf", instance_id="inst")
    await reporter.received()
    await reporter.before_skill("extract_invoice", has_invoice_json=True)
    await reporter.before_skill("po_match", has_invoice_json=True)
    await reporter.before_skill("duplicate_detect")
    await reporter.before_skill("vendor_validate")
    await reporter.before_skill("backorder_detect")
    await reporter.before_skill("finalize_decision")
    await reporter.before_skill("workflow_progress")
    await reporter.before_skill("workflow_move_next")
    await reporter.completed()
    stages = [c["stage"] for c in ez.calls]
    assert stages[0] == "RECEIVED"
    assert "READING" not in stages
    assert stages[1] == "EXTRACTING"
    assert stages[-2] == "REVIEWING"
    assert stages[-1] == "COMPLETED"
    percents = [c["percent"] for c in ez.calls]
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert ez.calls[-1]["message"] == "Invoice processed successfully"


@pytest.mark.asyncio
async def test_reading_then_heartbeat_then_extracting():
    ez = _FakeEzofis()
    reporter = ApProgressReporter(
        ezofis=ez,
        tenant_id="t",
        workflow_id="wf",
        instance_id="inst",
        heartbeat_seconds=0.05,
    )
    await reporter.received()
    await reporter.before_skill("extract_invoice", has_invoice_json=False)
    handle = reporter.start_extract_heartbeat()
    await asyncio.sleep(0.12)
    await reporter.stop_extract_heartbeat(handle)
    await reporter.before_skill("po_match")
    stages = [c["stage"] for c in ez.calls]
    assert stages[0] == "RECEIVED"
    assert stages[1] == "READING"
    assert "Recognizing text" in [c["message"] for c in ez.calls]
    assert stages[-1] == "EXTRACTING"
    percents = [c["percent"] for c in ez.calls]
    assert percents == sorted(percents)


@pytest.mark.asyncio
async def test_failed_uses_sanitized_message():
    ez = _FakeEzofis()
    reporter = ApProgressReporter(ezofis=ez, tenant_id="t", workflow_id="wf", instance_id="inst")
    await reporter.received()
    await reporter.failed()
    assert ez.calls[-1]["stage"] == "FAILED"
    assert ez.calls[-1]["message"] == FAILED_MESSAGE
    assert ez.calls[-1]["percent"] == 100


@pytest.mark.asyncio
async def test_progress_error_does_not_raise():
    class Boom:
        async def report_ap_progress(self, **kwargs):
            raise RuntimeError("network")

    reporter = ApProgressReporter(ezofis=Boom(), tenant_id="t", workflow_id="wf", instance_id="inst")
    await reporter.received()
    await reporter.completed()


@pytest.mark.asyncio
async def test_workflow_progress_skill_is_noop_when_runner_owned():
    ez = _FakeEzofis()
    ctx = ApContext(
        tenant_id="t",
        item_key="doc-1",
        run_id="run-1",
        session_id="s-1",
        invoice_json={},
        artifacts={},
        settings=None,
        ezofis=ez,
        document_job={
            RUNNER_OWNED_FLAG: True,
            "workflow_id": "wf",
            "instance_id": "inst",
        },
    )
    result = await workflow_progress_run(ctx)
    assert result.credits == 0
    assert result.data["skipped"] is True
    assert ez.calls == []
