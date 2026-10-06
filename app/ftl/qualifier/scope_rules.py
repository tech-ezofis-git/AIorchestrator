"""
Shared scope signals — the ONE place that decides, from an RFQ's candidate text (extract.py), which
product categories are actually in scope for FTL to quote.

This file is deliberately IDENTICAL in the Quote Estimator and the Qualifier repos (and has no
dependency on either one — pure text in, plain values out). The Estimator uses it to strip
out-of-scope lines from its quote; the Qualifier uses the very same functions to decide which items
to list as matched. That is what makes "the Qualifier lists exactly the items the Estimator would
quote" a property of the code rather than of two prompts that happen to agree.

Every regex and rule here was moved, unchanged, from the Estimator's agent.py (where the real-RFQ
history that justifies each one is documented next to its enforcement function). If you change
a rule here, change it in both repos — tests/test_scope_parity.py in the Qualifier pins the two
copies together (it compares the file's hash with the Estimator's, when both repos sit side by side).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------------------------
# Equipment scope table (extract.py's "## Equipment scope table" section)
# ---------------------------------------------------------------------------------------------
SCOPE_TABLE_SECTION_RE = re.compile(
    r"## Equipment scope table.*?\n(.*?)(?:\n##|\Z)", re.IGNORECASE | re.DOTALL
)
SCOPE_TABLE_NEW_RE = re.compile(r"^\s*new\b", re.IGNORECASE)

# Category keywords (Estimator `category` enum) that, when the scope table's row for them says
# something other than New, mean "not new scope".
SCOPE_TABLE_CATEGORY_ROW_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "roller_guide": ("car guide", "guide rail", "counterweight guide"),
}

# Categories that fail by ABSENCE: the table never mentions them (a real, client-confirmed quote —
# EST-261132 — had no "Car Safety" row at all). Only meaningful once the table has enough rows.
SCOPE_TABLE_MIN_ROWS_FOR_ABSENCE_CHECK = 6
ABSENCE_CATEGORY_LABEL_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "car_safety": ("safet",),
}

# Counterweight tension weight / swingarm / idler assemblies are never their own scope-table row and
# were wrongly added in every real test run; stripped by keyword whenever a real scope table exists.
SUSPECT_KEYWORD_LINE_ITEMS: Tuple[Tuple[str, ...], ...] = (
    ("swingarm",),
    ("tension", "sheave"),
    ("tension", "idler"),
)

GOVERNOR_SCOPE_ROW_KEYWORDS = ("governor",)


def item_matches_suspect_keywords(text: str) -> bool:
    text = (text or "").lower()
    return any(all(kw in text for kw in group) for group in SUSPECT_KEYWORD_LINE_ITEMS)


def parse_scope_table_rows(scope_text: str) -> List[Tuple[str, str]]:
    """The scope table is a flat list of alternating label/status lines (e.g. "Car guides" then
    "Refurbish") once the "Equipment"/"Scope" header pair is skipped."""
    lines = [l.strip() for l in (scope_text or "").splitlines() if l.strip()]
    rows: List[Tuple[str, str]] = []
    i = 0
    while i < len(lines):
        label = lines[i]
        if label.lower() in ("equipment", "scope", "equipment specifications"):
            i += 1
            continue
        if i + 1 < len(lines):
            rows.append((label, lines[i + 1]))
            i += 2
        else:
            break
    return rows


def scope_table_rows(candidate_text: str) -> List[Tuple[str, str]]:
    """The parsed (label, status) rows of the RFQ's Equipment scope table; [] when there is none."""
    m = SCOPE_TABLE_SECTION_RE.search(candidate_text or "")
    if not m:
        return []
    return parse_scope_table_rows(m.group(1))


