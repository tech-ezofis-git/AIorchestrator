"""Workflow PO master signals gate HANA/SAP injection."""
from app.ap_skills.hana_po import wants_sap_or_hana_po_master


def test_empty_job_is_internal_form():
    assert wants_sap_or_hana_po_master({}) is False
    assert wants_sap_or_hana_po_master(None) is False


def test_internal_form_master_source():
    assert wants_sap_or_hana_po_master({"master_source": "InternalForm"}) is False
    assert wants_sap_or_hana_po_master({"masterSource": "Ezofis"}) is False
    assert wants_sap_or_hana_po_master({"resource": "InternalForm"}) is False


def test_sap_and_hana_signals():
    assert wants_sap_or_hana_po_master({"resource": "SAP"}) is True
    assert wants_sap_or_hana_po_master({"resource": "HANA"}) is True
    assert wants_sap_or_hana_po_master({"master_source": "SAP"}) is True
    assert wants_sap_or_hana_po_master({"masterSource": "HANA"}) is True


def test_other_connectors_skip_hana():
    assert wants_sap_or_hana_po_master({"resource": "QUICKBOOKS"}) is False
    assert wants_sap_or_hana_po_master({"master_source": "QuickBooks"}) is False
    assert wants_sap_or_hana_po_master({"resource": "SAGE"}) is False
