"""Wittur SGV survey form (answers typed into the PDF's form fields).

Regression for 82 Lorne: the agent used to see only the blank template and invent
quantities, door type, and new doors from the printed option lists.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.ftl.quote_estimator import agent, extract
from app.ftl.quote_estimator import quote_controls as qc
from app.ftl.quote_estimator import quote_variants as qv

PDF = (Path(__file__).parent / "fixtures" / "82_lorne_survey.pdf").read_bytes()
EMAIL = (
    "## Email\nFrom: John Lewis <john@ftl-distribution.ca>\nSubject: FW: 82 Lorne\n\n"
    "From: Julie House <jhouse@attaelevators.com>\nSubject: 82 Lorne\n\nPlease find survey attached. We will need a quote please.\n"
    "Julie House\n40 North Rivermede Rd., Concord, ON L4K 2H3\n\n## Detected structure signal: unknown\n\n"
    "CH required if new car door panels are required\nSelect the hall door interlocks. New Double skin, powder coat\n"
)


def _line(category, code, qty, price):
    return {"product_code": code, "description": code, "category": category, "qty": qty, "unit_price": price}


def _draft(lines):
    return {
        "customer_name": "ATTA Elevators",
        "contact_name": "Julie House",
        "contact_phone": "(437) 248-5651",
        "billing_address": "40 North Rivermede Rd., Concord, ON L4K 2H3",
        "shipping_address": "40 North Rivermede Rd., Concord, ON L4K 2H3",
        "project_name": "82 Lorne",
        "line_items": lines,
    }


BAD_DRAFT = [
    _line("door_operator", "SGV2_DOOR_OP_2T42_L", 5, 5210.08),
    _line("clutch", "SGV2_CLUTCH_G.M.D._L", 5, 1161.94),
    _line("car_door_panel", "2T_UNIVERSAL_CAR_DOOR_PANEL - 42 X 84 (2-DOOR/SET)", 10, 2077.6),
    _line("door_tools", "SGV2_DOOR_TOOLS", 1, 698.5),
]


def _text_with(facts_mutator=None):
    sv = extract.parse_wittur_survey_form(PDF)
    if facts_mutator:
        facts_mutator(sv)
    section = extract.SURVEY_FORM_MARKER + " — test\n" + extract.SURVEY_FACTS_PREFIX + json.dumps(sv)
    return EMAIL.split("## Detected structure signal")[0] + "\n" + section + "\n"


def _by_cat(quote, cat):
    return [i for i in quote["line_items"] if i["category"] == cat]


def _plain_pdf():
    from io import BytesIO

    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, "Just a normal specification page")
    c.save()
    return buf.getvalue()


def test_reader():
    sv = extract.parse_wittur_survey_form(PDF)
    assert sv["filled"] and sv["operator_total"] == 1
    c = sv["configs"]["1"]
    assert (c["door_type"], c["door_family"], c["hand"], c["clear_opening_in"], c["clear_height_in"]) == (
        "1S-L",
        "1S",
        "L",
        42,
        84,
    )
    assert (c["clutch_interface"], c["mounting"], c["programming_tool_qty"]) == ("GAL/ECI", "Custom", 1)
    assert c["cab_panel"] == "0" and "retain" in extract._panel_meaning(c["cab_panel"])
    assert "cab_panel_interface" not in c and any("10b" in m for m in sv["missing"])
    assert sv["detector"]["type"] == "Vision Plus 2D/3D" and sv["detector"]["qty"] == 1 and sv["controller"] == "MCE"
    assert (sv["contact"]["name"], sv["company"], sv["project"]) == (
        "David Nevin",
        "ATTA Elevators",
        "84 Lorne St - Sudbury",
    )
    assert sv["custom_clutch"] and len(sv["bolt_survey"]) > 10
    section = extract.extract_survey_form_section(PDF)
    assert extract.survey_facts_from_text(section)["operator_total"] == 1
    assert extract.extract_survey_form_section(_plain_pdf()) == ""
    merged = extract.apply_survey_form(EMAIL, PDF)
    assert "New Double skin" not in merged and "CH required" not in merged and "David Nevin" in merged
    assert extract.apply_survey_form(EMAIL, _plain_pdf()) == EMAIL


def test_82_lorne_replay():
    text = extract.apply_survey_form(EMAIL, PDF)
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT)), text)
    ops = _by_cat(out, "door_operator")
    assert len(ops) == 1 and ops[0]["product_code"] == "SGV2_DOOR_OP_1S42_L" and ops[0]["qty"] == 1
    assert [(i["product_code"], i["qty"]) for i in _by_cat(out, "clutch")] == [("SGV2_CLUTCH_G.M.D._L", 1)]
    assert [i["qty"] for i in out["line_items"] if i["product_code"] == "SGV2_CAR_DOOR_RESTRICTOR"] == [1]
    assert [(i["product_code"], i["qty"]) for i in _by_cat(out, "door_protective_device")] == [
        ("FS_VISIONPLUS_3D_CAR_DOOR_DET", 1)
    ]
    assert [(i["qty"], i["unit_price"]) for i in _by_cat(out, "door_tools")] == [(1, 698.5)]
    assert not _by_cat(out, "car_door_panel") and out["car_door_decision"]["decision"] == "retain"
    assert not _by_cat(out, "panel_adaptor") and any(
        h["item"] == "Car door panel adaptor" and "NOT SELECTED" in h["reason"] for h in out["held_items"]
    )
    assert ops[0]["origin"] == "SPEC" and "survey config #1" in ops[0]["note"]
    items = {h["item"] for h in out["held_items"]}
    assert {
        "Operator mounting — survey config #1",
        "Custom clutch survey vs standard clutch interface",
        "Contact differs from the survey",
        "Project address mismatch",
    } <= items
    assert out["freight_estimate"] == 0 and out["freight_basis"]["mode"] == "manual" and "Freight" in items
    assert out["release_status"].startswith("HOLD")
    variants = qv.derive_variants(out, text)
    assert [v["status"] for v in variants] == [qv.STATUS_NO_RECOMMENDED, qv.STATUS_NO_THIRD]
    again = agent.postprocess_quote(copy.deepcopy(out), text)
    assert [(i["product_code"], i["qty"]) for i in again["line_items"]] == [
        (i["product_code"], i["qty"]) for i in out["line_items"]
    ]
    assert any("Survey config #1" in n for n in out["internal_notes"]["confirmed"])
    assert "survey" in "".join(out["internal_notes"]["confirmed"]).lower()


def test_survey_variations():
    t = _text_with(lambda sv: sv["configs"]["1"].update(cab_panel_interface="GAL"))
    out = agent.postprocess_quote(_draft([_line("door_operator", "SGV2_DOOR_OP_1S42_L", 3, 1)]), t)
    ad = _by_cat(out, "panel_adaptor")
    assert [(i["product_code"], i["qty"]) for i in ad] == [("SGV2(1S)_PANEL_ADAPTOR_GAL42_LH", 1)]
    assert not any(h["item"] == "Car door panel adaptor" for h in out["held_items"])

    t = _text_with(lambda sv: sv["configs"]["1"].update(cab_panel="1.25-C"))
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT)), t)
    pn = _by_cat(out, "car_door_panel")
    assert len(pn) == 1 and pn[0]["qty"] == 1 and pn[0]["product_code"].startswith("1S_") and not _by_cat(
        out, "panel_adaptor"
    )
    assert out["car_door_decision"]["decision"] == "replace"

    t = _text_with(lambda sv: sv["detector"].update(type="None", qty=None))
    out = agent.postprocess_quote(
        _draft(copy.deepcopy(BAD_DRAFT) + [_line("door_protective_device", "FS_VISIONPLUS_3D_CAR_DOOR_DET", 5, 1295)]),
        t,
    )
    assert not _by_cat(out, "door_protective_device")

    t = _text_with(lambda sv: sv["configs"]["1"].update(clutch_interface="OTHER"))
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT)), t)
    assert not _by_cat(out, "clutch") and any(h["item"].startswith("Clutch") for h in out["held_items"])

    t = _text_with(lambda sv: sv["configs"]["1"].update(door_type="2C-L", door_family="2C", hand="L"))
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT)), t)
    assert [i["product_code"] for i in _by_cat(out, "door_operator")] == ["SGV2_DOOR_OP_2C42_L"]

    t = _text_with(lambda sv: sv["configs"]["1"].update(door_type="4C-L", door_family="4C", hand="L"))
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT)), t)
    assert not _by_cat(out, "door_operator") and any("Door operator" in h["item"] for h in out["held_items"])


def test_blank_form_is_never_estimated():
    blank = (
        extract.SURVEY_FORM_MARKER
        + " — NOT FILLED IN\n"
        + extract.SURVEY_FACTS_PREFIX
        + json.dumps({"filled": False, "configs": {}, "contact": {}, "project": "", "company": ""})
    )
    text = EMAIL.split("## Detected structure signal")[0] + "\n" + blank + "\n"
    gov = _line("governor", "WG_OL35_GOVERNOR_RC", 1, 1)
    out = agent.postprocess_quote(_draft(copy.deepcopy(BAD_DRAFT) + [gov]), text)
    cats = {i["category"] for i in out["line_items"]}
    assert not (cats & {"door_operator", "clutch", "car_door_panel", "panel_adaptor", "door_protective_device", "door_tools"})
    assert any(h["item"] == "Wittur survey form not filled in" and h["level"] == "HOLD" for h in out["held_items"])
    assert out["release_status"].startswith("HOLD")
    assert any("survey form" in s.lower() for s in out["internal_notes"]["survey_needed"])


def test_not_a_survey_is_untouched():
    text = "## Email\nFrom: a\nSubject: tender\n\nReplace the SGV2 operator on one car.\n"
    q = _draft([_line("door_operator", "SGV2_DOOR_OP_1S42_L", 2, 4955.28)])
    assert qc.enforce_survey_form(copy.deepcopy(q), text) == q
