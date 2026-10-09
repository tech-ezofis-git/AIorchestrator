"""Tests for Content-Based Duplicate EML Detection in the FTL Agent."""

import json
import pytest
from unittest.mock import MagicMock, patch

from app.ftl.laya_comparator import (
    DuplicateCheckResult,
    LayaContentComparator,
    clean_email_body,
    compute_content_fingerprint,
    compute_lexical_similarity,
    normalize_address_list,
    normalize_email_address,
    normalize_subject,
    normalize_whitespace,
    parse_and_extract_eml,
)
from app.ftl.qualifier import runs_store


# Sample raw EML contents for testing
SAMPLE_EML_A = b"""From: "John Doe" <john.doe@modernization.com>
To: rfq@ftlelevators.com, quotes@ftlelevators.com
Cc: info@elevatorconsulting.com
Subject: [EXTERNAL] RFQ: Bloor Street Modernization Tender #4021
Date: Mon, 12 Oct 2026 10:00:00 +0000
Message-ID: <msg-001@modernization.com>
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Please provide your quotation for the following elevator modernization equipment:
1. Wittur Hydra Plus 2-Panel Center Opening Door Operator (42 inch opening)
2. Car door clutch and restrictors
3. Heavy duty roller guides

Project: Bloor St Modernization
Deadline: October 30, 2026
"""

# Same content as A, but with different headers formatting, extra spaces, Re: prefix, different filename
SAMPLE_EML_A_FORMATTED = b"""From: john.doe@modernization.com
To: quotes@ftlelevators.com, rfq@ftlelevators.com
Cc: info@elevatorconsulting.com
Subject: Re: [EXTERNAL] RFQ: Bloor Street Modernization Tender #4021
Date: Mon, 12 Oct 2026 10:05:00 +0000
Message-ID: <msg-001-fwd@modernization.com>
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html>
<body>
<p>Please provide your quotation for the following elevator modernization equipment:</p>
<p>1. Wittur Hydra Plus 2-Panel Center Opening Door Operator (42 inch opening)<br/>
2. Car door clutch and restrictors<br/>
3. Heavy duty roller guides</p>
<p>Project: Bloor St Modernization<br/>
Deadline: October 30, 2026</p>
</body>
</html>
"""

# Slightly modified content (minor wording / updated date)
SAMPLE_EML_A_SLIGHTLY_MODIFIED = b"""From: "John Doe" <john.doe@modernization.com>
To: rfq@ftlelevators.com
Subject: RFQ: Bloor Street Modernization Tender #4021 - Revised
Date: Tue, 13 Oct 2026 09:00:00 +0000
Content-Type: text/plain; charset="utf-8"

Please provide your revised quote for the Bloor St elevator modernization:
1. Wittur Hydra Plus 2-Panel Center Opening Door Operator (42 inch opening)
2. Car door clutch and restrictor package
3. Heavy duty roller guides

Project: Bloor St Modernization
Updated Deadline: November 05, 2026
"""

# Completely different content
SAMPLE_EML_B = b"""From: "Sarah Connor" <s.connor@skylineelevators.org>
To: quotes@ftlelevators.com
Subject: New Construction RFQ: Apex Tower Hydraulic Elevator Package
Date: Wed, 14 Oct 2026 14:30:00 +0000
Content-Type: text/plain; charset="utf-8"

We require pricing for a brand new 4-stop hydraulic elevator passenger package:
- Submersible power unit 25HP
- Telescopic car sling and platform
- Premium stainless steel cab finish

Location: 500 King St West, Toronto
Bid Due: December 01, 2026
"""


def test_eml_extraction_and_cleaning():
    """Verify parsing extracts subject, sender, recipients, and cleaned body."""
    parsed = parse_and_extract_eml(SAMPLE_EML_A, filename="rfq_bloor.eml")
    assert parsed["from"] == "john.doe@modernization.com"
    assert "rfq@ftlelevators.com" in parsed["to"]
    assert "quotes@ftlelevators.com" in parsed["to"]
    assert "info@elevatorconsulting.com" in parsed["cc"]
    assert "bloor street modernization tender #4021" in parsed["subject"]
    assert "Wittur Hydra Plus" in parsed["body_text"]
    assert parsed["content_fingerprint"]