def scope_table_excluded_categories(candidate_text: str) -> Dict[str, str]:
    """{estimator_category: "label: status" | absence reason} for every category the scope table
    explicitly rules out of NEW scope. Empty when the RFQ has no scope table (never fires then)."""
    rows = scope_table_rows(candidate_text)
    if not rows:
        return {}
    excluded: Dict[str, str] = {}
    for category, keywords in SCOPE_TABLE_CATEGORY_ROW_KEYWORDS.items():
        for label, status in rows:
            if any(kw in label.lower() for kw in keywords) and not SCOPE_TABLE_NEW_RE.match(status):
                excluded[category] = f"{label}: {status}"
                break
    if len(rows) >= SCOPE_TABLE_MIN_ROWS_FOR_ABSENCE_CHECK:
        for category, keywords in ABSENCE_CATEGORY_LABEL_KEYWORDS.items():
            if category in excluded:
                continue
            if not any(any(kw in label.lower() for kw in keywords) for label, _status in rows):
                excluded[category] = (
                    f"no row for this category anywhere in the table (table lists {len(rows)} "
                    "other equipment categories)"
                )
    return excluded


def scope_table_confirms_new(candidate_text: str, keywords: Tuple[str, ...]) -> bool:
    """True only when the table has a row matching one of `keywords` whose status starts with New —
    a positive confirmation, not merely "the table didn't say Refurbish"."""
    for label, status in scope_table_rows(candidate_text):
        if any(kw in label.lower() for kw in keywords) and SCOPE_TABLE_NEW_RE.match(status):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# Governors — opt-in. Deliberately does NOT match the generic "safeties, governors... as required"
# weight-margin clause, nor "governor and idler sheaves" / "governor shall be labelled" boilerplate,
# which describe EXISTING equipment.
# ---------------------------------------------------------------------------------------------
GOVERNOR_EXPLICIT_SCOPE_RE = re.compile(
    r"(install|provide|replace)\s+(a\s+|the\s+|one\s+|two\s+)*(new\s+)?"
    r"(remote[- ]control\s+|manual[- ]trip\s+|encoder\s+)?governor\b|governor\s+replacement|new\s+governor\b"
    r"|governor\s+new\b",
    re.IGNORECASE,
)


def governor_in_scope(candidate_text: str) -> bool:
    return scope_table_confirms_new(candidate_text, GOVERNOR_SCOPE_ROW_KEYWORDS) or bool(
        GOVERNOR_EXPLICIT_SCOPE_RE.search(candidate_text or "")
    )


# ---------------------------------------------------------------------------------------------
# WRG roller guides — opt-in, and invalid on roll-formed HT / lubricated rail or non-passenger cars.
# NOTE: deliberately does NOT match plain "guide shoe" (universal sliding-shoe boilerplate).
# ---------------------------------------------------------------------------------------------
WRG_HT_RAIL_RE = re.compile(r"roll[- ]?formed\s+HT\s+rail|lubricated\s+rail", re.IGNORECASE)
WRG_NONPASSENGER_RE = re.compile(r"non[- ]?passenger|freight\s+elevator|unbalanced\s+car", re.IGNORECASE)
WRG_CWT_DESC_RE = re.compile(r"counterweight|\bCWT\b", re.IGNORECASE)
WRG_EXPLICIT_SCOPE_RE = re.compile(r"roller\s+guide|isolated\s+roller\s+guide|\bWRG\d", re.IGNORECASE)


def roller_guide_in_scope(candidate_text: str) -> bool:
    return bool(WRG_EXPLICIT_SCOPE_RE.search(candidate_text or ""))


def roller_guide_invalid_application(candidate_text: str) -> bool:
    t = candidate_text or ""
    return bool(WRG_HT_RAIL_RE.search(t) or WRG_NONPASSENGER_RE.search(t))


