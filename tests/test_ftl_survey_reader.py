"""The Qualifier reads the Wittur SGV survey form the same way the Quote Estimator does."""
from __future__ import annotations

from pathlib import Path

from app.ftl.qualifier import extract as qualifier_extract
from app.ftl.quote_estimator import extract as estimator_extract

PDF = (Path(__file__).parent / "fixtures" / "82_lorne_survey.pdf").read_bytes()


def test_qualifier_reads_filled_survey_answers():
    sv = qualifier_extract.parse_wittur_survey_form(PDF)
    assert sv and sv["filled"] and sv["operator_total"] == 1
    c = sv["configs"]["1"]
    assert (c["door_type"], c["clear_opening_in"], c["clear_height_in"], c["clutch_interface"], c["cab_panel"]) == (
        "1S-L",
        42,
        84,
        "GAL/ECI",
        "0",
    )
    assert sv["detector"]["qty"] == 1 and any("10b" in m for m in sv["missing"])
    email = (
        "## Email\nFrom: x\nSubject: 82 Lorne\n\n## Detected structure signal: unknown\n\n"
        "New Double skin option list from the blank template\n"
    )
    merged = qualifier_extract.apply_survey_form(email, PDF)
    assert "David Nevin" in merged and "New Double skin" not in merged


def test_qualifier_and_estimator_share_survey_facts():
    q = qualifier_extract.parse_wittur_survey_form(PDF)
    e = estimator_extract.parse_wittur_survey_form(PDF)
    assert q["operator_total"] == e["operator_total"]
    assert q["configs"]["1"]["door_type"] == e["configs"]["1"]["door_type"]
    assert q["detector"] == e["detector"]