def test_eml_normalization_consistency():
    """Verify different formatting and encoding produces identical normalized content and fingerprint."""
    parsed_a = parse_and_extract_eml(SAMPLE_EML_A, filename="original.eml")
    parsed_formatted = parse_and_extract_eml(SAMPLE_EML_A_FORMATTED, filename="different_name.eml")

    assert parsed_a["from"] == parsed_formatted["from"]
    assert parsed_a["to"] == parsed_formatted["to"]
    assert parsed_a["subject"] == parsed_formatted["subject"]
    # Both bodies should match after cleaning/normalization
    assert parsed_a["content_fingerprint"] == parsed_formatted["content_fingerprint"]


def test_exact_duplicate_different_filename():
    """Requirement: If two files have identical normalized content but different filenames, identify them as duplicates."""
    parsed_1 = parse_and_extract_eml(SAMPLE_EML_A, filename="inbox_email_123.eml")
    parsed_2 = parse_and_extract_eml(SAMPLE_EML_A_FORMATTED, filename="tender_upload_xyz.eml")

    comparator = LayaContentComparator()
    res = comparator.compare_emails_sync(parsed_1, parsed_2)

    assert res.is_duplicate is True
    assert res.match_type == "exact_hash"
    assert res.similarity_score == 1.0
    assert "fingerprint match" in res.reason.lower()


def test_same_filename_different_content():
    """Requirement: Same filename + different content → Do not classify as automatic duplicate."""
    parsed_1 = parse_and_extract_eml(SAMPLE_EML_A, filename="rfq_upload.eml")
    parsed_2 = parse_and_extract_eml(SAMPLE_EML_B, filename="rfq_upload.eml")

    comparator = LayaContentComparator()
    res = comparator.compare_emails_sync(parsed_1, parsed_2)

    assert res.is_duplicate is False
    assert res.match_type == "distinct"
    assert res.similarity_score < 0.5


def test_slightly_modified_content_laya_model():
    """Requirement: Slightly modified email content with LLM fallback → Use similarity comparison."""
    parsed_orig = parse_and_extract_eml(SAMPLE_EML_A, filename="bloor_v1.eml")
    parsed_mod = parse_and_extract_eml(SAMPLE_EML_A_SLIGHTLY_MODIFIED, filename="bloor_v2.eml")

    # Mock Laya LLM response
    mock_laya_reply = json.dumps({
        "similarity_score": 0.85,
        "is_duplicate": False,
        "is_potential_duplicate": True,
        "match_type": "possible_duplicate_for_review",
        "reason": "Email represents a revised version of the Bloor St elevator modernization tender with an updated deadline.",
        "differences": ["Updated deadline from Oct 30 to Nov 05", "Slight phrasing change in greeting"],
        "confidence": 0.92,
    })

    with patch("app.ftl.llm.open_client") as mock_open:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = mock_laya_reply
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        mock_open.return_value = (mock_client, "gpt-4.1-nano")

        comparator = LayaContentComparator()
        res = comparator.compare_emails_sync(parsed_mod, parsed_orig, llm_overrides={"model": "gpt-4.1-nano"})

        assert res.is_potential_duplicate is True
        assert res.match_type == "possible_duplicate_for_review"
        assert res.similarity_score == 0.85
        assert "Bloor St elevator modernization" in res.reason


def test_native_laya_model_prediction():
    """Requirement: Native Laya decision model evaluates RFQ revision directly without API calls."""
    parsed_orig = parse_and_extract_eml(SAMPLE_EML_A, filename="bloor_v1.eml")
    parsed_mod = parse_and_extract_eml(SAMPLE_EML_A_SLIGHTLY_MODIFIED, filename="bloor_v2.eml")

    comparator = LayaContentComparator()
    res = comparator.compare_emails_sync(parsed_mod, parsed_orig)

    assert res.is_potential_duplicate is True
    assert res.match_type in ("possible_duplicate_for_review", "semantic_duplicate")
    assert res.similarity_score >= 0.75