# ---------------------------------------------------------------------------------------------
# "Two-speed" door configuration with no centre-opening mention => 2T (not 2C). Confirmed live on
# '171 Guelph St': FTL's own pricelist describes 2T as the SPEED variant ("2/SPEED DOOR") and 2C as
# an opening-style variant ("C/OPEN DOOR"). Backs off entirely when centre opening is mentioned.
# ---------------------------------------------------------------------------------------------
TWO_SPEED_CONFIG_RE = re.compile(r"\btwo[- ]speed\b|\b2[- ]speed\b", re.IGNORECASE)
CENTRE_OPENING_RE = re.compile(r"cent(er|re)\s*[- ]?\s*open", re.IGNORECASE)


def two_speed_means_2t(candidate_text: str) -> bool:
    t = candidate_text or ""
    return bool(TWO_SPEED_CONFIG_RE.search(t)) and not CENTRE_OPENING_RE.search(t)


# ---------------------------------------------------------------------------------------------
# Computed breakdowns rendered by extract.py
# ---------------------------------------------------------------------------------------------
DOOR_PACKAGE_SECTION_RE = re.compile(r"## COMPUTED door package breakdown.*?(?=\n## |\Z)", re.DOTALL)
DOOR_PACKAGE_PER_CAR_LINE_RE = re.compile(r"^-\s*Car\s+\S+:", re.MULTILINE)
DOOR_PACKAGE_OPENING_GROUP_RE = re.compile(
    r"-\s*SGV2\s+(?P<family>\S+)\s+(?P<width>[\d.]+)\"\s*:\s*qty\s+(?P<qty>\d+)"
)
DOOR_PACKAGE_PANEL_GROUP_RE = re.compile(
    r"-\s*(?P<family>\S+)\s+(?P<width>[\d.]+)\"\s*car door panels:\s*qty\s+(?P<qty>\d+)"
)
DOOR_PACKAGE_TOTAL_OPENINGS_RE = re.compile(r"total openings\s*=\s*(\d+)", re.IGNORECASE)

GOVERNOR_BREAKDOWN_SECTION_RE = re.compile(r"## COMPUTED governor breakdown.*?(?=\n## |\Z)", re.DOTALL)
GOVERNOR_BREAKDOWN_STANDARD_RE = re.compile(r"Total cars needing STANDARD governor:\s*(\d+)")
GOVERNOR_BREAKDOWN_GEARLESS_RE = re.compile(r"Total cars needing GEARLESS/high-capacity governor:\s*(\d+)")


def computed_door_groups(candidate_text: str) -> Dict[str, object]:
    """{'total_openings': int|None, 'cars': int, 'groups': [{'family','width','qty'}], 'panels': [...]}
    parsed back out of the COMPUTED door package breakdown; empty groups when the RFQ has none."""
    m = DOOR_PACKAGE_SECTION_RE.search(candidate_text or "")
    out: Dict[str, object] = {"total_openings": None, "cars": 0, "groups": [], "panels": []}
    if not m:
        return out
    section = m.group(0)
    out["cars"] = len(DOOR_PACKAGE_PER_CAR_LINE_RE.findall(section))
    t = DOOR_PACKAGE_TOTAL_OPENINGS_RE.search(section)
    out["total_openings"] = int(t.group(1)) if t else None
    out["groups"] = [
        {"family": g.group("family"), "width": float(g.group("width")), "qty": int(g.group("qty"))}
        for g in DOOR_PACKAGE_OPENING_GROUP_RE.finditer(section)
    ]
    out["panels"] = [
        {"family": g.group("family"), "width": float(g.group("width")), "qty": int(g.group("qty"))}
        for g in DOOR_PACKAGE_PANEL_GROUP_RE.finditer(section)
    ]
    return out


def computed_governor_counts(candidate_text: str) -> Optional[Dict[str, int]]:
    """{'standard': n, 'gearless': n} from the COMPUTED governor breakdown, or None when absent."""
    m = GOVERNOR_BREAKDOWN_SECTION_RE.search(candidate_text or "")
    if not m:
        return None
    s = GOVERNOR_BREAKDOWN_STANDARD_RE.search(m.group(0))
    g = GOVERNOR_BREAKDOWN_GEARLESS_RE.search(m.group(0))
    return {"standard": int(s.group(1)) if s else 0, "gearless": int(g.group(1)) if g else 0}


