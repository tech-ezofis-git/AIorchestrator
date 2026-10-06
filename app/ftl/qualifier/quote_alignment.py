"""
Makes the Qualifier's item list match what the Quote Estimator would actually quote.

The Qualifier's model reads the RFQ and proposes `matched_items`. The Estimator then turns a
qualified RFQ into a priced quote and, on the way, deterministically drops items that are not in
scope (governors/roller guides nobody asked for, safety gear that is never auto-quoted, the power
supply that is never added) and deterministically adds the items that always travel with others
(clutch + 3D detector + $0 restrictor per door operator, one programming tool, a car door panel or
adaptor, the mandatory governor tension assembly). Two agents making those calls independently —
one from prose instructions — will not stay in agreement, so this module applies the Estimator's
own rules to the Qualifier's list, using the SAME shared code (scope_rules.py, rules_engine.py,
extract.py — identical in both repos) on the SAME evidence (extract.py's candidate text, which
carries the Equipment scope table and the COMPUTED door-package / governor breakdowns).

What it does to a decision's `matched_items`, in order:

  1. Normalises every catalog_ref to the CURRENT price-book code (old spellings are rewritten).
  2. Applies the Estimator's scope gates: car safeties never become a quoted item (they move to
     `hold_items`: in scope for qualification, never auto-quoted); governors / roller guides are
     opt-in; guides refurbished/retained in the scope table, or on HT/lubricated rail or a
     non-passenger car, are dropped; a detector power supply is dropped when an SGV2 operator is
     matched (DET-002; kept only for FTL's confirmed lone-detector case); a new car door panel and a
     panel adaptor are never both listed. Everything removed lands in `excluded_items` with the
     reason, so nothing disappears silently.
  3. Adds what the Estimator always adds: per door operator a clutch, the Formula 3D detector, the
     $0 restrictor; one programming tool; a car door panel when there is neither panel nor adaptor;
     the governor tension assembly per OL governor; a baseline governor when the scope table says
     "Governor: New" and none was listed; default roller guides when the spec calls for them.
     Added rows carry source="estimator_companion" (or "estimator_auto" for a governor/guide the
     Estimator would add on its own).

Added companions never count toward the qualify threshold (a lone operator does not become
"qualified" just because it gets a clutch); a safety-gear item and an auto-added governor/guide do,
because they are real scope signals. agent.py applies the threshold rules.

Nothing here prices anything: the Qualifier's output stays free of dollar amounts.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from app.ftl.qualifier import scope_rules
from app.ftl.qualifier.pricelist_store import car_door_panel_prices
from app.ftl.quote_estimator import rules_engine

# Estimator categories this module recognises (a subset of the Estimator's `category` enum).
DOOR_OPERATOR = "door_operator"
CLUTCH = "clutch"
DETECTOR = "door_protective_device"
RESTRICTOR = "restrictor"
DOOR_TOOLS = "door_tools"
CAR_DOOR_PANEL = "car_door_panel"
PANEL_ADAPTOR = "panel_adaptor"
GOVERNOR = "governor"
GOVERNOR_TENSION = "governor_tension"
ROLLER_GUIDE = "roller_guide"
CAR_SAFETY = "car_safety"
POWER_SUPPLY = "power_supply"
OTHER = "other"

SOURCE_RFQ = "rfq"
SOURCE_COMPANION = "estimator_companion"
SOURCE_AUTO = "estimator_auto"

RESTRICTOR_CODE = "SGV2_CAR_DOOR_RESTRICTOR"
TOOLS_CODE = "SGV2_DOOR_TOOLS"
DETECTOR_CODE = "FS_VISIONPLUS_3D_CAR_DOOR_DET"

_ORDERED_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    (POWER_SUPPLY, ("power supply", "powersupply", "power_supply")),
    (RESTRICTOR, ("restrictor",)),
    (DOOR_TOOLS, ("programming tool", "programing tool", "door tools", "door_tools", "keypad programming")),
    (GOVERNOR_TENSION, ("tension", "swingarm")),
    (DETECTOR, ("detector", "protective device", "light curtain", "visionplus", "3d_light")),
    (GOVERNOR, ("governor",)),
    (ROLLER_GUIDE, ("roller guide", "wrg", "guide roller")),
    (CLUTCH, ("clutch",)),
    (PANEL_ADAPTOR, ("adaptor", "adapter")),
    (CAR_DOOR_PANEL, ("car door panel", "door panel", "universal car door", "car panel")),
    (CAR_SAFETY, ("safeties", "car safety", "safety gear", "unidirectional")),
    (DOOR_OPERATOR, ("door operator", "operator", "sgv2_door_op")),
]

_CODE_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9_().+/-]*[A-Z0-9)]", re.IGNORECASE)
_GOVERNOR_SIZE_RE = re.compile(r"OL\s*[-_]?\s*(35|100)", re.IGNORECASE)


def classify(entry: Dict[str, Any]) -> str:
    """Estimator category for a Qualifier matched_items entry. Looks at item/category/catalog_ref
    first (the note often mentions neighbouring items — "operator with clutch" — so it is only a
    fallback)."""
    primary = " ".join(str(entry.get(k) or "") for k in ("item", "category", "catalog_ref")).lower()
    for cat, kws in _ORDERED_KEYWORDS:
        if any(kw in primary for kw in kws):
            return cat
    note = str(entry.get("note") or "").lower()
    for cat, kws in _ORDERED_KEYWORDS:
        if any(kw in note for kw in kws):
            return cat
    return OTHER


def canonical_catalog_ref(ref: Any) -> Tuple[Optional[str], Optional[str]]:
    """(canonical_ref, old_ref_if_rewritten). Rewrites a catalog_ref that is — or contains — an old
    code the price book renamed to its current code. Anything else is returned unchanged."""
    if not ref or not isinstance(ref, str):
        return (ref if isinstance(ref, str) else None), None
    text = ref.strip()
    canon = rules_engine.canonical_code(text)
    if canon != text:
        return canon, text
    out, rewritten = text, None
    for tok in _CODE_TOKEN_RE.findall(text):
        c = rules_engine.canonical_code(tok)
        if c != tok:
            out = out.replace(tok, c)
            rewritten = tok
    return out, rewritten


def _cite(trigger_id: str) -> str:
    """rules_engine.cite_trigger, minus its 'Required resolution' tail — some of those mention a
    dollar figure, and this agent's output carries no prices."""
    text = re.sub(r"\s*Required resolution:.*$", "", rules_engine.cite_trigger(trigger_id), flags=re.DOTALL)
    return text if "$" not in text else f"[{trigger_id}]"


