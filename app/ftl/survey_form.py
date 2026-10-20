"""Wittur SGV survey-form reader.

Answers live in PDF form fields, not the printed text layer. Shared by the Qualifier
and the Quote Estimator so both agents see the typed answers instead of the blank template.
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional

import pdfplumber

# ---------------------------------------------------------------------------------------------
# Wittur "SGV Car Door Modernization Order/Survey Form" — the answers are typed into the PDF's
# FORM FIELDS, not printed in its text layer, so extract_pdf_text() only ever sees the blank
# template (confirmed on '82 Lorne': the agent saw no quantity, door type, size or detector and
# invented 5 operators). This reads the filled fields, labels each one from the form's own row
# captions and config columns, and renders a section that is appended to the candidate text.
# Shared by the Estimator and the Qualifier.
# ---------------------------------------------------------------------------------------------
SURVEY_FORM_MARKER = "## Wittur SGV survey form"
SURVEY_FACTS_PREFIX = "SURVEY_FACTS_JSON: "
_WITTUR_FORM_SIGNATURE_RE = re.compile(r"SGV\s+Car\s+Door\s+Modernization\s+Order/Survey\s+Form", re.IGNORECASE)
# x ranges of Config #1..#6 on every grid page of the template
_SURVEY_GRID_EDGES = [175.0, 243.3, 307.0, 371.0, 434.5, 493.0, 565.0]


def _decode_pdf_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(x for x in (_decode_pdf_value(i) for i in v) if x)
    if hasattr(v, "name"):  # a PDF name object (checkbox state, e.g. /Off, /Yes)
        return str(v.name)
    if isinstance(v, bytes):
        if v[:2] in (b"\xfe\xff", b"\xff\xfe"):
            try:
                return v.decode("utf-16").strip()
            except UnicodeError:
                pass
        return v.decode("latin-1").strip()
    return str(v).strip()


def read_pdf_form_widgets(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """Every filled form widget in the PDF: page, field name, value, geometry (points, y from top)."""
    from pdfminer.pdftypes import resolve1

    def field_value(data: Dict[str, Any]) -> Any:
        node, v, hops = data, None, 0
        while node is not None and v is None and hops < 6:
            v = node.get("V")
            parent = node.get("Parent")
            node = resolve1(parent) if parent is not None else None
            hops += 1
        return resolve1(v) if v is not None else None

    out: List[Dict[str, Any]] = []
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                for a in page.annots or []:
                    data = a.get("data") or {}
                    if resolve1(data.get("Subtype")) is not None and getattr(resolve1(data.get("Subtype")), "name", "") != "Widget":
                        continue
                    value = _decode_pdf_value(field_value(data))
                    if value in ("", "Off") or not value.strip():
                        continue
                    out.append(
                        {
                            "page": pno,
                            "name": a.get("title") or "",
                            "value": value,
                            "x0": float(a["x0"]),
                            "x1": float(a["x1"]),
                            "top": float(a["top"]),
                            "width": float(a["x1"]) - float(a["x0"]),
                        }
                    )
    except Exception:
        return []
    return out


def _survey_margin_lines(pdf_bytes: bytes) -> Dict[int, List[Dict[str, Any]]]:
    """Per page: the row captions in the form's left column (words left of the grid), as lines."""
    result: Dict[int, List[Dict[str, Any]]] = {}
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            words = [w for w in page.extract_words(keep_blank_chars=False) if w["x0"] < 172]
            lines: List[Dict[str, Any]] = []
            for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
                if lines and abs(lines[-1]["top"] - w["top"]) <= 2.5:
                    lines[-1]["text"] += " " + w["text"]
                else:
                    lines.append({"top": w["top"], "text": w["text"]})
            result[pno] = lines
    return result


def _survey_col(w: Dict[str, Any]) -> Optional[int]:
    if w["x0"] < 175 or w["width"] > 70:
        return None
    cx = (w["x0"] + w["x1"]) / 2
    for i in range(6):
        if _SURVEY_GRID_EDGES[i] <= cx < _SURVEY_GRID_EDGES[i + 1]:
            return i + 1
    return None


