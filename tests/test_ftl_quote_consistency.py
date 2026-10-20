"""Quote 1 consistency (145 Richmond).

The same thin email used to produce three different Quote 1s (with / without
universal car doors, with / without a detector), so Quote 2 appeared and vanished.
Quote 1 now carries only what the RFQ supports; unread survey photos are a HOLD.
"""
from __future__ import annotations

import copy

from app.ftl.quote_estimator import agent, extract
from app.ftl.quote_estimator import quote_controls as qc
from app.ftl.quote_estimator import quote_variants as qv


def _line(category, code, qty, price, origin="SPEC", note=""):
    return {
        "product_code": code,
        "description": code,
        "category": category,
        "qty": qty,
        "unit_price": price,
        "origin": origin,
        "note": note,
    }


def _draft(lines):
    return {
        "customer_name": "March Elevator",
        "contact_name": "Mark Minnick",
        "contact_phone": "(416) 533-1135",
        "billing_address": "1025 Stacey Court, Mississauga, ON L4W 2X7",
        "shipping_address": "1025 Stacey Court, Mississauga, ON L4W 2X7",
        "project_name": "145 Richmond Ave W",
        "line_items": lines,
    }


BASE = [
    _line("door_operator", "SGV2_DOOR_OP_2C42_L", 1, 5730.32),
    _line("clutch", "SGV2_CLUTCH_G.M.D._L", 1, 1161.94),
    _line("other", "SGV2_CAR_DOOR_RESTRICTOR", 1, 0, origin="FTL_RULE"),
    _line("door_tools", "SGV2_DOOR_TOOLS", 1, 698.5),
]
PANEL = _line("car_door_panel", "2C_UNIVERSAL_CAR_DOOR_PANEL - 42 X 84 (2-DOOR/SET)", 2, 1705.2)
DET = _line(
    "door_protective_device",
    "FS_VISIONPLUS_3D_CAR_DOOR_DET",
    1,
    1295,
    note="Only include if the survey explicitly requires a 3D door-edge detector.",
)

THIN_EMAIL = (
    "## Email\nFrom: John Lewis <john@ftl-distribution.ca>\nSubject: FW: 145 Richmond Ave W - freight car door operator\n\n"
    "HI Scott can you please quote the attached survey for a wittur 4C-R operator.\n\n"
    "## Detected structure signal: unknown\n\n## Keyword-proximity excerpts (heading-based targeting found little)\n"
    "Historical 2026 FTL RFQ package: 145 Richmond Avenue West.\n"
)
PHOTOS = [{"filename": f"100001191{i}.jpg", "bytes": b"x" * 3_500_000} for i in (4, 3)] + [
    {"filename": "logo.png", "bytes": b"x" * 9_000}
]
THIN = extract.apply_unread_attachments(THIN_EMAIL, PHOTOS)


def _sig(quote):
    return sorted((i["category"], i["product_code"], i["qty"]) for i in quote["line_items"])


def test_unread_attachments():
    assert extract.has_unread_attachments(THIN) == ["1000011914.jpg", "1000011913.jpg"]
    assert extract.unread_attachments([{"filename": "spec.pdf", "bytes": b"x" * 9_000_000}], "spec.pdf") == []
    assert extract.apply_unread_attachments(THIN_EMAIL, [PHOTOS[2]]) == THIN_EMAIL
    ocr_used = extract.unread_attachments(PHOTOS, ["1000011914.jpg"])
    assert [u["filename"] for u in ocr_used] == ["1000011913.jpg"]


def test_same_quote_every_run():
    drafts = {"A bare": BASE, "B +panels": BASE + [PANEL], "C +panels +detector": BASE + [PANEL, DET]}
    outs = {k: agent.postprocess_quote(_draft(copy.deepcopy(v)), THIN) for k, v in drafts.items()}
    sigs = {k: _sig(o) for k, o in outs.items()}
    assert sigs["A bare"] == sigs["B +panels"] == sigs["C +panels +detector"]
    for k, o in outs.items():
        cats = {i["category"] for i in o["line_items"]}
        assert not (cats & {"door_protective_device", "car_door_panel", "panel_adaptor"}), k
        assert any("Car doors" in h["item"] for h in o["held_items"]), k
        assert any(h["item"] == "Attached files not read" and h["level"] == "HOLD" for h in o["held_items"]), k
        assert o["release_status"].startswith("HOLD"), k
    totals = {round(sum(i["qty"] * i["unit_price"] for i in o["line_items"]), 2) for o in outs.values()}
    assert len(totals) == 1
    for k, o in outs.items():
        rec = qv.derive_recommended(o, THIN)
        assert rec["status"] == qv.STATUS_GENERATED, k
        assert any(i["category"] == "door_protective_device" for i in rec["result"]["line_items"]), k
        alt = qv.derive_alternate(o, rec.get("result"), THIN)
        assert alt["status"] == qv.STATUS_NO_THIRD, k
    again = agent.postprocess_quote(copy.deepcopy(outs["C +panels +detector"]), THIN)
    assert _sig(again) == _sig(outs["C +panels +detector"])
    assert len(again["held_items"]) == len(outs["C +panels +detector"]["held_items"])


def test_spec_that_mentions_a_detector_keeps_it():
    text = (
        "## Email\nFrom: a\nSubject: tender\n\nInstall a new Electronic Door Detector on each car. Replace the car doors with new doors.\n"
    )
    o = agent.postprocess_quote(_draft(copy.deepcopy(BASE + [PANEL, DET])), text)
    cats = {i["category"] for i in o["line_items"]}
    assert "door_protective_device" in cats
    assert "car_door_panel" in cats
    rec = qv.derive_recommended(o, text)
    assert rec["status"] == qv.STATUS_NO_RECOMMENDED


def test_contradictory_doors_are_not_stripped():
    text = "## Email\nFrom: a\nSubject: t\n\nRetain the existing car doors. Provide new universal car doors for all cars.\n"
    o = agent.postprocess_quote(_draft(copy.deepcopy(BASE + [PANEL])), text)
    assert any(i["category"] == "car_door_panel" for i in o["line_items"])
    assert any(i.get("needs_engineering_review") for i in o["line_items"] if i["category"] == "car_door_panel")
    assert not qc.car_door_decision(text)["silent"]
