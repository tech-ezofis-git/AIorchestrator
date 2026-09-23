"""
RFQ input extraction for the Quote Estimator agent: turns a raw upload (.eml, or a bare PDF/DOCX
spec) into (a) email metadata if present, and (b) a *shrunk* candidate text — not the full
40-120+ page tender document — built from deterministic, code-level section targeting.

Why this exists at all, rather than just handing the whole document to the model: the agent runs
on a nano-tier model over documents that regularly run 100+ pages. Section targeting is a hard,
code-level step (same philosophy as chat_agent_retrieval.py's price-redaction backstop — don't
rely on the model alone for something code can do reliably), grounded in the real RFQ samples:
consultant modernization tenders consistently split into three linked spec sections (commonly
numbered 14000/14100/14900) with a recurring, nameable set of FTL-relevant subsections (Governor &
Governor Ropes, Counterweight Guides, Hall Door Equipment, Car Roller Guides, Door Operators, Door
Protective Device, Car Door Clutch, Car Safeties) plus a bidder equipment-manifest questionnaire.
New-construction tenders use a different, single-spec PART 1/2/3 template covering the whole
elevator — that structural difference is itself extracted as a signal.

Unlike the Qualifier (which only needs to know an item exists), the Quote Estimator also needs to
know *how many* cars/elevators need each item, since that drives line-item quantities. That count
lives in two places the item-subsection targeting alone doesn't reach: a short "Number of Devices"
summary line near the top of the spec (e.g. "Elevator 2"), and a "Description of Existing
Equipment" (or "Schedule of Existing Equipment") table further in, with one column per car —
that table also carries each car's existing door type/operator/safety, useful for confirming
per-car configuration. Both are pulled out explicitly below so quantities have a real source
instead of defaulting to an honest-but-useless 0.

This is a v1 heuristic tuned against a handful of real samples, not a general-purpose document
parser — expect to tune the patterns further against real production traffic.
"""

from __future__ import annotations

import email
import re
from email import policy
from email.parser import BytesParser
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from docx import Document as DocxDocument
from io import BytesIO

try:
    import pymupdf as fitz  # PyMuPDF's current package name; keep the `fitz` alias so every call
    # site below (fitz.open, etc.) needs no changes. Importing this way avoids the "the `fitz` API
    # is deprecated, use `import pymupdf` instead" warning that `import fitz` triggers on newer
    # PyMuPDF versions — purely cosmetic (fitz still works fine either way), fixed so it doesn't
    # look like a real problem on every app launch.

    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

# ---------------------------------------------------------------------------
# Email parsing — handles the real intake shape (a forwarded chain, the actual tender spec as a
# nested attachment, sometimes multiple attachments).
# ---------------------------------------------------------------------------


def parse_eml_bytes(raw: bytes) -> Dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    body_text: Optional[str] = None
    attachments: List[Dict[str, Any]] = []

    def walk(m):
        nonlocal body_text
        if m.is_multipart():
            for part in m.iter_parts():
                walk(part)
            return
        disp = m.get_content_disposition()
        fname = m.get_filename()
        ct = m.get_content_type()
        if disp == "attachment" or (fname and disp != "inline"):
            try:
                content = m.get_content()
            except Exception:
                content = None
            if isinstance(content, str):
                content = content.encode("utf-8", "replace")
            if isinstance(content, (bytes, bytearray)):
                attachments.append({"filename": fname or "attachment", "content_type": ct, "bytes": bytes(content)})
        elif ct == "text/plain" and body_text is None and disp != "attachment":
            try:
                body_text = m.get_content()
            except Exception:
                pass

    walk(msg)

    if body_text is None:
        # fall back to text/html stripped of tags, best-effort
        def find_html(m):
            if m.is_multipart():
                for p in m.iter_parts():
                    r = find_html(p)
                    if r:
                        return r
            elif m.get_content_type() == "text/html":
                try:
                    return m.get_content()
                except Exception:
                    return None
            return None

        html = find_html(msg)
        if html:
            body_text = re.sub(r"<[^>]+>", " ", html)

    return {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
        "body_text": body_text or "",
        "attachments": attachments,
        "forwarded_sender": _find_forwarded_sender(body_text or ""),
    }


_FORWARDED_FROM_RE = re.compile(
    r"From:\s*([^\n<]+?)\s*<([\w.+\-]+@[\w.\-]+)>[^\n]*\n(?:.*\n){0,4}?Subject:\s*([^\n]+)",
    re.IGNORECASE,
)


_FTL_DOMAIN = "ftl-distribution"