def test_malformed_eml_graceful_handling():
    """Requirement: Handle malformed EML files and missing fields gracefully."""
    malformed_bytes = b"NOT A VALID MIME EMAIL HEADER\x00\xff\xfe Some random text"
    parsed = parse_and_extract_eml(malformed_bytes, filename="bad.eml")
    assert parsed["content_fingerprint"] is not None
    assert isinstance(parsed["attachments"], list)

    comparator = LayaContentComparator()
    res = comparator.compare_emails_sync(parsed, parsed)
    assert res.is_duplicate is True


def test_runs_store_duplicate_prevention(tmp_path, monkeypatch):
    """Requirement: Store content fingerprint, check existing records, prevent duplicate runs."""
    monkeypatch.setattr("app.ftl.qualifier.runs_store.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.RUNS_PATH", str(tmp_path / "runs.json"))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.UPLOADS_DIR", str(tmp_path / "uploads"))

    # Initially empty
    assert len(runs_store.load_runs()) == 0

    parsed_1 = parse_and_extract_eml(SAMPLE_EML_A, filename="first_submission.eml")
    run_1 = runs_store.append_run(
        input_filename="first_submission.eml",
        input_type="eml",
        candidate_text="Sample candidate text",
        decision={"qualify": "qualify", "project_name": "Bloor St Modernization"},
        total_tokens=500,
        content_fingerprint=parsed_1["content_fingerprint"],
        normalized_content=parsed_1["normalized_body"],
        email_metadata=parsed_1,
    )

    assert run_1["id"] == 1
    assert run_1["content_fingerprint"] == parsed_1["content_fingerprint"]

    # Verify lookup by fingerprint
    found = runs_store.find_run_by_fingerprint(parsed_1["content_fingerprint"])
    assert found is not None
    assert found["id"] == 1

    # Second upload with different filename but identical normalized content
    parsed_2 = parse_and_extract_eml(SAMPLE_EML_A_FORMATTED, filename="second_upload_different_name.eml")
    comparator = LayaContentComparator()
    dup_res = comparator.find_duplicate_in_runs(parsed_2, runs_store.load_runs())

    assert dup_res.is_duplicate is True
    assert dup_res.match_type == "exact_hash"
    assert dup_res.matched_run_id == 1