def _fmt_width(w: Any) -> str:
    try:
        f = float(w)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(w)


def _row_exists(code: str) -> bool:
    return bool(code) and rules_engine.product_master_row(code) is not None


def _entry(
    item: str,
    est_cat: str,
    *,
    catalog_ref: Optional[str],
    qty: Optional[int],
    note: str,
    status: str = "quote",
    source: str = SOURCE_COMPANION,
    match: str = "exact",
    category: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "item": item,
        "category": category or est_cat.replace("_", " "),
        "match": match,
        "catalog_ref": catalog_ref,
        "note": note,
        "estimator_category": est_cat,
        "estimator_qty": qty,
        "quote_status": status,
        "source": source,
    }


_OP_CODE_RE = re.compile(r"SGV2_DOOR_OP_(1S|2C|2T)(\d+)", re.IGNORECASE)


def _panel_code(family: str, width: Any, two_speed_as_2t: bool) -> Optional[str]:
    """The panel's product name exactly as the Estimator would write it: the catalogue's own full
    row text (car_door_panel_prices(), the same lookup the Estimator uses), falling back to the
    Product Master's short code if the searchable index isn't built yet."""
    fam = "2T" if (family == "2C" and two_speed_as_2t) else family
    w = _fmt_width(width)
    try:
        for row in car_door_panel_prices():
            if row["door_type"] == fam and row["width"] == w:
                return row["code"]
    except Exception:
        pass
    for row in rules_engine.universal_door_panel_rows():
        code = row.get("ftl_product_code") or ""
        if code.upper().startswith(f"{fam}_") and f"- {w} X" in code.upper():
            return code
    return None


def _panel_groups(items: List[Dict[str, Any]], groups_info: Dict[str, Any], openings: Optional[int], two_speed: bool):
    """[(family, width, panel_qty|None)] — the door types/widths needing a car door panel. From the
    computed breakdown when the spec has a per-car table; otherwise from the listed operators' own
    codes (SGV2_DOOR_OP_<type><width>_<hand>)."""
    panels = groups_info["panels"]
    if panels:
        return [(p["family"], p["width"], p["qty"]) for p in panels]
    seen: Dict[Tuple[str, str], Optional[int]] = {}
    for e in items:
        if e["estimator_category"] != DOOR_OPERATOR:
            continue
        m = _OP_CODE_RE.search(str(e.get("catalog_ref") or ""))
        if not m:
            continue
        fam, width = m.group(1).upper(), m.group(2)
        if fam == "2C" and two_speed:
            fam = "2T"
        seen.setdefault((fam, width), None)
    out = []
    only_one = len(seen) == 1
    for (fam, width) in seen:
        qty = None
        if only_one and openings is not None:
            qty = openings * (1 if fam == "1S" else 2)
        out.append((fam, width, qty))
    return out


