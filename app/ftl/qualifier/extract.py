"""
RFQ input extraction for the Qualifier agent: turns a raw upload (.eml, or a bare PDF/DOCX spec)
into (a) email metadata if present, and (b) a *shrunk* candidate text — not the full 40-120+ page
tender document — built from deterministic, code-level section targeting.

Why this exists at all, rather than just handing the whole document to the model: the Qualifier
runs on a nano-tier model over documents that regularly run 100+ pages. Section targeting is a
hard, code-level step (same philosophy as chat_agent_retrieval.py's price-redaction backstop —
don't rely on the model alone for something code can do reliably), grounded in the real RFQ
samples: consultant modernization tenders consistently split into three linked spec sections
(commonly numbered 14000/14100/14900) with a recurring, nameable set of FTL-relevant subsections
(Governor & Governor Ropes, Counterweight Guides, Hall Door Equipment, Car Roller Guides, Door
Operators, Door Protective Device, Car Door Clutch, Car Safeties) plus a bidder equipment-manifest
questionnaire. New-construction tenders use a different, single-spec PART 1/2/3 template covering
the whole elevator — that structural difference is itself extracted as a signal.

Also extracted: a "Device count / schedule summary" (how many cars/elevators the tender covers —
useful context for sizing a project even though the Qualifier doesn't compute quantities), and the
"Description/Schedule of Existing Equipment" table and the numbered "New Equipment" scope checklist
— both ported over from auditing the separate Quote Estimator project against real quotes. The
checklist matters here too: a category can appear ONLY in that bare numbered list with no
elaborated prose subsection anywhere else in the spec (a real sample never described "Car Door
Equipment" outside of it), so without capturing it the Qualifier could under-count in-scope
categories purely because nothing else in the document happens to name them.

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
    }


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
    search_pricelist (same pricelist, same bug, as found auditing the separate Quote Estimator
    project — ported here since this file reads the identical source PDF)."""
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
# it needs more room than a normal prose subsection or its later cars/rows get truncated away
# (verified against a real sample: the row stating existing door entrance type sits well past the
# default 600-word cutoff, and is exactly the signal needed to identify the right door category).
# "New Equipment" is a short numbered scope checklist, not prose, but is a hard signal for which
# categories are actually in scope even when a category has no elaborated prose subsection
# elsewhere (a real sample's spec only ever mentioned "Car Door Equipment" inside this checklist).
SUBSECTION_WORD_BUDGET = {
    "Description of Existing Equipment": 1100,
    "Schedule of Existing Equipment": 1100,
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


def _find_device_count_summary(full_text: str) -> str:
    """The short building/bank/machine-type/device-count summary near the top of the spec (e.g.
    'Building Bank Machine Type Number of Devices ... Elevator 6') — the most direct statement of
    how many cars/elevators this tender covers, which helps size/scope the project for a qualify
    decision (e.g. distinguishing a single-elevator job from a large multi-car modernization)."""
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
        body = (email_meta.get("body_text") or "").strip()
        if body:
            lines.append("")
            lines.append(body[:3000])
        lines.append("")

    lines.append(f"## Detected structure signal: {candidate['structure_signal']}")
    lines.append("")

    if candidate.get("device_count_summary"):
        lines.append("## Device count / schedule summary (from the spec)")
        lines.append(candidate["device_count_summary"])
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