_ROW_KEYS = [
    (r"^1\s+Contact", "contact"),
    (r"^2\s+Company", "company"),
    (r"^3\s+Project", "project"),
    (r"^4\s+Elevator", "elevator"),
    (r"^5a\b", "operator_qty"),
    (r"^5b\b", "landing_track_qty"),
    (r"^6\s+Door\s+Type", "door_type"),
    (r"^7\s+Clear\s+Opening", "clear_opening_in"),
    (r"^Clear\s+Height", "clear_height_in"),
    (r"^8a\b", "otis_landing_doors"),
    (r"^8\s+Clutch", "clutch_interface"),
    (r"^9\s+Operator\s+Mounting", "mounting"),
    (r"^10a\b", "cab_panel"),
    (r"^10b\b", "cab_panel_interface"),
    (r"^10c\b", "controller"),
    (r"^10d\b", "detector"),
    (r"^10e\b", "landing_panel"),
    (r"^10f\b", "landing_panel_interface"),
    (r"^11\b", "programming_tool_qty"),
    (r"^12\b", "notes"),
]
_ROW_LABELS = {
    "operator_qty": "5a Door operator quantity",
    "landing_track_qty": "5b Landing track quantity",
    "door_type": "6 Door type / clutch location",
    "clear_opening_in": "7 Clear opening (CO) width, in",
    "clear_height_in": "7 Clear height (CH), in",
    "otis_landing_doors": "8a Otis landing door qty",
    "clutch_interface": "8 Clutch interface",
    "mounting": "9 Operator mounting",
    "cab_panel": "10a Cab door panel",
    "cab_panel_interface": "10b Cab door panel interface",
    "controller": "10c Main elevator controller",
    "detector": "10d Door protection (Formula Systems)",
    "landing_panel": "10e Landing door panel",
    "landing_panel_interface": "10f Landing door panel interface",
    "programming_tool_qty": "11 Programming tool qty",
    "notes": "12 Notes",
}
# rows that must be answered for a configuration that is in use
_REQUIRED_ROWS = ("operator_qty", "door_type", "clear_opening_in", "clear_height_in", "clutch_interface", "cab_panel", "cab_panel_interface")
_PANEL_NEW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[- ]?\s*([A-F])\s*$", re.IGNORECASE)