def align_with_estimator(decision: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """Returns a copy of `decision` whose matched_items / excluded_items / hold_items reflect the
    Estimator's quoting scope (see module docstring). Safe to call on any decision dict; never
    raises on odd model output (non-dict entries are passed through untouched)."""
    decision = dict(decision)
    candidate_text = candidate_text or ""
    raw_items = decision.get("matched_items") or []
    excluded: List[Dict[str, Any]] = [dict(e) for e in (decision.get("excluded_items") or []) if isinstance(e, dict)]
    hold: List[Dict[str, Any]] = [dict(h) for h in (decision.get("hold_items") or []) if isinstance(h, dict)]
    log: List[str] = []

    items: List[Dict[str, Any]] = []
    passthrough: List[Any] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            passthrough.append(raw)
            continue
        e = copy.deepcopy(raw)
        cat = classify(e)
        ref, old = canonical_catalog_ref(e.get("catalog_ref"))
        if old:
            e["catalog_ref"] = ref
            e["note"] = (
                (e.get("note") + " " if e.get("note") else "")
                + f"ℹ {ref} is the current price-book code (previously {old})."
            )
        e["estimator_category"] = cat
        e.setdefault("source", SOURCE_RFQ)
        e.setdefault("quote_status", "quote")
        e.setdefault("estimator_qty", None)
        items.append(e)

    def drop(e: Dict[str, Any], reason: str) -> None:
        excluded.append({"item": e.get("item", ""), "reason": reason})
        log.append(f"removed {e.get('item', '')!r}")

    has_operator = any(e["estimator_category"] == DOOR_OPERATOR for e in items)
    scope_excluded = scope_rules.scope_table_excluded_categories(candidate_text)
    has_scope_table = bool(scope_rules.scope_table_rows(candidate_text))
    kept: List[Dict[str, Any]] = []
    for e in items:
        cat = e["estimator_category"]

        if cat == POWER_SUPPLY and has_operator:
            drop(
                e,
                "The Estimator never adds a separate detector power supply to an SGV2 package "
                "(rulebook DET-002; product master 'DO NOT AUTO-ADD') — the matched operator powers the "
                "detector. (A power supply is only listed when a detector is offered WITHOUT an SGV2 "
                "operator, per FTL's own instruction.)",
            )
            continue

        if cat == CAR_SAFETY:
            if CAR_SAFETY in scope_excluded:
                drop(
                    e,
                    f"The RFQ's Equipment scope table says \"{scope_excluded[CAR_SAFETY]}\", so car safeties "
                    "are not in scope (SAFE-001).",
                )
                continue
            hold.append(
                {
                    "item": e.get("item", ""),
                    "category": e.get("category", "car safety"),
                    "match": e.get("match", "ambiguous"),
                    "catalog_ref": e.get("catalog_ref"),
                    "note": e.get("note", ""),
                    "estimator_category": CAR_SAFETY,
                    "quote_status": "hold",
                    "source": SOURCE_RFQ,
                    "reason": (
                        "In scope for qualification, but the Estimator never auto-quotes safety gear: it "
                        "needs engineering selection (Fmax, speed, rail size/condition) before any SKU or "
                        "price (SAFE-001..004). " + _cite("RT-014")
                    ),
                }
            )
            log.append("held car safeties")
            continue

        if has_scope_table and scope_rules.item_matches_suspect_keywords(
            " ".join(str(e.get(k) or "") for k in ("item", "catalog_ref"))
        ):
            drop(
                e,
                "A counterweight tension weight/swingarm/idler line that the RFQ's Equipment scope table "
                "does not call for as new equipment — the Estimator strips these.",
            )
            continue

        if cat == ROLLER_GUIDE:
            if ROLLER_GUIDE in scope_excluded:
                drop(
                    e,
                    f"The RFQ's Equipment scope table says \"{scope_excluded[ROLLER_GUIDE]}\" for the guides — "
                    "not new scope, so the Estimator quotes no roller guides.",
                )
                continue
            if not scope_rules.roller_guide_in_scope(candidate_text):
                drop(
                    e,
                    "The spec never explicitly calls for roller/car guide replacement; roller guides are "
                    "opt-in (\"quote only when scope calls\"), so the Estimator would not quote them.",
                )
                continue
            if scope_rules.roller_guide_invalid_application(candidate_text):
                drop(
                    e,
                    "The spec mentions roll-formed HT / lubricated rail or a non-passenger/unbalanced car — "
                    "WRG roller guides are not valid there. " + _cite("RT-013"),
                )
                continue

        if cat == GOVERNOR and not scope_rules.governor_in_scope(candidate_text):
            drop(
                e,
                "The spec never explicitly calls for new or replacement governor equipment (a generic "
                "\"safeties, governors... as required\" clause does not count); governors are opt-in, so the "
                "Estimator would not quote one.",
            )
            continue

        kept.append(e)
    items = kept

    cats = {e["estimator_category"] for e in items}
    groups_info = scope_rules.computed_door_groups(candidate_text)
    groups: List[Dict[str, Any]] = list(groups_info["groups"])  # type: ignore[arg-type]
    counts = scope_rules.car_and_opening_counts(candidate_text)
    total_openings: Optional[int] = counts["openings"]
    cars: int = int(counts["cars"] or 0)
    two_speed = scope_rules.two_speed_means_2t(candidate_text)

    # --- A new universal panel and an adaptor are never both quoted (Estimator keeps the new panel).
    if CAR_DOOR_PANEL in cats and PANEL_ADAPTOR in cats:
        keep2: List[Dict[str, Any]] = []
        for e in items:
            if e["estimator_category"] == PANEL_ADAPTOR:
                drop(
                    e,
                    "Listed alongside a brand-new car door panel for the same openings; an adaptor is only for "
                    "a RETAINED existing panel, so the Estimator keeps the new panel (PANEL exclusivity). Swap "
                    "if the panels are actually being retained.",
                )
                continue
            keep2.append(e)
        items = keep2
        cats = {e["estimator_category"] for e in items}

    added: List[Dict[str, Any]] = []

    # --- Normalise the items whose code is fully determined (the Estimator rewrites these too).
    for e in items:
        c = e["estimator_category"]
        if c == DETECTOR:
            if e.get("catalog_ref") != DETECTOR_CODE:
                e["catalog_ref"] = DETECTOR_CODE
            e["estimator_qty"] = total_openings
            e["quote_status"] = "quote_review"
        elif c == DOOR_TOOLS:
            e["catalog_ref"] = TOOLS_CODE
            e["estimator_qty"] = 1
        elif c == RESTRICTOR:
            e["catalog_ref"] = RESTRICTOR_CODE
            e["estimator_qty"] = total_openings
            e["quote_status"] = "bundled_no_charge"
        elif c == CLUTCH:
            e["estimator_qty"] = total_openings
        elif c == DOOR_OPERATOR:
            ref = e.get("catalog_ref") or ""
            if two_speed:
                m = re.match(r"^(SGV2_DOOR_OP_)2C(\d+)_(LH|RH|L|R)$", ref, re.IGNORECASE)
                if m:
                    e["catalog_ref"] = f"{m.group(1)}2T{m.group(2)}_{m.group(3)}"
                    e["note"] = (
                        (e.get("note") + " " if e.get("note") else "")
                        + "ℹ Door type read as 2T: the spec states \"Two-speed\" and never mentions centre opening."
                    )
            if not e.get("catalog_ref") and len(groups) == 1:
                g = groups[0]
                fam = "2T" if (g["family"] == "2C" and two_speed) else g["family"]
                guess = f"SGV2_DOOR_OP_{fam}{_fmt_width(g['width'])}_L"
                if _row_exists(guess):
                    e["catalog_ref"] = guess
                    e["note"] = (
                        (e.get("note") + " " if e.get("note") else "")
                        + "ℹ Hand defaulted to left (FTL's standard when the spec is silent) — confirm before quoting."
                    )
            e["estimator_qty"] = total_openings if total_openings is not None else e.get("estimator_qty")
        elif c == CAR_DOOR_PANEL:
            pg = _panel_groups(items, groups_info, total_openings, two_speed)
            if pg and all(q is not None for _f, _w, q in pg):
                e["estimator_qty"] = sum(q for _f, _w, q in pg)  # type: ignore[misc]

    # --- Door package companions: only ever added around a door operator (BUNDLE-001/002).
    if DOOR_OPERATOR in cats:
        have = lambda c: any(i["estimator_category"] == c for i in items + added)  # noqa: E731
        if not have(CLUTCH):
            added.append(
                _entry(
                    "Car door clutch (one per door operator)",
                    CLUTCH,
                    catalog_ref=None,
                    qty=total_openings,
                    note="Estimator adds one clutch per operator (BUNDLE-001); the OEM/hand variant is chosen from "
                    "the existing landing locks when it quotes.",
                )
            )
        if not have(DETECTOR):
            added.append(
                _entry(
                    "Formula 3D VisionPlus car door detector (one per door operator)",
                    DETECTOR,
                    catalog_ref=DETECTOR_CODE,
                    qty=total_openings,
                    status="quote_review",
                    note="Estimator adds one detector per operator (BUNDLE-001/DET-001), with no separate power "
                    "supply (DET-002).",
                )
            )
        if not have(RESTRICTOR):
            added.append(
                _entry(
                    "Car door restrictor (bundled, no charge)",
                    RESTRICTOR,
                    catalog_ref=RESTRICTOR_CODE,
                    qty=total_openings,
                    status="bundled_no_charge",
                    note="Bundled with every SGV2 operator at no charge (BUNDLE-001).",
                )
            )
        if not have(DOOR_TOOLS):
            added.append(
                _entry(
                    "Door programming tool (one per project)",
                    DOOR_TOOLS,
                    catalog_ref=TOOLS_CODE,
                    qty=1,
                    note="One programming tool per project unless the customer already has one (BUNDLE-002).",
                )
            )
        if not have(CAR_DOOR_PANEL) and not have(PANEL_ADAPTOR):
            pg = _panel_groups(items, groups_info, total_openings, two_speed)
            if pg:
                for fam, width, pqty in pg:
                    code = _panel_code(fam, width, two_speed)
                    added.append(
                        _entry(
                            f"Universal car door panel — {fam} {_fmt_width(width)}\"",
                            CAR_DOOR_PANEL,
                            catalog_ref=code,
                            qty=pqty,
                            status="quote_review",
                            note="Estimator defaults to a new universal panel when neither a panel nor an adaptor is "
                            "listed (PANEL-001/002); an adaptor replaces it if the existing panels are retained.",
                        )
                    )
            else:
                added.append(
                    _entry(
                        "Universal car door panel (new), or panel adaptor if the existing panels are retained",
                        CAR_DOOR_PANEL,
                        catalog_ref=None,
                        qty=None,
                        status="quote_review",
                        note="Every operator needs one or the other; the Estimator defaults to a new universal panel "
                        "(PANEL-001/002).",
                    )
                )

    # --- Governor: baseline when the scope table says New and none listed; tension companion per OL governor.
    gov_counts = scope_rules.computed_governor_counts(candidate_text)
    if (
        GOVERNOR not in cats
        and scope_rules.scope_table_confirms_new(candidate_text, scope_rules.GOVERNOR_SCOPE_ROW_KEYWORDS)
        and cars >= 1
    ):
        baseline = "WG_OL35_GOVERNOR_RC"
        if _row_exists(baseline):
            added.append(
                _entry(
                    "Governor (OL35, remote-control baseline)",
                    GOVERNOR,
                    catalog_ref=baseline,
                    qty=cars,
                    status="quote_review",
                    source=SOURCE_AUTO,
                    match="ambiguous",
                    note="The Equipment scope table says Governor: New, so the Estimator adds one governor per car "
                    "(variant per car confirmed at quoting).",
                )
            )

    gov_entries = [e for e in items + added if e["estimator_category"] == GOVERNOR]
    if gov_entries:
        if len(gov_entries) == 1 and gov_entries[0].get("estimator_qty") is None:
            # one governor per CAR (not per opening)
            total = (gov_counts["standard"] + gov_counts["gearless"]) if gov_counts else 0
            gov_entries[0]["estimator_qty"] = total or (cars or None)
        # size -> qty of governors of that size ("?" = size not stated on the item)
        sizes: Dict[str, Optional[int]] = {}
        for e in gov_entries:
            text = " ".join(str(e.get(k) or "") for k in ("item", "category", "catalog_ref", "note"))
            m = _GOVERNOR_SIZE_RE.search(text)
            key = m.group(1) if m else "?"
            if sizes.get(key) is None and key in sizes:
                continue
            q = e.get("estimator_qty")
            sizes[key] = (sizes.get(key) or 0) + q if q is not None else (sizes.get(key) if key in sizes else None)
        if set(sizes) == {"?"} and gov_counts:
            # No size on the item: the computed breakdown says standard cars take OL35, gearless/high-
            # capacity cars take OL100 (the Estimator's own split).
            sizes = {}
            if gov_counts["standard"]:
                sizes["35"] = gov_counts["standard"]
            if gov_counts["gearless"]:
                sizes["100"] = gov_counts["gearless"]
        existing_tension = [i for i in items if i["estimator_category"] == GOVERNOR_TENSION]
        for t in existing_tension:
            m = _GOVERNOR_SIZE_RE.search(" ".join(str(t.get(k) or "") for k in ("item", "catalog_ref")))
            size = m.group(1) if m else (next(iter(sizes)) if len(sizes) == 1 else None)
            if size is not None and t.get("estimator_qty") is None:
                t["estimator_qty"] = sizes.get(size) or None
            t["quote_status"] = "quote_review"
        if not existing_tension:
            known = [s for s in sizes if s != "?"]
            for size in known:
                row = rules_engine.governor_tension_companion(size)
                if row:
                    added.append(
                        _entry(
                            f"Governor tension assembly (OL{size}) — one per governor",
                            GOVERNOR_TENSION,
                            catalog_ref=row["ftl_product_code"],
                            qty=sizes[size] or None,
                            status="quote_review",
                            note="Mandatory companion to every OL governor (GOV-003/GOV-004)."
                            + (" " + _cite("RT-010") if size == "100" else ""),
                        )
                    )
            if not known:
                added.append(
                    _entry(
                        "Governor tension assembly — one per governor (size follows the governor selected)",
                        GOVERNOR_TENSION,
                        catalog_ref=None,
                        qty=gov_entries[0].get("estimator_qty") if len(gov_entries) == 1 else None,
                        status="quote_review",
                        note="Mandatory companion to every OL governor (GOV-003/GOV-004); OL35 and OL100 use "
                        "different assemblies.",
                    )
                )

    # --- Roller guides: spec calls for them, none listed, there are door operators => Estimator adds a default set.
    if (
        ROLLER_GUIDE not in cats
        and DOOR_OPERATOR in cats
        and scope_rules.roller_guide_in_scope(candidate_text)
        and not scope_rules.roller_guide_invalid_application(candidate_text)
        and ROLLER_GUIDE not in scope_excluded
        and cars >= 1
    ):
        for code, label in (("WRG_MOTION_GEAR150", "car"), ("WRG_MOTION_GEAR80", "counterweight")):
            if _row_exists(code):
                added.append(
                    _entry(
                        f"WRG roller guides — {label} set (default frame size)",
                        ROLLER_GUIDE,
                        catalog_ref=code,
                        qty=cars * 4,
                        status="quote_review",
                        source=SOURCE_AUTO,
                        match="ambiguous",
                        note="The spec explicitly calls for roller guide replacement, so the Estimator adds a "
                        "4-piece set per car (WRG-002); frame size is a placeholder until rail/roller diameter is "
                        "confirmed.",
                    )
                )

    # Listed roller guides: one 4-piece set per car (WRG-002) when the car count is stated.
    for e in items:
        if e["estimator_category"] == ROLLER_GUIDE and e.get("estimator_qty") is None and cars >= 1:
            e["estimator_qty"] = cars * 4

    # Any matched entry the model filed under a code the price book doesn't know gets flagged (no guessing).
    for e in items:
        ref = e.get("catalog_ref")
        if ref and isinstance(ref, str) and re.match(r"^[A-Z0-9_().+-]+$", ref) and not _row_exists(ref):
            e["note"] = (
                (e.get("note") + " " if e.get("note") else "")
                + f"⚠ {ref} is not a code in the current price book — verify the catalog match."
            )

    decision["matched_items"] = items + passthrough + added
    decision["excluded_items"] = excluded
    decision["hold_items"] = hold
    removed_n = len(log)
    decision["estimator_alignment"] = {
        "removed": [l for l in log if l.startswith("removed")],
        "held": [l for l in log if l.startswith("held")],
        "added": [a["item"] for a in added],
    }
    if removed_n or added:
        decision["flags"] = list(decision.get("flags") or []) + ["aligned_with_estimator_scope"]
    return decision
