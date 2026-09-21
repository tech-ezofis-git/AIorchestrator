"""UUID/Decimal payloads must serialize for move-next httpx json=."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.integrations.ezofis_client import _json_safe


def test_json_safe_uuid_decimal_datetime_nested():
    payload = {
        "activityid": "DR97uPaylMtwahvi3XYr_",
        "AIAGENTResponse": {
            "po_row": {
                "Id": UUID("1e16dd88-28b9-4da4-b577-eb0e8f0c0001"),
                "PO Amount": Decimal("5203.65"),
                "PO Date": date(2026, 5, 20),
            },
            "run_id": UUID("34004efe-b3df-4d1f-b055-352633adce09"),
        },
        "when": datetime(2026, 9, 21, 12, 0, 0),
    }
    safe = _json_safe(payload)
    encoded = json.dumps(safe)
    assert "1e16dd88-28b9-4da4-b577-eb0e8f0c0001" in encoded
    assert "5203.65" in encoded
    parsed = json.loads(encoded)
    assert parsed["AIAGENTResponse"]["po_row"]["Id"] == "1e16dd88-28b9-4da4-b577-eb0e8f0c0001"
    assert parsed["AIAGENTResponse"]["po_row"]["PO Amount"] == 5203.65
