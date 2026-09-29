from app.ap_skills.agent_validation_response import _line_matching
from app.ap_skills.form_lines import decode_form_line_items
from app.ap_skills.po_match import _stamp_invoice_line_scores

_PO_LINE_JSON = (
    '[{"2z2Rh5MpXEaiHSaWlMThr":"1","eEpfRP5JIbS8aFle8J615":"NET-RTR-220",'
    '"ZpY63z5PRSjClud4PDpKV":"Network Edge Router","hy5p0sTmR4l7MkX5sWIuE":"EA",'
    '"eewd3Jx-Kx1ub1ZcjBt7L":"2","STqVWjmFqexaezHTRAkFG":"250","gRh9236whOB_ri9TtFaKq":"500"},'
    '{"2z2Rh5MpXEaiHSaWlMThr":"2","eEpfRP5JIbS8aFle8J615":"NET-SW-140",'
    '"ZpY63z5PRSjClud4PDpKV":"Managed Network Switch","hy5p0sTmR4l7MkX5sWIuE":"EA",'
    '"eewd3Jx-Kx1ub1ZcjBt7L":"3","STqVWjmFqexaezHTRAkFG":"300","gRh9236whOB_ri9TtFaKq":"900"}]'
)

_CONTROLS = [
    {"name": "Line", "json_id": "2z2Rh5MpXEaiHSaWlMThr"},
    {"name": "Item", "json_id": "eEpfRP5JIbS8aFle8J615"},
    {"name": "Description", "json_id": "ZpY63z5PRSjClud4PDpKV"},
    {"name": "UOM", "json_id": "hy5p0sTmR4l7MkX5sWIuE"},
    {"name": "Quantity", "json_id": "eewd3Jx-Kx1ub1ZcjBt7L"},
    {"name": "Unit Price", "json_id": "STqVWjmFqexaezHTRAkFG"},
    {"name": "Amount", "json_id": "gRh9236whOB_ri9TtFaKq"},
]


def test_decode_form_line_items_without_control_names():
    raw = (
        '[{"2z2Rh5MpXEaiHSaWlMThr":"1","eEpfRP5JIbS8aFle8J615":"ICP1001",'
        '"8nVIWBIeCFM6wgC7JOlzL":"A","ZpY63z5PRSjClud4PDpKV":"Industrial Control Panel",'
        '"hy5p0sTmR4l7MkX5sWIuE":"EA","ja59TImIXkfIm_EIy2dxJ":"13%",'
        '"eewd3Jx-Kx1ub1ZcjBt7L":"6","STqVWjmFqexaezHTRAkFG":"254",'
        '"gRh9236whOB_ri9TtFaKq":"1524","Ywg9Bc_J8IyRglLcnrAWl":"2026-06-20",'
        '"kXPikEE9xLRxtpE9lGwFo":"3.30","JXmxAE-HiQMv119GGn5N6":"6100-AP"},'
        '{"2z2Rh5MpXEaiHSaWlMThr":"2","eEpfRP5JIbS8aFle8J615":"PDU2001",'
        '"ZpY63z5PRSjClud4PDpKV":"Power Distribution Unit","hy5p0sTmR4l7MkX5sWIuE":"EA",'
        '"eewd3Jx-Kx1ub1ZcjBt7L":"9","STqVWjmFqexaezHTRAkFG":"270","gRh9236whOB_ri9TtFaKq":"2430"},'
        '{"2z2Rh5MpXEaiHSaWlMThr":"3","eEpfRP5JIbS8aFle8J615":"NER3001",'
        '"ZpY63z5PRSjClud4PDpKV":"Network Equipment Rack","hy5p0sTmR4l7MkX5sWIuE":"EA",'
        '"eewd3Jx-Kx1ub1ZcjBt7L":"3","STqVWjmFqexaezHTRAkFG":"217","gRh9236whOB_ri9TtFaKq":"651"}]'
    )
    lines = decode_form_line_items(raw, [])
    assert [row["description"] for row in lines] == [
        "Industrial Control Panel",
        "Power Distribution Unit",
        "Network Equipment Rack",
    ]
    assert lines[0]["qty"] == "6"
    assert lines[0]["price"] == "254"
    assert lines[0]["amount"] == "1524"
    assert lines[1]["amount"] == "2430"
    assert lines[2]["qty"] == "3"


def test_decode_form_line_items_maps_control_ids():
    lines = decode_form_line_items(_PO_LINE_JSON, _CONTROLS)
    assert len(lines) == 2
    assert lines[0]["description"] == "Network Edge Router"
    assert lines[0]["qty"] == "2"
    assert lines[0]["price"] == "250"
    assert lines[0]["amount"] == "500"
    assert lines[1]["description"] == "Managed Network Switch"
    assert lines[1]["amount"] == "900"


def test_invoice_lines_get_match_score_and_total():
    invoice = {
        "line_items": [
            {"qty": 2, "price": 250.0, "amount": 500.0, "description": "Network Edge Router"},
            {"qty": 3, "price": 300.0, "amount": 900.0, "description": "Managed Network Switch"},
        ]
    }
    po = {"lines": decode_form_line_items(_PO_LINE_JSON, _CONTROLS)}
    assert _line_matching(invoice, po)[0]["Line Score"] == 100
    _stamp_invoice_line_scores(invoice, po)
    assert invoice["line_items"][0]["Score"] == 100
    assert invoice["line_items"][0]["total"] == 500.0
    assert invoice["line_items"][1]["Score"] == 100
    assert invoice["line_items"][1]["total"] == 900.0