def _find_forwarded_sender(body_text: str) -> Optional[Dict[str, str]]:
    """FTL's RFQs almost always arrive as an internally-forwarded email — the outer From/Subject
    headers belong to the FTL employee who forwarded it, not the customer. The real customer's
    identity is embedded in the quoted body as a further 'From: Name <email>' block (Outlook-style
    forward), typically followed by 'Sent:'/'To:'/'Cc:'/'Subject:' lines and then the original
    message with the customer's own sign-off (name/phone/address).

    Rather than relying on the model alone to notice and parse this (verified unreliable on a
    nano-tier model across repeated test runs even after being told to look for it), pull the
    right 'From:' block out here in code and hand it to the model as an explicit, impossible-to-
    miss hint line — the same "surface it structurally, don't just describe it in prose" approach
    already used for the device-count and New Equipment checklist extraction.

    Outlook-style forward chains are REVERSE chronological reading top-to-bottom — the most recent
    forward's 'From:' block appears FIRST in the body, with older messages (and the oldest, most
    original sender) further down. A single-hop forward (customer -> FTL) only has one match, so
    first/last don't differ there. A real multi-hop sample confirmed why last-match is wrong for
    longer chains: a tender's original inviting consultant forwarded it to bidding contractors
    months earlier; one contractor (the actual FTL customer, later confirmed exactly against a real
    accepted Sales Order — matching name, phone, and billing address) forwarded it on to FTL more
    recently. Taking the last match grabbed the old consultant instead of the actual customer.
    Returns the FIRST match whose email domain isn't FTL's own (skips any internal FTL-to-FTL
    forwarding hop, if present, and lands on the most recent non-FTL sender — the actual customer)."""
    matches = list(_FORWARDED_FROM_RE.finditer(body_text or ""))
    for m in matches:
        email_addr = m.group(2).strip()
        if _FTL_DOMAIN not in email_addr.lower():
            return {"name": m.group(1).strip(), "email": email_addr, "subject": m.group(3).strip()}
    return None


# ---------------------------------------------------------------------------
# PDF / DOCX text extraction
# ---------------------------------------------------------------------------


def extract_pdf_pages(pdf_bytes: bytes) -> List[str]:
    """One string per page. Used both for the pricelist (page-granular ingestion) and for RFQ
    specs (section targeting works over the concatenated text).

    Tries PyMuPDF (fitz) first, falling back to pdfplumber if it isn't installed or a given
    document errors. This isn't cosmetic: verified against FTL's real Wittur pricelist PDF, an
    entire pricing table (Universal Car Door Panels) came out as scrambled, transposed single
    characters under pdfplumber — apparently a rotated/multi-column layout pdfplumber's default
    word-ordering can't handle — while PyMuPDF extracted the same page cleanly, in correct reading
    order. A silently unreadable pricelist page means that product category is invisible to
    search_pricelist and any quote touching it will simply omit the line, so this matters for
    quote accuracy, not just readability."""
    if _HAS_FITZ:
        try:
            pages: List[str] = []
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                for page in doc:
                    pages.append(page.get_text("text") or "")
            finally:
                doc.close()
            return pages
        except Exception:
            pass  # fall through to pdfplumber below

    pages = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
    return pages


def extract_pdf_text(pdf_bytes: bytes) -> str:
    return "\n\n".join(extract_pdf_pages(pdf_bytes))