def test_api_qualify_exact_duplicate_short_circuit(client, monkeypatch, tmp_path):
    """Requirement: Integrated FTL qualify endpoint detects exact duplicate and skips redundant processing."""
    monkeypatch.setattr("app.ftl.qualifier.runs_store.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.RUNS_PATH", str(tmp_path / "runs.json"))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.UPLOADS_DIR", str(tmp_path / "uploads"))

    mock_decision = {
        "qualify": "qualify",
        "project_type": "modernization",
        "project_name": "Bloor St Modernization",
        "matched_items": [],
        "excluded_items": [],
        "flags": [],
        "confidence": 0.95,
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 1100

    monkeypatch.setattr("app.ftl.qualifier.agent.run_qualification", fake_run_qualification)

    # 1. First upload
    res_1 = client.post(
        "/api/ftl/qualify",
        files={"file": ("inbox_tender.eml", SAMPLE_EML_A, "message/rfc822")},
    )
    assert res_1.status_code == 200
    body_1 = res_1.json()
    assert body_1["status"] == "success"
    run_1_id = body_1["run_id"]

    # 2. Second upload with DIFFERENT filename but SAME content
    # Should detect exact duplicate
    res_2 = client.post(
        "/api/ftl/qualify",
        files={"file": ("re_tender_duplicate.eml", SAMPLE_EML_A_FORMATTED, "message/rfc822")},
    )
    assert res_2.status_code == 200
    body_2 = res_2.json()
    assert body_2["status"] == "success"
    assert body_2.get("duplicate_info") is not None
    assert body_2["duplicate_info"]["is_duplicate"] is True
    assert body_2["duplicate_info"]["match_type"] == "exact_hash"
    assert body_2["duplicate_info"]["matched_run_id"] == run_1_id


def test_api_check_duplicate_endpoint(client, monkeypatch, tmp_path):
    """Test dedicated /api/ftl/eml/check-duplicate endpoint."""
    monkeypatch.setattr("app.ftl.qualifier.runs_store.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.RUNS_PATH", str(tmp_path / "runs.json"))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.UPLOADS_DIR", str(tmp_path / "uploads"))

    # Populate a past run
    parsed = parse_and_extract_eml(SAMPLE_EML_A, filename="original.eml")
    runs_store.append_run(
        input_filename="original.eml",
        input_type="eml",
        candidate_text="Original text",
        decision={"qualify": "qualify"},
        content_fingerprint=parsed["content_fingerprint"],
        normalized_content=parsed["normalized_body"],
        email_metadata=parsed,
    )

    # Check duplicate via API
    res = client.post(
        "/api/ftl/eml/check-duplicate",
        files={"file": ("new_upload.eml", SAMPLE_EML_A_FORMATTED, "message/rfc822")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["is_duplicate"] is True
    assert data["match_type"] == "exact_hash"
    assert data["matched_run_id"] == 1
    assert data["content_fingerprint"] == parsed["content_fingerprint"]


def test_chat_post_ftl_qualifier_multipart_duplicate(client, monkeypatch, tmp_path):
    """Test POST /chat with intent=ftl_qualifier multipart file upload and duplicate detection."""
    monkeypatch.setattr("app.ftl.qualifier.runs_store.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.RUNS_PATH", str(tmp_path / "runs.json"))
    monkeypatch.setattr("app.ftl.qualifier.runs_store.UPLOADS_DIR", str(tmp_path / "uploads"))

    mock_decision = {
        "qualify": "qualify",
        "project_type": "modernization",
        "project_name": "285-295 Coventry - Modernization",
        "matched_items": [
            {
                "item": "clutch assembly",
                "category": "Door Interlock / Clutch",
                "match": "exact",
                "catalog_ref": "SGV2 clutch + car door lock assemblies, model-specific.",
                "note": "FTL stocks clutches c/w car door interlock.",
            }
        ],
        "excluded_items": [],
        "flags": [],
        "confidence": 0.85,
        "reasoning": "Standard modernization scope.",
        "ai_insight": "Good modernization fit.",
    }

    def fake_run_qualification(skill, candidate_text, llm_overrides=None):
        return mock_decision, 1200

    monkeypatch.setattr("app.ftl.qualifier.agent.run_qualification", fake_run_qualification)

    # 1. First upload via POST /chat
    res_1 = client.post(
        "/chat",
        data={
            "session_id": "test-coventry-1",
            "intent": "ftl_qualifier",
        },
        files={
            "file": ("coventry_rfq.eml", SAMPLE_EML_A, "message/rfc822"),
        },
    )
    assert res_1.status_code == 200
    body_1 = res_1.json()
    assert body_1["session_id"] == "test-coventry-1"
    assert "qualifier_result" in body_1 and body_1["qualifier_result"] is not None
    assert body_1["qualifier_result"]["Qualify"] == "Qualify"
    assert body_1["qualifier_result"]["Project"] == "285-295 Coventry - Modernization"

    # 2. Second upload with different filename but identical normalized content via POST /chat
    res_2 = client.post(
        "/chat",
        data={
            "session_id": "test-coventry-1-dup",
            "intent": "ftl_qualifier",
        },
        files={
            "file": ("coventry_rfq_forwarded_copy.eml", SAMPLE_EML_A_FORMATTED, "message/rfc822"),
        },
    )
    assert res_2.status_code == 200
    body_2 = res_2.json()
    assert body_2["session_id"] == "test-coventry-1-dup"
    assert "qualifier_result" in body_2 and body_2["qualifier_result"] is not None
    assert body_2["qualifier_result"]["Qualify"] == "Qualify"
    assert "Duplicate RFQ Detected" in body_2["reply"]
    dup_info = body_2["qualifier_result"].get("Duplicate Info")
    assert dup_info is not None
    assert dup_info["Is Duplicate"] is True
    assert dup_info["Matched Run Id"] == 1
    assert dup_info["Match Type"] in ("exact_hash", "Exact Hash")
    assert dup_info["Similarity Score"] == 1.0
