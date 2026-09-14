"""Tests for agent_data_validation narrative fields (ai_insight / reason / source_type)."""
from app.ap_skills.agent_validation_text import (
    build_ai_insight,
    build_validation_reason,
    enrichment_for_finalize,
    resolve_source_type,
)


def test_source_type_hana_from_lookup():
    artifacts = {
        "po_lookup_sap": {
            "source": "hana",
            "po": {"po_number": "4500063646", "source": "hana_cloud"},
        }
    }
    assert resolve_source_type(artifacts) == "HANA Cloud"


def test_source_type_sap_sample():
    artifacts = {
        "po_lookup_sap": {
            "source": "sap",
            "po": {"po_number": "PO-60001", "source": "sap_sample"},
        }
    }
    assert resolve_source_type(artifacts) == "SAP"


def test_source_type_ezofis_db_from_po_match():
    artifacts = {
        "po_match": {
            "po": {"po_number": "PO-1", "ezfb_table": "ezfb_abc_items", "form_id": "f1"},
        }
    }
    assert resolve_source_type(artifacts) == "EZOFIS DB"


def test_ai_insight_matched_approve():
    insight = build_ai_insight(
        decision="MATCHED",
        po_match={"reason": "PO 1 found. Vendor matches PO. Totals match within tolerance.", "po": {}},
        vendor={"status": "ACTIVE"},
    )
    assert "approve for posting" in insight.lower()


def test_ai_insight_not_matched_hold():
    insight = build_ai_insight(decision="NOT_MATCHED", po_match={"reason": "PO not found."}, vendor={})
    assert "hold" in insight.lower() or "manual" in insight.lower()


def test_validation_reason_includes_score_and_lines():
    reason = build_validation_reason(
        decision="PARTIALLY_MATCHED",
        base_reason="PO found. Vendor matches PO. Totals match within tolerance.",
        artifacts={
            "po_match": {
                "score": 90,
                "reason": "PO found. Vendor matches PO. Totals match within tolerance.",
                "po": {
                    "lines": [
                        {"description": "Widget", "qty": 10},
                        {"description": "Gadget", "qty": 2},
                    ]
                },
            },
            "vendor_validate": {"status": "ACTIVE"},
        },
        invoice={
            "line_items": [
                {"description": "Widget", "qty": 10},
                {"description": "Gadget", "qty": 5},
            ]
        },
    )
    assert "90%" in reason
    assert "vendor" in reason.lower()
    assert "lines matched" in reason.lower()
    assert "quantity" in reason.lower()


def test_enrichment_bundle_keys():
    out = enrichment_for_finalize(
        decision="MATCHED",
        reason="PO found. Vendor matches PO. Totals match within tolerance.",
        artifacts={
            "po_lookup_sap": {"source": "hana", "po": {"source": "hana_cloud"}},
            "po_match": {
                "score": 100,
                "reason": "PO found. Vendor matches PO. Totals match within tolerance.",
                "po": {"source": "hana_cloud", "lines": [{"description": "A", "qty": 1}]},
            },
            "vendor_validate": {"status": "ACTIVE"},
        },
        invoice={"line_items": [{"description": "A", "qty": 1}]},
        document_job={"resource": "HANA"},
    )
    assert out["source_type"] == "HANA Cloud"
    assert out["ai_insight"]
    assert out["reason"]