def extract_docx_text(docx_bytes: bytes) -> str:
    doc = DocxDocument(BytesIO(docx_bytes))
    parts: List[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_text_by_filename(filename: str, content_bytes: bytes) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return extract_pdf_text(content_bytes)
    if lower.endswith(".docx"):
        return extract_docx_text(content_bytes)
    if lower.endswith(".txt") or lower.endswith(".md"):
        return content_bytes.decode("utf-8", "replace")
    # Unknown type: best-effort text decode so we never silently drop an attachment.
    try:
        return content_bytes.decode("utf-8", "replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Deterministic section targeting
# ---------------------------------------------------------------------------

# Title text (not section numbers, which vary by document) for the FTL-relevant subsections found
# consistently across the real modernization-tender samples.
TARGET_SUBSECTION_TITLES = [
    "Governor and Governor Ropes",
    "Counterweight Guides",
    "Hall Door Equipment",
    "Car Roller Guides",
    "Door Operators",
    "Door Protective Device",
    "Car Door Clutch",
    "Car Safeties",
    "Description of Existing Equipment",
    "Schedule of Existing Equipment",
    "New Equipment",
]

# Word budget per targeted subsection, overriding DEFAULT_SUBSECTION_WORD_BUDGET below. The
# existing-equipment schedule is a wide per-car table (one column per car, ~30+ attribute rows) —
# it needs more room than a normal prose subsection or its later cars/rows get truncated away,
# which would silently reintroduce the missing-quantity problem this file exists to fix.
SUBSECTION_WORD_BUDGET = {
    "Description of Existing Equipment": 1100,
    "Schedule of Existing Equipment": 1100,
    # A numbered checklist (~35 short items), not prose — the default 600-word cap is already
    # generous for it, but give it a bit more room since it's a hard, complete signal for which
    # categories are actually in scope (see skill_store.py's "New Equipment checklist" guidance).
    "New Equipment": 400,
}
DEFAULT_SUBSECTION_WORD_BUDGET = 600

# Loose keyword set for the fallback window extractor — used when the document doesn't match the
# modernization 14000/14100/14900 template closely enough for heading-based targeting to find much.
FALLBACK_KEYWORDS = [
    "door operator",
    "roller guide",
    "governor",
    "car safet",
    "door clutch",
    "panel adaptor",
    "panel adapter",
    "hall door",
    "counterweight guide",
    "door protective",
    "door detector",
    "car door",
]

_HEADING_RE = re.compile(r"^\s*\d+\.\d+\.?\s+([A-Z][A-Za-z0-9 ,/&'\-]{2,80})\s*$", re.MULTILINE)


def detect_structure_signal(full_text: str, subsection_hit_count: int = 0) -> str:
    """'modernization_3section' | 'new_construction_single_spec' | 'unknown' — a hard, code-level
    signal handed to the model rather than left for it to infer from scratch.

    Consultants number their modernization spec sections differently (Solucore's samples use
    14000/14100/14900; ATTA's use a single combined 14200) — so literal section-number matching
    alone under-detects. The number of TARGET_SUBSECTION_TITLES actually found by heading (found
    consistently regardless of numbering scheme, since that lookup is title-based) is the more
    robust primary signal; the literal 14000/14100/14900 count is a secondary corroborating signal.
    """
    mod_number_hits = len(re.findall(r"\b14000\b", full_text)) + len(re.findall(r"\b14100\b", full_text)) + len(
        re.findall(r"\b14900\b", full_text)
    )
    new_construction_hits = sum(
        1
        for pat in (r"PART\s*1\s*[-–]\s*GENERAL", r"PART\s*2\s*[-–]\s*PRODUCTS", r"PART\s*3\s*[-–]\s*EXECUTION")
        if re.search(pat, full_text, re.IGNORECASE)
    )
    if subsection_hit_count >= 4 or mod_number_hits >= 6:
        return "modernization_3section"
    if new_construction_hits >= 2:
        return "new_construction_single_spec"
    return "unknown"


def _find_equipment_manifest(full_text: str) -> str:
    """The bidder-questionnaire block asking existing make/model for Controllers, Door Operators,
    Governor, Car Roller Guides, etc. — grabbed as a fixed-size window around its first hit."""
    m = re.search(r"(Controllers make|Door Operators make|Governor make)", full_text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - 200)
    end = min(len(full_text), m.start() + 2500)
    return full_text[start:end].strip()


def _extract_by_headings(full_text: str) -> Dict[str, str]:
    """For each target subsection title, find its real body occurrence (a numbered heading line,
    not the dotted table-of-contents listing) and capture text up to the next numbered heading."""
    out: Dict[str, str] = {}
    heading_matches = list(_HEADING_RE.finditer(full_text))
    for title in TARGET_SUBSECTION_TITLES:
        title_norm = title.lower()
        found = None
        for idx, hm in enumerate(heading_matches):
            heading_text = hm.group(1).strip().lower()
            # TOC listings usually carry a trailing run of dots/page number on the same line,
            # which the heading regex's tight character class already excludes from group(1) —
            # so an in-body heading and a TOC entry can look identical here. Prefer the LAST
            # match (bodies come after the TOC in every sample) when a title appears more than
            # once, and require near-exact equality to avoid partial-title false positives.
            if heading_text == title_norm or heading_text.startswith(title_norm):
                found = idx
        if found is None:
            continue
        start = heading_matches[found].end()
        end = heading_matches[found + 1].start() if found + 1 < len(heading_matches) else min(
            len(full_text), start + 6000
        )
        body = full_text[start:end].strip()
        # Cap any single subsection so one runaway match can't crowd out the others.
        budget = SUBSECTION_WORD_BUDGET.get(title, DEFAULT_SUBSECTION_WORD_BUDGET)
        words = body.split()
        if len(words) > budget:
            body = " ".join(words[:budget]) + " …"
        out[title] = body
    return out


# Alternate anchor phrases for the per-car equipment inventory table. Different consultants title
# this table differently — "Description of Existing Equipment" and "Schedule of Existing
# Equipment" are both already in TARGET_SUBSECTION_TITLES above and get found fine by heading-based
# extraction on the modernization-template samples this file was first tuned against. But a real,
# confirmed failure (the "25 Telegram Mews" RFQ, Bredan Elevator Consultants' template) used neither
# heading text NOR that template's numbered x.y heading style at all — the table sat under a bare
# "Equipment Inventory:" label with no section number, so heading-based extraction found nothing and
# the whole table silently fell through to the generic keyword-window fallback, which only grabs
# ~400 characters around each keyword hit — nowhere near enough to keep a "Designation" (car number)
# row attached to the Entrance Type/Width/Rear Door values under it. That single gap explained most
# of a real client-reported quote failure: the agent had no reliable way to know this project had 5
# cars/8 openings and defaulted to a guessed, wrong count of 4. This function is a dedicated,
# anchor-based grab (not heading-dependent) with a much larger word budget, specifically so this
# table survives intact regardless of which heading convention (or none) introduces it.
_EQUIPMENT_INVENTORY_ANCHORS = (
    "Equipment Inventory",
    "Description of Existing Equipment",
    "Schedule of Existing Equipment",
)
_EQUIPMENT_INVENTORY_ANCHOR_RE = re.compile(
    "|".join(re.escape(a) for a in _EQUIPMENT_INVENTORY_ANCHORS), re.IGNORECASE
)
_EQUIPMENT_INVENTORY_WORD_BUDGET = 2200


def _find_equipment_inventory_table_raw(full_text: str) -> str:
    """Same span-finding as _find_equipment_inventory_table below, but returns the UNTRUNCATED body
    — used by _parse_equipment_inventory_records, which needs every designation/value line intact
    rather than a word-budget-truncated excerpt."""
    m = _EQUIPMENT_INVENTORY_ANCHOR_RE.search(full_text)
    if not m:
        return ""
    start = max(0, m.start() - 50)
    stop_m = re.search(r"\n\s*General Specifications\s*\n", full_text[start:])
    end = start + stop_m.start() if stop_m else min(len(full_text), start + 12000)
    return full_text[start:end].strip()


def _find_equipment_inventory_table(full_text: str) -> str:
    """The per-car (per-elevator) equipment table — one column per car, carrying at minimum each
    car's Entrance Type/Entrance Width/Rear Door (and usually much more: control type, drive method,
    door operator, etc.). This is THE primary source for exact car/opening counts and per-car
    configuration — see the module docstring above this function for why it needs its own generous,
    anchor-based extraction rather than relying on heading-based targeting or the short device-count
    summary window. Captures from the first anchor hit until the next 'General Specifications'-style
    section boundary (confirmed real layout: this table sits right before the numbered spec body
    begins), or a generous fixed budget if no such boundary is found."""
    body = _find_equipment_inventory_table_raw(full_text)
    if not body:
        return ""
    words = body.split()
    if len(words) > _EQUIPMENT_INVENTORY_WORD_BUDGET:
        body = " ".join(words[:_EQUIPMENT_INVENTORY_WORD_BUDGET]) + " …"
    return body


# --- Structured per-car parsing --------------------------------------------------------------
# A real, twice-reproduced failure: the model correctly derives AGGREGATE totals (e.g. "8 clutches
# total") but then loses track of the per-door-TYPE breakdown when it writes individual line items
# — one real test run submitted correct clutch/detector/restrictor totals (8 each) while completely
# omitting the center-opening door-operator AND panel lines for two of the five cars. Rather than
# ask a nano-tier model to keep re-deriving this arithmetic reliably, this table (when it matches
# the observed strict layout — a label line immediately followed by exactly N one-line values, N
# being the designation count of that block) is parsed into structured per-car records so the exact
# breakdown can be computed once in code and handed to the model as a fact, not an exercise.
_INVENTORY_DESIGNATION_LABEL_RE = re.compile(r"^designation$", re.IGNORECASE)
_INVENTORY_FIELD_LABEL_PATTERNS: Dict[str, str] = {
    "entrance_type": r"^entrance type$",
    "entrance_width": r"^entrance width(\s*\(inches\))?$",
    "rear_door": r"^rear door$",
    "drive_method": r"^drive method$",
    "contract_speed": r"^contract speed$",
}
_INVENTORY_FIELD_LABEL_RES: Dict[str, re.Pattern] = {
    k: re.compile(v, re.IGNORECASE) for k, v in _INVENTORY_FIELD_LABEL_PATTERNS.items()
}
_DESIGNATION_VALUE_RE = re.compile(r"^[A-Za-z0-9]{1,4}$")
_MAX_DESIGNATIONS_PER_BLOCK = 12


def _parse_equipment_inventory_records(full_text: str) -> List[Dict[str, str]]:
    """Returns one dict per car (with 'designation' plus whichever of entrance_type/entrance_width/
    rear_door/drive_method/contract_speed were found), or [] if the table isn't present or doesn't
    match the expected strict layout closely enough to trust. This is an ADDITIVE enhancement over
    the raw-text table (still rendered as-is regardless) — never a silent replacement, since a
    differently-formatted spec should just fall back to the model reasoning over the raw text."""
    raw = _find_equipment_inventory_table_raw(full_text)
    if not raw:
        return []
    lines = [l.strip() for l in raw.splitlines()]
    # Locate every "Designation" block start.
    block_starts = [i for i, l in enumerate(lines) if _INVENTORY_DESIGNATION_LABEL_RE.match(l)]
    if not block_starts:
        return []
    block_bounds = list(zip(block_starts, block_starts[1:] + [len(lines)]))

    records: List[Dict[str, str]] = []
    for block_start, block_end in block_bounds:
        # Designations: consecutive short alnum-token lines immediately after the "Designation" line.
        designations: List[str] = []
        i = block_start + 1
        while (
            i < block_end
            and len(designations) < _MAX_DESIGNATIONS_PER_BLOCK
            and lines[i]
            and _DESIGNATION_VALUE_RE.match(lines[i])
        ):
            designations.append(lines[i])
            i += 1
        if not designations:
            continue
        n = len(designations)
        block_records: List[Dict[str, str]] = [{"designation": d} for d in designations]

        for field, pattern in _INVENTORY_FIELD_LABEL_RES.items():
            for j in range(block_start + 1, block_end):
                if pattern.match(lines[j]):
                    values = [v for v in lines[j + 1 : j + 1 + n] if v]
                    if len(values) == n:
                        for rec, val in zip(block_records, values):
                            rec[field] = val
                    break
        records.extend(block_records)

    # Only trust this if every record actually got the fields this feature depends on — partial
    # matches (e.g. a differently-labeled table that happens to also say "Designation") are more
    # likely a false-positive layout match than real per-car door data.
    if not records or not all(
        "entrance_type" in r and "entrance_width" in r and "rear_door" in r for r in records
    ):
        return []
    return records


# Maps the RFQ's own entrance-type vocabulary onto this business's SGV2 door-operator/panel product
# family codes — confirmed against multiple real accepted quotes (e.g. EST-261132): SSSO/1SSO
# (single section side opening) -> "1S", CO/2PCO (center opening) -> "2C".
_ENTRANCE_TYPE_TO_SGV2_FAMILY: Dict[str, str] = {
    "SSSO": "1S",
    "1SSO": "1S",
    "CO": "2C",
    "2PCO": "2C",
}


def compute_door_package_breakdown(records: List[Dict[str, str]]) -> Dict[str, Any]:
    """From parsed per-car records (see _parse_equipment_inventory_records), computes the exact
    door-operator/clutch/panel/detector/restrictor breakdown using this business's own confirmed
    counting rules: each car has a FRONT opening always, plus a REAR opening when Rear Door=Yes;
    each opening needs its own door-operator unit, clutch, 3D detector, and restrictor, and its own
    door panel(s) (1 panel for a single-section 1S opening, 2 panels/set for a center-opening 2C
    opening). Returns {} if any car's entrance type isn't in the known SGV2 family mapping (rather
    than guessing) — the raw table is always rendered too, so the model can still reason it out."""
    if not records:
        return {}
    groups: Dict[Tuple[str, str], int] = {}  # (family, width) -> opening count
    total_openings = 0
    per_car_lines: List[str] = []
    for rec in records:
        entrance_type = (rec.get("entrance_type") or "").strip().upper()
        family = _ENTRANCE_TYPE_TO_SGV2_FAMILY.get(entrance_type)
        if not family:
            return {}
        width = (rec.get("entrance_width") or "").strip().rstrip("”\"'")
        rear_door = (rec.get("rear_door") or "").strip().lower()
        openings_this_car = 2 if rear_door == "yes" else 1
        groups[(family, width)] = groups.get((family, width), 0) + openings_this_car
        total_openings += openings_this_car
        per_car_lines.append(
            f"Car {rec.get('designation')}: {entrance_type} ({family}) {width}\" — "
            f"{'front + rear' if openings_this_car == 2 else 'front only'} "
            f"= {openings_this_car} opening(s)"
        )
    door_operator_lines = [
        f"SGV2 {family} {width}\": qty {qty} (door operator + matching clutch + 3D detector + "
        f"restrictor, one of each per opening)"
        for (family, width), qty in sorted(groups.items())
    ]
    panel_lines = [
        (
            f"{family} {width}\" car door panels: qty {qty} (1 panel per opening)"
            if family == "1S"
            else (
                f"{family} {width}\" car door panels: qty {qty * 2} "
                f"(2-panel set per opening — {qty} opening(s) x 2 panels/set — priced/counted as "
                "individual panels, not sets)"
            )
        )
        for (family, width), qty in sorted(groups.items())
    ]
    return {
        "per_car_lines": per_car_lines,
        "door_operator_lines": door_operator_lines,
        "panel_lines": panel_lines,
        "total_openings": total_openings,
        "groups": {f"{family}_{width}": qty for (family, width), qty in groups.items()},
    }


# A car whose Drive Method explicitly says "gearless" needs the high-capacity/gearless-rated
# governor variant; every other Drive Method wording seen so far — "OH traction (geared)",
# "Traction (Basement)" — needs the standard variant. Confirmed against the real "25 Telegram Mews"
# RFQ: 2 cars "OH traction (geared)" @ 500fpm + 2 cars "Traction (Basement)" @ 500fpm = 4 standard,
# 1 car "OH traction (gearless)" @ 700fpm = 1 gearless — matching the real reference quote's
# governor split (4 standard + 1 high-capacity) exactly.
_GEARLESS_DRIVE_METHOD_RE = re.compile(r"\bgearless\b", re.IGNORECASE)


def compute_governor_breakdown(records: List[Dict[str, str]]) -> Dict[str, Any]:
    """From the same per-car records used for compute_door_package_breakdown (see
    _parse_equipment_inventory_records) — reusing the 'drive_method' field already parsed there for
    a different purpose — computes an authoritative governor split. Governors are one per CAR, not
    per opening (unlike door hardware above), and NOT every car necessarily needs the same governor
    product: this business's own real reference quotes show gearless/higher-speed cars needing a
    different, notably more expensive governor variant than the rest.

    This exists because governor quantity/variant has been, by a wide margin, the single most
    persistent failure category across real repeated test runs on this exact RFQ — the model has at
    various times submitted 0, 2, 3, or 5-but-all-one-variant governor lines, despite every one of
    those runs being given the same per-car Drive Method data this function reads. A warning-only
    check (see agent.py's _check_governor_count / _check_governor_variant_differentiation) kept
    catching the mismatch after the fact but never changed the actual number reaching the total.
    Since the correct split is fully derivable here, in code, from data already reliably parsed
    (same trust bar as compute_door_package_breakdown above), agent.py now uses this to AUTO-CORRECT
    the governor line items outright rather than just flag them — the same escalation already
    applied once before to the SSSO/1S entrance-type mistake for the same reason (repeated real
    failures that prose warnings alone never fixed).

    Returns {} if any car's record is missing 'drive_method' (e.g. a differently-formatted spec, or
    one without this table at all) — callers must fall back to the existing warning-only checks in
    that case, never guess a split without this data."""
    if not records or not all("drive_method" in r for r in records):
        return {}
    standard_designations: List[str] = []
    gearless_designations: List[str] = []
    per_car_lines: List[str] = []
    for rec in records:
        drive = (rec.get("drive_method") or "").strip()
        designation = rec.get("designation", "?")
        is_gearless = bool(_GEARLESS_DRIVE_METHOD_RE.search(drive))
        if is_gearless:
            gearless_designations.append(designation)
        else:
            standard_designations.append(designation)
        per_car_lines.append(
            f"Car {designation}: Drive Method = \"{drive or 'unknown'}\" -> "
            f"{'GEARLESS/high-capacity governor needed' if is_gearless else 'standard governor'}"
        )
    return {
        "per_car_lines": per_car_lines,
        "n_standard": len(standard_designations),
        "n_gearless": len(gearless_designations),
        "standard_designations": standard_designations,
        "gearless_designations": gearless_designations,
        "total_cars": len(records),
    }


# A compact "Equipment / Scope" table some consultants include (confirmed on the same real "25
# Telegram Mews" sample) — one row per equipment category, each with a single Scope value: New,
# Refurbish, Retain, None, or a per-car split (e.g. "Refurbish car 3/replace cars 1, 2, 4, 5"). This
# is a direct, authoritative statement of what's actually in scope as NEW equipment — far more
# reliable than inferring scope from prose or hoping a numbered "New Equipment" checklist exists.
# Matching on "Equipment Specifications" alone would also match the running page-header text
# ("Elevator Modernization Specifications Document"), so the anchor requires the immediately
# following "Equipment" / "Scope" column headers too, which only the real table has.
_EQUIPMENT_SCOPE_ANCHOR_RE = re.compile(
    r"Equipment Specifications\s*\n\s*\n?\s*Equipment\s*\n\s*Scope\b", re.IGNORECASE
)
_EQUIPMENT_SCOPE_WORD_BUDGET = 700


def _find_equipment_scope_table(full_text: str) -> str:
    """See _EQUIPMENT_SCOPE_ANCHOR_RE above. A real confirmed failure quoted new roller guides and
    car safeties that this exact table (when present) would have ruled out immediately: it listed
    'Car guides: Refurbish' and carried no 'Car Safety' row at all. When this table is present, it
    should be treated as the authoritative New-vs-Refurbish/Retain/None answer for every category it
    lists — never overridden by an inference from prose elsewhere."""
    m = _EQUIPMENT_SCOPE_ANCHOR_RE.search(full_text)
    if not m:
        return ""
    start = m.start()
    window_end = min(len(full_text), start + 4500)
    window = full_text[start:window_end]
    # Stop at the next numbered heading (e.g. "37 \n General Scope and Instruction") so the table
    # doesn't run on into unrelated prose; skip past the table's own short header before searching
    # so the search doesn't just re-match a false start.
    stop_m = re.search(r"\n\s*\d+\s*\n\s*[A-Z][A-Za-z ]{3,40}\n", window[200:])
    end = start + 200 + stop_m.start() if stop_m else window_end
    body = full_text[start:end].strip()
    words = body.split()
    if len(words) > _EQUIPMENT_SCOPE_WORD_BUDGET:
        body = " ".join(words[:_EQUIPMENT_SCOPE_WORD_BUDGET]) + " …"
    return body


def _find_device_count_summary(full_text: str) -> str:
    """The short building/bank/machine-type/device-count summary near the top of the spec (e.g.
    'Building Bank Machine Type Number of Devices ... 40 Baif Boulevard Passenger Elevators
    Elevator 2') — the most direct statement of how many cars/elevators this tender covers. This
    is the primary source for line-item quantities, so it's pulled out on its own rather than left
    to be found (or missed) inside a longer section."""
    m = re.search(r"Number of Devices", full_text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - 120)
    end = min(len(full_text), m.start() + 400)
    window = full_text[start:end].strip()
    # Trim back to the start of a sentence/list-item so the window doesn't open mid-word — look
    # for the nearest preceding numbered list marker ("4." style) or line break.
    lead_match = re.search(r"(?:^|\n)\s*\d+\.\s+\S", window)
    if lead_match:
        window = window[lead_match.start():].lstrip()
    return window.strip()


def _fallback_keyword_windows(full_text: str, total_word_budget: int = 4000) -> str:
    lower = full_text.lower()
    hits: List[Tuple[int, int]] = []
    for kw in FALLBACK_KEYWORDS:
        for m in re.finditer(re.escape(kw), lower):
            hits.append((m.start(), m.end()))
    if not hits:
        return ""
    hits.sort()
    windows: List[Tuple[int, int]] = []
    for start, end in hits:
        w_start = max(0, start - 400)
        w_end = min(len(full_text), end + 400)
        if windows and w_start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], w_end))
        else:
            windows.append((w_start, w_end))

    out_parts: List[str] = []
    budget = total_word_budget
    for w_start, w_end in windows:
        snippet = full_text[w_start:w_end].strip()
        words = snippet.split()
        if not words:
            continue
        if len(words) > budget:
            words = words[:budget]
        out_parts.append(" ".join(words) + " …")
        budget -= len(words)
        if budget <= 0:
            break
    return "\n\n---\n\n".join(out_parts)