# ---------------------------------------------------------------------------------------------
# Car count / entrance configuration, read straight from the spec's own statement (never inferred
# from a model's output). Two templates are known; see the Estimator's quantity enforcement for the
# real-RFQ history ('171 Guelph St', '5770 Hurontario St', ...).
# ---------------------------------------------------------------------------------------------
EXISTING_EQUIPMENT_INFO_HEADING_RE = re.compile(r"Existing Equipment Information", re.IGNORECASE)
CAR_HEADER_ROW_RE = re.compile(r"\bCar\s+(\d+)\b", re.IGNORECASE)
MULTI_ENTRANCE_RE = re.compile(r"(double|front\s+and\s+rear|two)\s+entrance", re.IGNORECASE)
SINGLE_ENTRANCE_RE = re.compile(r"single\s+entrance", re.IGNORECASE)
# PEC ("PERRY ELEVATOR CONSULTANTS") template: ".1 5770 Hurontario Street 4 Elevators" => 4 cars;
# ".14 Openings - Front 12" / ".15 Openings - Rear 0" => single entrance (0 rear openings).
EXISTING_EQUIPMENT_DESC_HEADING_RE = re.compile(r"Existing Equipment Description", re.IGNORECASE)
PEC_ELEVATOR_COUNT_RE = re.compile(r"\b(\d+)\s+Elevators?\b", re.IGNORECASE)
PEC_REAR_OPENINGS_RE = re.compile(r"Openings\s*[-–—]\s*Rear\s+(\d+)", re.IGNORECASE)


def detect_car_count_and_single_entrance(candidate_text: str) -> Optional[Tuple[int, bool]]:
    """(car_count, single_entrance_confirmed) from the first known template whose own literal
    car-count/entrance statement matches, or None. A direct read of text the spec states."""
    text = candidate_text or ""

    heading_match = EXISTING_EQUIPMENT_INFO_HEADING_RE.search(text)
    if heading_match:
        window = text[heading_match.end(): heading_match.end() + 600]
        car_numbers = {int(n) for n in CAR_HEADER_ROW_RE.findall(window)}
        if car_numbers:
            car_count = len(car_numbers)
            single_entrance = bool(SINGLE_ENTRANCE_RE.search(window)) and not MULTI_ENTRANCE_RE.search(window)
            return car_count, single_entrance

    heading_match = EXISTING_EQUIPMENT_DESC_HEADING_RE.search(text)
    if heading_match:
        window = text[heading_match.end(): heading_match.end() + 800]
        car_count_match = PEC_ELEVATOR_COUNT_RE.search(window)
        rear_match = PEC_REAR_OPENINGS_RE.search(window)
        if car_count_match and rear_match:
            car_count = int(car_count_match.group(1))
            single_entrance = int(rear_match.group(1)) == 0
            return car_count, single_entrance

    return None


def car_and_opening_counts(candidate_text: str) -> Dict[str, Optional[int]]:
    """{'cars': n|None, 'openings': n|None}. Prefers the COMPUTED door package breakdown (per-car
    table, front+rear counted per car); otherwise the spec's own car-count statement, with openings
    known only when a single entrance is confirmed (openings == cars) — exactly the Estimator's
    own quantity rule, so both agents state the same number or both state none."""
    groups = computed_door_groups(candidate_text)
    if groups["total_openings"] is not None:
        return {"cars": int(groups["cars"]) or None, "openings": int(groups["total_openings"])}  # type: ignore[arg-type]
    detected = detect_car_count_and_single_entrance(candidate_text)
    if detected:
        cars, single = detected
        return {"cars": cars, "openings": cars if single else None}
    return {"cars": None, "openings": None}
