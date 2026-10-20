"""
FTL's THREE-QUOTE rule (rulebook v1.1, CORE-004 / QUOTE-001..004, workbook sheet "3 Quote Logic"):

  Quote 1  SPEC / BASE COMPLIANT   exactly what the customer asked FTL to price — confirmed scope
                                   only, no typical-MOD add-ons, no alternates, no guessed equipment.
  Quote 2  FTL RECOMMENDED         Quote 1 plus ONLY the branches an approved FTL upgrade rule
                                   authorises. No approved rule => "NO VALID RECOMMENDED VARIANT".
  Quote 3  ALTERNATE / COMPLETE    only a real alternate defined by the RFQ / consultant / customer,
                                   or a broader approved FTL configuration. Never filled for the sake
                                   of having three => "NO VALID THIRD OPTION".

All three belong to one project and are compared side by side (added / removed lines, quantity and
configuration changes, price difference, reason, outstanding review flags).

Quote 1 comes from the model + the deterministic pipeline in agent.py. Quotes 2 and 3 are derived
from Quote 1 HERE, in plain Python, from an explicit list of approved rules — there is no model
call, so they can never "invent" an upgrade, and the same Quote 1 always gives the same Quote 2/3.
Every derivation names the rulebook rule that authorises it. Adding a new approved recommendation
means adding one entry to RECOMMENDATION_RULES with its rule IDs.

Also here: duplicate-and-revise (manual edit of a copy; the original is never touched).
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.ftl.qualifier import scope_rules
from app.ftl.quote_estimator import quote_controls as qc
from app.ftl.quote_estimator import quote_template
from app.ftl.quote_estimator import rules_engine

VARIANT_LABELS = {
    1: "Quote 1 — Customer spec (base)",
    2: "Quote 2 — FTL recommended",
    3: "Quote 3 — FTL complete / alternate",
}
STATUS_GENERATED = "GENERATED"
STATUS_NO_RECOMMENDED = "NO VALID RECOMMENDED VARIANT"
STATUS_NO_THIRD = "NO VALID THIRD OPTION"
STATUS_ALT_MANUAL = "ALTERNATE DETECTED — ESTIMATOR TO BUILD"


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def _lines(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    return q.get("line_items") or []


def _qty(it: Dict[str, Any]) -> float:
    try:
        return float(it.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


_OP_RE = re.compile(r"SGV2_DOOR_OP_(1S|2C|2T)(\d+)", re.IGNORECASE)


def _operator_groups(q: Dict[str, Any]) -> Dict[Tuple[str, str], float]:
    groups: Dict[Tuple[str, str], float] = {}
    for it in _lines(q):
        if it.get("category") != "door_operator":
            continue
        m = _OP_RE.search(str(it.get("product_code") or ""))
        if m:
            key = (m.group(1).upper(), m.group(2))
            groups[key] = groups.get(key, 0) + _qty(it)
    return groups


def infer_category(code: str) -> str:
    """Line category for a manually added SKU, from its Product Master code (only used when the
    estimator did not choose one)."""
    c = (code or "").upper()
    rules = (
        (r"SGV2_DOOR_OP_", "door_operator"),
        (r"CLUTCH", "clutch"),
        (r"ADAPTOR|ADAPTER", "panel_adaptor"),
        (r"UNIVERSAL_CAR_DOOR_PANEL", "car_door_panel"),
        (r"^WRG|ROLLER", "roller_guide"),
        (r"^WG_|GOVERNOR|TENSION", "governor"),
        (r"SAFET|^WS_", "car_safety"),
        (r"DET|VISIONPLUS", "door_protective_device"),
        (r"TOOL", "door_tools"),
    )
    for pat, cat in rules:
        if re.search(pat, c):
            return cat
    return "other"


def _new_line(row: Dict[str, Any], category: str, qty: float, description: str, note: str, origin: str = "FTL_RULE") -> Dict[str, Any]:
    return {
        "product_code": row["ftl_product_code"],
        "description": description,
        "category": category,
        "qty": qty,
        "unit_price": float(row.get("price_cad") or 0),
        "needs_engineering_review": True,
        "note": note,
        "origin": origin,
    }


def _refinish(derived: Dict[str, Any], candidate_text: str, variant: int, reason: str, rules: List[str]) -> Dict[str, Any]:
    """Re-run the v1.1 controls on a derived quote so its prices, traces, holds and release status
    are computed exactly as for Quote 1 (the car-door decision is NOT re-run: the derivation itself
    is the decision)."""
    derived = qc.apply_inflow_price_authority(derived)
    derived = qc.reconcile_opening_counts(derived, candidate_text)
    derived = qc.annotate_line_traceability(derived, candidate_text)
    derived["internal_notes"] = qc.build_internal_notes(derived, candidate_text)
    label, reasons = qc.release_status(derived)
    derived["release_status"] = label
    derived["release_reasons"] = reasons
    derived["rulebook_version"] = rules_engine.rulebook_version()
    derived["price_basis"] = rules_engine.INFLOW_SOURCE_LABEL
    derived["variant"] = variant
    derived["variant_label"] = VARIANT_LABELS[variant]
    derived["variant_reason"] = reason
    derived["variant_rules"] = rules
    return derived


# ---------------------------------------------------------------------------------------------
# Approved recommendation / alternate rules. Each: (applies?, transform) over a COPY of Quote 1.
# ---------------------------------------------------------------------------------------------
_GAL_CONVERSION_RE = re.compile(
    r"(convert\w*|replac\w*|new|upgrad\w*)[^.\n]{0,60}(landing|hoistway|hall)[^.\n]{0,40}(lock|interlock)s?[^.\n]{0,60}\bGAL\b"
    r"|\bGAL\b[^.\n]{0,40}(landing|hall)[^.\n]{0,20}(lock|interlock)s?[^.\n]{0,40}(convert|replac|new|install)",
    re.IGNORECASE,
)


def _universal_door_branch_applies(q: Dict[str, Any], candidate_text: str) -> Tuple[bool, str]:
    """DOOR-005 / QUOTE-002: when the landing locks are converted to GAL, new Wittur universal car
    doors are FTL's preferred, easier-install solution and are labelled FTL RECOMMENDED — unless
    they are already spec-required (DOOR-004: a spec that skins / replaces the doors puts them in
    Quote 1 itself) or the RFQ prices them only as an alternate (that is Quote 3's job, QUOTE-003).
    Never fires on a contradictory car-door statement (UNCLEAR): that is a hold, not a recommendation."""
    cats = {it.get("category") for it in _lines(q)}
    if "door_operator" not in cats or "car_door_panel" in cats:
        return False, ""
    decision = qc.car_door_decision(candidate_text)["decision"]
    if decision in ("replace", "alternate_only"):
        return False, ""
    if decision == "unclear" and qc.car_door_decision(candidate_text)["base_replace"]:
        return False, ""
    if _GAL_CONVERSION_RE.search(candidate_text or ""):
        return True, "the landing locks are being converted to GAL, where new universal car doors are FTL's preferred, easier-install solution (DOOR-005)"
    return False, ""


def _swap_adaptors_for_universal_doors(q: Dict[str, Any], why: str, rule_label: str) -> Tuple[Dict[str, Any], List[str]]:
    q = copy.deepcopy(q)
    groups = _operator_groups(q)
    added: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for (dtype, width), qty in groups.items():
        row = rules_engine.universal_panel_row(dtype, width)
        if not row or qty <= 0:
            skipped.append(f"{dtype} {width} in (no stocked universal door at 84 in)")
            continue
        panels = qty * (1 if dtype == "1S" else 2)
        added.append(
            _new_line(
                row,
                "car_door_panel",
                panels,
                row.get("description") or row["ftl_product_code"],
                f"ℹ {rule_label}: {why}. Height 84 in (standard) and finish #4 brushed are assumed — confirm clear height and finish "
                "(DOOR-004). Replaces the OEM panel adaptor line(s).",
            )
        )
    if not added:
        return q, skipped
    q["line_items"] = [it for it in _lines(q) if it.get("category") != "panel_adaptor"] + added
    q["held_items"] = [h for h in (q.get("held_items") or []) if h.get("item") != "Car door panel adaptor"]
    return q, skipped


def _detector_branch_applies(q: Dict[str, Any], candidate_text: str) -> Tuple[bool, str]:
    """DET-001 / QUOTE-002 (JR's example): the spec does not call for a 3D edge and retains the
    existing detection — FTL's recommendation is the Formula VisionPlus 3D package with its 24 V
    (18-30 VDC) plug-and-play connectivity. Never when the customer says detection is not required
    from FTL (their call), nor when Quote 1 already has the detector."""
    cats = {it.get("category") for it in _lines(q)}
    if "door_operator" not in cats or "door_protective_device" in cats:
        return False, ""
    if scope_rules.detector_in_scope(candidate_text):
        return False, ""
    for _label, excluded in scope_rules.not_required_from_ftl(candidate_text or ""):
        if "door_protective_device" in excluded:
            return False, ""
    return True, "the specification does not call for a 3D door-edge detector and keeps the existing one (DET-001)"


def _add_detectors(q: Dict[str, Any], why: str) -> Dict[str, Any]:
    q = copy.deepcopy(q)
    row = rules_engine.detector_row()
    ops = sum(_qty(it) for it in _lines(q) if it.get("category") == "door_operator")
    if not row or ops <= 0:
        return q
    q["line_items"] = _lines(q) + [
        _new_line(
            row,
            "door_protective_device",
            ops,
            row.get("description") or row["ftl_product_code"],
            f"ℹ FTL recommendation: Formula VisionPlus 3D detector, one per SGV2 opening — {why}. Benefit: 24 V (18–30 VDC, NPN "
            "normally-closed output) plug-and-play connectivity with the SGV2 operator (Formula FZU-1442US01); no separate power supply "
            "is added automatically (DET-002).",
        )
    ]
    return q


# (key, rule ids, applies(q, text) -> (bool, why), transform(q, why) -> q, short title)
RECOMMENDATION_RULES: List[Dict[str, Any]] = [
    {
        "key": "universal_car_doors",
        "rules": ["DOOR-004", "DOOR-005", "QUOTE-002"],
        "title": "New Wittur universal car doors",
        "applies": _universal_door_branch_applies,
        "apply": lambda q, why: _swap_adaptors_for_universal_doors(q, why, "FTL recommended")[0],
    },
    {
        "key": "formula_3d_detector",
        "rules": ["DET-001", "DET-002", "QUOTE-002"],
        "title": "Formula VisionPlus 3D detector (24 V plug-and-play)",
        "applies": _detector_branch_applies,
        "apply": _add_detectors,
    },
]


_ALT_SENT_RE = re.compile(r"\b(alternate|alternative|add\s+alternate|alt\.)\b", re.IGNORECASE)


def alternates_in_rfq(candidate_text: str) -> List[str]:
    """Sentences of the customer's email / spec that define an alternate. Used to tell Quote 3
    whether a source-defined alternate exists at all."""
    out: List[str] = []
    for raw in re.split(r"[.\n;]+", candidate_text or ""):
        s = raw.strip()
        if len(s) > 8 and _ALT_SENT_RE.search(s) and not re.search(r"alternate\s+(?:power|supply|current)|or\s+alternate", s, re.IGNORECASE):
            out.append(s[:200])
    return out[:5]


# ---------------------------------------------------------------------------------------------
# Public: derive Quote 2 and Quote 3 from Quote 1
# ---------------------------------------------------------------------------------------------
def derive_recommended(base: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """{'status', 'reason', 'result'|None, 'rules'} for Quote 2."""
    working = copy.deepcopy(base)
    applied: List[Dict[str, Any]] = []
    for rule in RECOMMENDATION_RULES:
        ok, why = rule["applies"](working, candidate_text)
        if not ok:
            continue
        changed = rule["apply"](working, why)
        if changed.get("line_items") != working.get("line_items"):
            working = changed
            applied.append({"rule": rule, "why": why})
    if not applied:
        return {
            "status": STATUS_NO_RECOMMENDED,
            "reason": (
                "No approved FTL recommendation rule applies to this scope (QUOTE-002): the base quote already contains the FTL "
                "solution, or the trigger conditions are not met. An upgrade is never invented to raise the quote value."
            ),
            "result": None,
            "rules": ["QUOTE-002"],
        }
    reason = "Quote 1 plus: " + "; ".join(f"{a['rule']['title']} — {a['why']}" for a in applied) + "."
    rules = ["QUOTE-002"] + sorted({r for a in applied for r in a["rule"]["rules"] if r != "QUOTE-002"})
    return {
        "status": STATUS_GENERATED,
        "reason": reason,
        "result": _refinish(working, candidate_text, 2, reason, rules),
        "rules": rules,
    }


def derive_alternate(base: Dict[str, Any], recommended: Optional[Dict[str, Any]], candidate_text: str) -> Dict[str, Any]:
    """{'status', 'reason', 'result'|None, 'rules'} for Quote 3."""
    decision = qc.car_door_decision(candidate_text)
    if decision["decision"] == "alternate_only":
        why = (
            "the RFQ prices new universal car doors only as an alternate; the base quote retains the existing doors (QUOTE-003). "
            "Source: " + "; ".join(decision["alt_replace"][:2])
        )
        derived, skipped = _swap_adaptors_for_universal_doors(base, why, "Alternate")
        if derived.get("line_items") != base.get("line_items"):
            # Quote 3 must differ from Quote 2 as well; if the recommended quote already equals it, there is no extra option.
            if not (recommended and recommended.get("line_items") == derived.get("line_items")):
                reason = "Alternate defined by the RFQ: " + why + (f" Not stocked, so left on adaptors: {', '.join(skipped)}." if skipped else "")
                rules = ["QUOTE-003", "DOOR-004", "DOOR-006"]
                return {"status": STATUS_GENERATED, "reason": reason, "result": _refinish(derived, candidate_text, 3, reason, rules), "rules": rules}
    other = [s for s in alternates_in_rfq(candidate_text) if not re.search(r"car[\s-]*door", s, re.IGNORECASE)]
    if other:
        return {
            "status": STATUS_ALT_MANUAL,
            "reason": (
                "The RFQ defines an alternate that the agent cannot price from controlled rules: "
                + " | ".join(other[:2])
                + ". Nothing was invented — use 'Duplicate & revise' on Quote 1 to build it (QUOTE-003)."
            ),
            "result": None,
            "rules": ["QUOTE-003"],
        }
    return {
        "status": STATUS_NO_THIRD,
        "reason": (
            "No consultant / customer alternate and no approved broader FTL configuration applies (QUOTE-003). A third quote is never "
            "filled for the sake of having three."
        ),
        "result": None,
        "rules": ["QUOTE-003"],
    }


def derive_variants(base: Dict[str, Any], candidate_text: str) -> List[Dict[str, Any]]:
    """[Quote 2 info, Quote 3 info] for a finished Quote 1."""
    rec = derive_recommended(base, candidate_text)
    alt = derive_alternate(base, rec.get("result"), candidate_text)
    rec["variant"], alt["variant"] = 2, 3
    return [rec, alt]


# ---------------------------------------------------------------------------------------------
# Side-by-side comparison
# ---------------------------------------------------------------------------------------------
def _totals(q: Dict[str, Any]) -> Dict[str, float]:
    t = quote_template.compute_totals(q)
    return {"subtotal": t["subtotal"], "freight": t["freight_estimate"], "hst": t["hst"], "total": t["total"]}


def _by_category(q: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    cats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for it in _lines(q):
        cats.setdefault(str(it.get("category")), {})[str(it.get("product_code"))] = it
    return cats


def compare_quotes(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """What changes going from quote `a` (usually Quote 1) to quote `b`."""
    ca, cb = _by_category(a), _by_category(b)
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    qty_changes: List[Dict[str, Any]] = []
    price_changes: List[Dict[str, Any]] = []
    config_changes: List[Dict[str, Any]] = []
    for cat in sorted(set(ca) | set(cb)):
        la, lb = ca.get(cat, {}), cb.get(cat, {})
        for code in sorted(set(la) & set(lb)):
            x, y = la[code], lb[code]
            if abs(_qty(x) - _qty(y)) > 0.001:
                qty_changes.append({"category": cat, "product_code": code, "from": _qty(x), "to": _qty(y)})
            if abs(float(x.get("unit_price") or 0) - float(y.get("unit_price") or 0)) > 0.005:
                price_changes.append({"product_code": code, "from": float(x.get("unit_price") or 0), "to": float(y.get("unit_price") or 0)})
        only_a = [la[c] for c in sorted(set(la) - set(lb))]
        only_b = [lb[c] for c in sorted(set(lb) - set(la))]
        # a swap inside one category (adaptor -> universal door uses two categories, handled below)
        while only_a and only_b:
            x, y = only_a.pop(0), only_b.pop(0)
            config_changes.append(
                {"category": cat, "from_code": x.get("product_code"), "from_qty": _qty(x), "to_code": y.get("product_code"), "to_qty": _qty(y)}
            )
        removed.extend(only_a)
        added.extend(only_b)
    # adaptor (panel_adaptor) -> universal door (car_door_panel) is a configuration change across categories
    adaptors = [r for r in removed if r.get("category") == "panel_adaptor"]
    panels = [r for r in added if r.get("category") == "car_door_panel"]
    if adaptors and panels:
        config_changes.append(
            {
                "category": "car door",
                "from_code": ", ".join(str(r.get("product_code")) for r in adaptors),
                "from_qty": sum(_qty(r) for r in adaptors),
                "to_code": ", ".join(str(r.get("product_code")) for r in panels),
                "to_qty": sum(_qty(r) for r in panels),
            }
        )
        removed = [r for r in removed if r not in adaptors]
        added = [r for r in added if r not in panels]
    ta, tb = _totals(a), _totals(b)
    flags_a = {h.get("item") for h in a.get("held_items") or []}
    flags_b = [h for h in b.get("held_items") or [] if h.get("item") not in flags_a]

    def slim(it: Dict[str, Any]) -> Dict[str, Any]:
        return {"product_code": it.get("product_code"), "description": it.get("description"), "qty": _qty(it), "unit_price": float(it.get("unit_price") or 0)}

    return {
        "added": [slim(i) for i in added],
        "removed": [slim(i) for i in removed],
        "qty_changes": qty_changes,
        "price_changes": price_changes,
        "config_changes": config_changes,
        "totals_a": ta,
        "totals_b": tb,
        "delta": {k: round(tb[k] - ta[k], 2) for k in ta},
        "reason": b.get("variant_reason") or b.get("revision_note") or "",
        "review_flags": [f"[{h.get('level', 'HOLD')}] {h.get('item')}" for h in (b.get("held_items") or [])],
        "new_review_flags": [f"[{h.get('level', 'HOLD')}] {h.get('item')}" for h in flags_b],
        "release_status": b.get("release_status", ""),
    }


# ---------------------------------------------------------------------------------------------
# Duplicate & revise — never touches the original
# ---------------------------------------------------------------------------------------------
def apply_manual_edits(
    quote: Dict[str, Any],
    edits: List[Dict[str, Any]],
    *,
    editor: str = "estimator",
    note: str = "",
) -> Dict[str, Any]:
    """Returns an EDITED COPY of `quote`. Each edit is one of
        {"action": "set_qty",   "index": i, "qty": n}
        {"action": "set_price", "index": i, "unit_price": p, "reason": "..."}   # manual price / discount
        {"action": "remove",    "index": i}
        {"action": "add", "product_code": c, "description": d, "category": k, "qty": n, "unit_price": p}
    Indexes refer to the line order of the quote as passed in. Manual price changes are the one
    legitimate route for a discount or special price (COMM-002): they are tagged ESTIMATOR_OVERRIDE,
    recorded with who/why, and listed on the revision. An added line whose code is in the Product
    Master is priced from it unless the edit sets a price explicitly."""
    q = copy.deepcopy(quote)
    items = _lines(q)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    log: List[str] = list(q.get("manual_edits") or [])
    removed_idx = set()
    appended: List[Dict[str, Any]] = []
    for e in edits:
        act = e.get("action")
        idx = e.get("index")
        if act in ("set_qty", "set_price", "remove"):
            if not isinstance(idx, int) or not (0 <= idx < len(items)):
                continue
            it = items[idx]
            code = it.get("product_code")
            if act == "set_qty":
                old = _qty(it)
                new = float(e["qty"])
                if abs(old - new) > 1e-9:
                    it["qty"] = new
                    it["origin"] = "ESTIMATOR_OVERRIDE"
                    qc._append_note(it, f"✎ Quantity changed {old:g} → {new:g} by {editor} ({stamp}).")
                    log.append(f"{code}: qty {old:g} → {new:g}")
            elif act == "set_price":
                old = float(it.get("unit_price") or 0)
                new = float(e["unit_price"])
                if abs(old - new) > 0.005:
                    reason = (e.get("reason") or note or "").strip()
                    it["unit_price"] = new
                    it["subtotal_override"] = None
                    it["origin"] = "ESTIMATOR_OVERRIDE"
                    it["price_source"] = f"Estimator override by {editor} ({stamp})" + (f" — {reason}" if reason else "")
                    it["price_verified"] = False
                    qc._append_note(it, f"✎ Price changed ${old:,.2f} → ${new:,.2f} by {editor}" + (f": {reason}" if reason else "") + ".")
                    log.append(f"{code}: price ${old:,.2f} → ${new:,.2f}" + (f" ({reason})" if reason else ""))
            else:
                removed_idx.add(idx)
                log.append(f"{code}: line removed")
        elif act == "add":
            code = str(e.get("product_code") or "").strip()
            if not code:
                continue
            row = rules_engine.resolve_master_row(code)
            price = e.get("unit_price")
            if price in (None, "") and row:
                price = row.get("price_cad")
            line = {
                "product_code": code,
                "description": e.get("description") or (row or {}).get("description") or code,
                "category": e.get("category") if e.get("category") not in (None, "", "other") else infer_category(code),
                "qty": float(e.get("qty") or 1),
                "unit_price": float(price or 0),
                "origin": "ESTIMATOR_OVERRIDE",
                "needs_engineering_review": row is None,
                "note": f"✎ Line added by {editor} ({stamp}).",
            }
            if row:
                line["price_source"] = row.get("price_source")
                line["price_verified"] = row.get("price_source_kind") == "inflow"
            else:
                line["price_source"] = f"Estimator entry by {editor} — not in the Product Master"
                line["price_verified"] = False
            appended.append(line)
            log.append(f"{code}: line added (qty {line['qty']:g})")
    q["line_items"] = [it for i, it in enumerate(items) if i not in removed_idx] + appended
    q["manual_edits"] = log
    if note:
        q["revision_note"] = note
    return q


def refresh_after_edit(revised: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """Recompute traces / internal notes / release status for an edited copy (the manual edits
    themselves are kept; nothing is re-priced or reverted)."""
    # an estimator edit can fix — or break — the opening-count reconciliation, so redo it from scratch
    revised["held_items"] = [h for h in (revised.get("held_items") or []) if not str(h.get("item", "")).startswith("Opening-count reconciliation")]
    revised["assumptions"] = [a for a in (revised.get("assumptions") or []) if "Opening-count reconciliation" not in str(a)]
    revised = qc.reconcile_opening_counts(revised, candidate_text)
    revised = qc.annotate_line_traceability(revised, candidate_text)
    revised["internal_notes"] = qc.build_internal_notes(revised, candidate_text)
    label, reasons = qc.release_status(revised)
    revised["release_status"] = label
    revised["release_reasons"] = reasons
    return revised