def build_candidate_text(full_text: str) -> Dict[str, Any]:
    """The single entry point: run structure detection + targeted extraction (with a keyword-window
    fallback), and return everything the agent needs, already shrunk to a nano-model-friendly size."""
    equipment_manifest = _find_equipment_manifest(full_text)
    device_count_summary = _find_device_count_summary(full_text)
    subsections = _extract_by_headings(full_text)
    structure_signal = detect_structure_signal(full_text, subsection_hit_count=len(subsections))

    # Only pull in the dedicated equipment-inventory extraction if heading-based targeting didn't
    # already land a substantial capture under one of the same anchor titles — avoids duplicating
    # the same table twice in the candidate text on documents where heading-based targeting already
    # works fine (the modernization-template samples this file was first tuned against).
    _existing_inventory_text = " ".join(
        subsections.get(t, "") for t in ("Description of Existing Equipment", "Schedule of Existing Equipment")
    )
    equipment_inventory_table = (
        "" if len(_existing_inventory_text.split()) >= 100 else _find_equipment_inventory_table(full_text)
    )
    equipment_scope_table = _find_equipment_scope_table(full_text)
    _inventory_records = _parse_equipment_inventory_records(full_text)
    door_package_breakdown = compute_door_package_breakdown(_inventory_records)
    governor_breakdown = compute_governor_breakdown(_inventory_records)

    used_fallback = False
    if len(subsections) < 2:
        # Heading-based targeting found little — either a different template (e.g. the
        # new-construction PART 1/2/3 shape) or a document our patterns don't match well. Fall
        # back to keyword-proximity windows so the agent still gets *something* rather than
        # nothing, and structure_signal already flags this as lower-confidence territory.
        used_fallback = True
        fallback_text = _fallback_keyword_windows(full_text)
    else:
        fallback_text = ""

    return {
        "structure_signal": structure_signal,
        "equipment_manifest": equipment_manifest,
        "device_count_summary": device_count_summary,
        "equipment_inventory_table": equipment_inventory_table,
        "equipment_scope_table": equipment_scope_table,
        "door_package_breakdown": door_package_breakdown,
        "governor_breakdown": governor_breakdown,
        "subsections": subsections,
        "used_fallback": used_fallback,
        "fallback_text": fallback_text,
    }