def _row_key(text: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", text).strip()
    for pat, key in _ROW_KEYS:
        if re.search(pat, t, re.IGNORECASE):
            return key
    return None


def _num(s: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else None


def _panel_meaning(v: str) -> str:
    v = (v or "").strip()
    if v == "":
        return "NOT SELECTED"
    if v == "0":
        return "0 = retain existing"
    m = _PANEL_NEW_RE.match(v)
    if m:
        return f"{v} = NEW double-skin panel ({m.group(1)} in thick, finish {m.group(2).upper()})"
    return v


def parse_wittur_survey_form(pdf_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Structured answers from a filled Wittur SGV survey PDF, or None if this is not that form (or
    cannot be read). {'filled': bool, 'contact', 'company', 'project', 'configs': {n: {...}}, ...}"""
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            sig = any(_WITTUR_FORM_SIGNATURE_RE.search(p.extract_text() or "") for p in pdf.pages[:6])
    except Exception:
        return None
    if not sig:
        return None
    widgets = read_pdf_form_widgets(pdf_bytes)
    margin = _survey_margin_lines(pdf_bytes)
    facts: Dict[str, Any] = {"filled": False, "configs": {}, "contact": {}, "company": "", "project": "", "custom_clutch": {}, "bolt_survey": []}
    cfg: Dict[int, Dict[str, Any]] = facts["configs"]
    # widgets that sit in the caption column are themselves captions (e.g. a text box holding "10c Main Elevator Controller")
    caption_widgets: Dict[int, List[Dict[str, Any]]] = {}
    for w in widgets:
        if w["x0"] < 130:
            caption_widgets.setdefault(w["page"], []).append({"top": w["top"], "text": w["value"]})
    for w in widgets:
        if w["x0"] < 130:
            continue
        lines = list(margin.get(w["page"], [])) + caption_widgets.get(w["page"], [])
        near = min(lines, key=lambda l: abs(l["top"] - w["top"]), default=None)
        label = near["text"] if near and abs(near["top"] - w["top"]) <= 9 else ""
        key = _row_key(label)
        col = _survey_col(w)
        val = w["value"].strip()
        if w["page"] == 3 and w["top"] > 705 and key is None:  # the 10d detector row has its own little layout
            key = "detector"
        if w["page"] >= 5:
            if w["page"] == 5 and label:
                facts["custom_clutch"][re.sub(r"[^A-Za-z]+", " ", label).strip()] = val
            elif w["page"] >= 6:
                short = re.search(r"\b([A-Z]{2,3})=", label)
                facts["bolt_survey"].append((short.group(1) if short else f"field@{int(w['top'])}") + "=" + val)
            continue
        if key is None:
            continue
        if key == "contact":
            c = facts["contact"]
            if "@" in val:
                c["email"] = val
            elif re.search(r"\d{3}[-. )]*\d{3}[-. ]*\d{4}", val):
                c["phone"] = val
            else:
                c["name"] = val
            continue
        if key in ("company", "project"):
            facts[key] = val
            continue
        if key == "controller":
            facts["controller"] = val
            continue
        if key == "detector":
            d = facts.setdefault("detector", {"type": "", "qty": None})
            if w["x0"] >= 262 and val != "#":
                d["qty"] = _num(val)
            elif val != "#" and not re.match(r"^10d", val):
                d["type"] = val
            continue
        if col is None:
            continue
        c = cfg.setdefault(col, {})
        if key == "elevator":
            if w["width"] < 20 and re.fullmatch(r"\d+", val):
                c["elevator"] = val
            else:
                c["side"] = val
        else:
            c[key] = val
    # numeric / derived fields; which configurations are actually in use
    for n, c in list(cfg.items()):
        for k in ("operator_qty", "landing_track_qty", "programming_tool_qty", "clear_opening_in", "clear_height_in", "otis_landing_doors"):
            if k in c:
                c[k] = _num(c[k])
        dt = str(c.get("door_type") or "").strip().upper()
        m = re.fullmatch(r"(1S|2C|2T|4C)-([LRD])", dt)
        if m:
            c["door_family"], c["hand"] = m.group(1), m.group(2)
        c["active"] = bool((c.get("operator_qty") or 0) > 0 or dt)
    active = {n: c for n, c in cfg.items() if c.get("active")}
    facts["configs"] = {str(n): c for n, c in sorted(active.items())}
    facts["filled"] = any((c.get("operator_qty") or 0) > 0 for c in active.values())
    facts["operator_total"] = sum(c.get("operator_qty") or 0 for c in active.values())
    facts["missing"] = [
        f"Config #{n}: {_ROW_LABELS[k]}" for n, c in sorted(active.items()) for k in _REQUIRED_ROWS if c.get(k) in (None, "")
    ]
    return facts


def extract_survey_form_section(pdf_bytes: bytes) -> str:
    """The section to append to the candidate text for a Wittur survey PDF ('' for any other PDF)."""
    facts = parse_wittur_survey_form(pdf_bytes)
    if facts is None:
        return ""
    lines: List[str] = []
    if not facts["filled"]:
        lines += [
            f"{SURVEY_FORM_MARKER} — NOT FILLED IN",
            "The PDF's form fields contain NO operator quantity, door type or opening size. Nothing about this "
            "project's equipment can be taken from the form — the printed form text elsewhere in the spec is only the "
            "blank template. Do NOT estimate quantities, door types or sizes from it.",
        ]
    else:
        lines += [
            f"{SURVEY_FORM_MARKER} — answers typed into the PDF form fields (AUTHORITATIVE for this enquiry; the printed "
            "form text elsewhere in the spec is the BLANK TEMPLATE and says nothing about this project)",
        ]
        c0 = facts["contact"]
        lines.append("1 Contact (survey): " + " | ".join(x for x in (c0.get("name"), c0.get("email"), c0.get("phone")) if x))
        lines.append(f"2 Company (survey): {facts['company']}")
        lines.append(f"3 Project reference / address (survey): {facts['project']}")
        for n, c in facts["configs"].items():
            lines.append(f"Config #{n} — elevator {c.get('elevator', '?')}, side {c.get('side', '?')}:")
            for k in _ROW_LABELS:
                if k in ("operator_qty", "landing_track_qty", "door_type", "clear_opening_in", "clear_height_in", "clutch_interface", "mounting", "cab_panel", "cab_panel_interface", "landing_panel", "landing_panel_interface", "programming_tool_qty", "notes", "otis_landing_doors"):
                    v = c.get(k)
                    if k in ("cab_panel", "landing_panel"):
                        v = _panel_meaning(str(v or ""))
                    elif v in (None, ""):
                        v = "NOT FILLED IN" if k in _REQUIRED_ROWS else "(blank)"
                    elif isinstance(v, float):
                        v = f"{v:g}"
                    lines.append(f"  {_ROW_LABELS[k]}: {v}")
        if facts.get("controller"):
            lines.append(f"10c Main elevator controller: {facts['controller']}")
        d = facts.get("detector") or {}
        lines.append(f"10d Door protection (Formula Systems): {d.get('type') or '(blank)'}, qty {format(d['qty'], 'g') if d.get('qty') is not None else '(blank)'}")
        if facts["custom_clutch"]:
            lines.append("Custom clutch / coupler survey (page 5) was ALSO filled in: " + "; ".join(f"{k} = {v}" for k, v in facts["custom_clutch"].items()))
        if facts["bolt_survey"]:
            lines.append(f"Operator & pendent bolt survey (page 6): {len(facts['bolt_survey'])} measurements filled — " + ", ".join(facts["bolt_survey"][:12]))
        if facts["missing"]:
            lines.append("Required answers NOT filled in: " + "; ".join(facts["missing"]))
    lines.append(SURVEY_FACTS_PREFIX + json.dumps(facts, separators=(",", ":"), ensure_ascii=False))
    return "\n".join(lines)


def survey_facts_from_text(candidate_text: str) -> Optional[Dict[str, Any]]:
    """The facts parsed out of a candidate text that contains extract_survey_form_section()'s output."""
    for raw in (candidate_text or "").splitlines():
        if raw.startswith(SURVEY_FACTS_PREFIX):
            try:
                return json.loads(raw[len(SURVEY_FACTS_PREFIX):])
            except ValueError:
                return None
    return None


def strip_survey_template_text(candidate_text: str) -> str:
    """Candidate text without the Wittur form's printed (blank-template) content — used by decisions
    that must not read the template's option lists ('New Double skin…', 'CH required if new car
    door panels…') as if the customer had said them. Keeps the email block and the survey section."""
    text = candidate_text or ""
    if SURVEY_FORM_MARKER not in text:
        return text
    keep: List[str] = []
    block = ""
    for raw in text.splitlines():
        if raw.startswith("## "):
            block = raw
        if block.startswith("## Email") or block.startswith(SURVEY_FORM_MARKER):
            keep.append(raw)
    return "\n".join(keep)


def apply_survey_form(rendered_candidate_text: str, pdf_bytes: Optional[bytes]) -> str:
    """If the spec PDF is a Wittur survey form, the model gets the email plus the customer's typed
    answers — NOT the form's blank template text (whose option lists would read as requests)."""
    if not pdf_bytes:
        return rendered_candidate_text
    try:
        section = extract_survey_form_section(pdf_bytes)
    except Exception:
        return rendered_candidate_text
    if not section:
        return rendered_candidate_text
    head, _sep, _tail = rendered_candidate_text.partition("## Detected structure signal")
    return head.rstrip() + "\n\n" + section + "\n"


def apply_survey_form_from_pdfs(
    rendered_candidate_text: str, pdf_bytes_list: Optional[Iterable[bytes]] = None
) -> str:
    """First matching Wittur survey PDF wins (an EML may carry several attachments)."""
    text = rendered_candidate_text
    for pdf_bytes in pdf_bytes_list or []:
        if not pdf_bytes:
            continue
        next_text = apply_survey_form(text, pdf_bytes)
        if next_text != text:
            return next_text
    return text
