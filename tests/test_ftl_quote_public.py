"""Public Quote 2 / Quote 3 / compare envelope — API flatten only, no model call."""

from app.ftl.quote_estimator.quote_public import TRACE_PUBLIC, public_quote_payload


def _line(category, code, qty, price, **extra):
    row = {
        "product_code": code,
        "description": extra.pop("description", code),
        "category": category,
        "qty": qty,
        "unit_price": price,
    }
    row.update(extra)
    return row


def _quote1(*, detector=False, adaptor=True):
    items = [
        _line("door_operator", "SGV2_DOOR_OP_1S42", 1, 4000),
        _line("clutch", "SGV2_CLUTCH_INTERLOCK", 1, 300),
    ]
    if adaptor:
        items.append(_line("panel_adaptor", "SGV2(1S)_PANEL_ADAPTOR_GAL42_LH", 1, 200))
    if detector:
        items.append(_line("door_protective_device", "FS_VISIONPLUS_3D_CAR_DOOR_DET", 1, 1295))
    return {
        "estimate_number": "EST-900201",
        "project_name": "Monfort Hospital Cars 5 & 6",
        "customer_name": "ATTA Elevators",
        "contact_name": "Kevin Beerli",
        "contact_phone": "613-555-0142",
        "freight_estimate": 450.0,
        "line_items": items,
        "remarks": "Quoted from the RFQ.",
        "assumptions": [],
    }


GAL = (
    "Replace the SGV2 door operator on one car, single speed 42 in. "
    "Convert the landing locks to GAL. Existing car doors remain. Ship to Toronto, ON."
)
PLAIN = (
    "Replace the SGV2 door operator on one car, single speed 42 in. "
    "Existing car doors remain. Ship to Toronto, ON. 3D door-edge detector required."
)
ALT = (
    "Base bid: retain the existing car doors. Alternate: supply new universal car doors. "
    "Replace the SGV2 door operator on one car, single speed 42 in. "
    "3D detector required. Ship to Toronto, ON."
)


def test_quote_result_stays_quote_1_and_header_is_not_copied_to_variants():
    public = public_quote_payload(_quote1(), candidate_text=GAL)
    q1 = public["quote_result"]
    assert q1["Order Number"] == "EST-900201"
    assert q1["Project"] == "Monfort Hospital Cars 5 & 6"
    assert q1["Company Name"] == "ATTA Elevators"
    assert "quote_2 Project" not in public["quote_2"]
    assert "quote_2 Company Name" not in public["quote_2"]
    assert "Line Item" in q1
    trace = q1["Line Item"][0][TRACE_PUBLIC]
    assert trace["Why"]
    assert trace["Quantity Source"]
    assert trace["Rule"]
    assert "Price Source" in trace


def test_gal_lock_conversion_emits_prefixed_quote_2_diffs_only():
    public = public_quote_payload(_quote1(), candidate_text=GAL)
    q2 = public["quote_2"]
    assert q2["quote_2 Status"] == "GENERATED"
    assert "quote_2 Line Item" in q2
    assert "quote_2 Hst" in q2
    assert "quote_2 Subtotal" in q2
    assert "quote_2 Total" in q2
    cats = [it["Category"] for it in q2["quote_2 Line Item"]]
    assert "Car Door Panel" in cats
    assert "Panel Adaptor" not in cats
    panel = next(it for it in q2["quote_2 Line Item"] if it["Category"] == "Car Door Panel")
    assert panel[TRACE_PUBLIC]["Why"]
    assert panel[TRACE_PUBLIC]["Rule"]
    assert "DOOR-004" in panel[TRACE_PUBLIC]["Rule"] or "PANEL-001" in panel[TRACE_PUBLIC]["Rule"]
    compare = public["compare"]["quote_2"]
    assert compare is not None
    assert "Line Item" in compare
    assert "Hst" in compare
    assert "From" in compare["Hst"] and "To" in compare["Hst"]
    added = compare["Line Item"].get("Added") or []
    added_codes = [it.get("Product") for it in added]
    assert any("VISIONPLUS" in str(c) or "UNIVERSAL" in str(c) for c in added_codes)
    assert any(it.get(TRACE_PUBLIC) for it in added)


def test_no_approved_upgrade_keeps_quote_2_status_only():
    public = public_quote_payload(_quote1(detector=True, adaptor=False), candidate_text=PLAIN)
    q2 = public["quote_2"]
    assert q2["quote_2 Status"] == "NO VALID RECOMMENDED VARIANT"
    assert "quote_2 Line Item" not in q2
    assert "quote_2 Hst" not in q2
    assert public["compare"]["quote_2"] is None


def test_quote_3_alternate_and_no_third():
    none = public_quote_payload(_quote1(detector=True), candidate_text=PLAIN)
    assert none["quote_3"]["quote_3 Status"] == "NO VALID THIRD OPTION"
    assert none["compare"]["quote_3"] is None
    assert list(none["quote_3"].keys()) == ["quote_3 Status", "quote_3 Reason", "quote_3 Rules"]

    alt = public_quote_payload(_quote1(detector=True), candidate_text=ALT)
    q3 = alt["quote_3"]
    assert q3["quote_3 Status"] == "GENERATED"
    assert "quote_3 Line Item" in q3
    assert "quote_3 Hst" in q3
    assert alt["compare"]["quote_3"] is not None
    assert "Line Item" in alt["compare"]["quote_3"]