def render_candidate_text_for_model(candidate: Dict[str, Any], email_meta: Optional[Dict[str, Any]] = None) -> str:
    """Flatten build_candidate_text()'s output into the actual text block handed to the model."""
    lines: List[str] = []
    if email_meta:
        lines.append("## Email")
        lines.append(f"From: {email_meta.get('from', '')}")
        lines.append(f"Subject: {email_meta.get('subject', '')}")
        lines.append(f"Date: {email_meta.get('date', '')}")
        fwd = email_meta.get("forwarded_sender")
        if fwd:
            lines.append("")
            lines.append(
                f"NOTE: the From: line above is FTL's own employee forwarding this RFQ, NOT the "
                f"customer — this is normal, FTL sells only to elevator contractors who forward "
                f"tenders in. The actual customer contact, parsed from a forwarded 'From:' block "
                f"further down in the body below, is: {fwd['name']} <{fwd['email']}>. IMPORTANT: "
                f"customer_name and contact_name are NOT the same field — contact_name is this "
                f"person's own name ({fwd['name']}), while customer_name is their COMPANY's name "
                f"(infer it from their email domain and/or letterhead/signature in the body below, "
                f"e.g. a signature block or logo naming the company — do not just repeat the "
                f"person's name into customer_name too). Read the body below for their phone and "
                f"postal address, usually in their own sign-off. Do not leave customer_name/"
                f"contact_name/billing_address/shipping_address blank just because the From: "
                f"header above is an FTL address — the real customer's info is further down."
            )
        body = (email_meta.get("body_text") or "").strip()
        if body:
            lines.append("")
            lines.append(body[:3000])
        lines.append("")

    lines.append(f"## Detected structure signal: {candidate['structure_signal']}")
    lines.append("")

    if candidate.get("device_count_summary"):
        lines.append("## Device count / schedule summary (from the spec) — use this for quantities")
        lines.append(candidate["device_count_summary"])
        lines.append("")

    if candidate.get("equipment_inventory_table"):
        lines.append(
            "## Per-car equipment inventory (from the spec) — AUTHORITATIVE source for exact "
            "car/opening counts and per-car configuration. Use the exact per-car values shown here "
            "— do not guess, round, or default a car count. Different equipment categories are "
            "counted on different bases: door hardware (operators/clutches/restrictors/detectors) "
            "is normally one set per OPENING (count every car's front AND rear opening separately "
            "if both are listed), while governors/safeties are normally one per CAR/ELEVATOR "
            "(count elevators, not openings) unless this table or another spec table says "
            "otherwise. Read every car's row here before deciding any quantity."
        )
        lines.append(candidate["equipment_inventory_table"])
        lines.append("")

    breakdown = candidate.get("door_package_breakdown")
    if breakdown:
        lines.append(
            "## COMPUTED door package breakdown (derived directly from the per-car table above by "
            "code, not by the model) — AUTHORITATIVE. Use these exact groupings and quantities for "
            "door_operator, clutch, door_protective_device, and the door restrictor ('other' "
            "category) line items — do not recompute or re-derive these from the raw table, and do "
            "not omit any group below even if it's a small one. Every group listed here needs its "
            "own door_operator, clutch, door_protective_device, and restrictor line at the quantity "
            "shown (clutch/detector/restrictor each match the door_operator qty 1:1); car_door_panel "
            "quantities are listed separately below since panels don't always use the opening count "
            "directly (a center-opening/2C group is 2 panels per opening)."
        )
        lines.append("Per-car opening count:")
        lines.extend(f"- {l}" for l in breakdown["per_car_lines"])
        lines.append("")
        lines.append(
            f"Door operator / clutch / detector / restrictor groups (total openings = "
            f"{breakdown['total_openings']}):"
        )
        lines.extend(f"- {l}" for l in breakdown["door_operator_lines"])
        lines.append("")
        lines.append("Car door panel groups:")
        lines.extend(f"- {l}" for l in breakdown["panel_lines"])
        lines.append("")

    gov_breakdown = candidate.get("governor_breakdown")
    if gov_breakdown:
        lines.append(
            "## COMPUTED governor breakdown (derived directly from each car's own Drive Method "
            "field above, not by the model) — AUTHORITATIVE. Governors are one per CAR (not per "
            "opening). Use these exact quantities and DO NOT combine the two groups below into a "
            "single governor line/variant even if their price looks similar — a gearless/high-"
            "capacity car takes a genuinely different, more expensive governor product than the "
            "rest of the project, confirmed on this business's own real reference quotes."
        )
        lines.extend(f"- {l}" for l in gov_breakdown["per_car_lines"])
        lines.append(
            f"Total cars needing STANDARD governor: {gov_breakdown['n_standard']} "
            f"(cars {', '.join(gov_breakdown['standard_designations']) or 'none'})"
        )
        lines.append(
            f"Total cars needing GEARLESS/high-capacity governor: {gov_breakdown['n_gearless']} "
            f"(cars {', '.join(gov_breakdown['gearless_designations']) or 'none'})"
        )
        lines.append("")

    if candidate.get("equipment_scope_table"):
        lines.append(
            "## Equipment scope table (from the spec) — AUTHORITATIVE New vs Refurbish/Retain/None "
            "decision per category. If a category is listed here as Refurbish, Retain, or None, do "
            "NOT add a new-equipment line item for it, even if you'd otherwise expect a typical "
            "modernization to include one. Only categories marked New (or not listed at all, if "
            "independently confirmed elsewhere) are in scope for new equipment."
        )
        lines.append(candidate["equipment_scope_table"])
        lines.append("")

    if candidate["equipment_manifest"]:
        lines.append("## Equipment manifest / existing-equipment questionnaire (from the spec)")
        lines.append(candidate["equipment_manifest"])
        lines.append("")

    if candidate["subsections"]:
        lines.append("## Targeted technical subsections (from the spec)")
        for title, body in candidate["subsections"].items():
            lines.append(f"### {title}")
            lines.append(body)
            lines.append("")

    if candidate["used_fallback"] and candidate["fallback_text"]:
        lines.append(
            "## Keyword-proximity excerpts (heading-based targeting found little — "
            "this may be a differently structured spec, e.g. new construction)"
        )
        lines.append(candidate["fallback_text"])
        lines.append("")

    return "\n".join(lines).strip()
