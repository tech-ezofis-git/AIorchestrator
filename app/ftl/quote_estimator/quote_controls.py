"""
FTL rulebook v1.1 (2026-10-16) controls that sit AFTER the model and the Stage 2 enforcement
functions in agent.py. Everything here is deterministic — no model call — so the same quote data
always gives the same result:

  * apply_inflow_price_authority   - every priced line is set from the inFlow record (COMM-001/006/007)
  * enforce_no_automatic_discounts - no override, negative or "discount" line survives (COMM-002/003)
  * enforce_programming_tool       - one per project, never auto-free (BUNDLE-002); insert if missing
  * ensure_core_door_package       - Wittur door RFQ always gets operator + clutch (Seth 4-line Quote 1)
  * apply_controlled_freight       - the single-SGV2 $375 / $450 / manual rule (COMM-004, T-014)
  * ensure_customer_info           - never replace a known customer fact with TBD
  * enforce_survey_form            - Wittur SGV form-field answers are the door-package spec
  * enforce_detector_scope         - Quote 1 keeps a detector only when the RFQ mentions detection
  * enforce_unread_attachments     - HOLD when attached photos / CAD / spreadsheets were not read
  * finalize_quote                 - per-line five-question trace, internal notes, release status

Held items ("HOLD - TECHNICAL REVIEW REQUIRED" and friends) are recorded as structured data in
quote["held_items"] as well as the human-readable assumptions list, so the UI, the comparison view
and the release status all read the same facts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.ftl import survey_form
from app.ftl.qualifier import scope_rules
from app.ftl.quote_estimator import rules_engine
from app.ftl.quote_estimator.extract import UNREAD_ATTACHMENTS_MARKER, has_unread_attachments

# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
_PLACEHOLDER_RE = re.compile(r"^\W*(tbd|n/?a|unknown|not\s+(?:provided|stated|specified)|\[?\s*tbd\b.*)\W*$", re.IGNORECASE)


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or bool(_PLACEHOLDER_RE.match(text)) or text.upper().startswith("[TBD")


def _lines(quote: Dict[str, Any]) -> List[Dict[str, Any]]:
    return quote.get("line_items") or []


def _append_note(item: Dict[str, Any], text: str) -> None:
    current = item.get("note") or ""
    if text in current:  # idempotent: re-running a control must not repeat its note
        return
    item["note"] = ((current + " ") if current else "") + text


def _add_assumption(quote: Dict[str, Any], text: str) -> None:
    items = list(quote.get("assumptions") or [])
    if text not in items:
        items.append(text)
    quote["assumptions"] = items


def add_held_item(
    quote: Dict[str, Any],
    *,
    item: str,
    reason: str,
    rules: List[str],
    needed: str = "",
    level: str = "HOLD",
) -> None:
    """Record something the agent deliberately did NOT price / release, and why. De-duplicated on
    (item, reason). `level` is HOLD (blocks release) or REVIEW (estimator must confirm)."""
    held = list(quote.get("held_items") or [])
    entry = {"item": item, "reason": reason, "rules": list(rules), "needed": needed, "level": level}
    if entry not in held:
        held.append(entry)
    quote["held_items"] = held


def _qty(it: Dict[str, Any]) -> float:
    try:
        return float(it.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def total_qty(quote: Dict[str, Any], category: str) -> float:
    return sum(_qty(it) for it in _lines(quote) if it.get("category") == category)


# ---------------------------------------------------------------------------------------------
# 1. Price authority — live inFlow is the commercial authority (COMM-001, COMM-006, COMM-007)
# ---------------------------------------------------------------------------------------------
UOM_UNCONFIRMED_RE = re.compile(r"VERIFY\s+UOM", re.IGNORECASE)


def apply_inflow_price_authority(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Sets every line's unit_price from its Product Master / inFlow record and stamps where the
    price came from. A price the model, the embedded price list or a historical quote suggested is
    never kept when the rulebook has an exact record for the SKU. A line with NO exact record keeps
    the price it has but is flagged: FTL's rule is that a missing price/SKU is never silently
    released (Pricing Control: 'Missing price/SKU -> HOLD')."""
    quote = dict(quote)
    items = [dict(it) for it in _lines(quote)]
    unverified: List[str] = []
    uom_lines: List[str] = []
    for it in items:
        code = str(it.get("product_code") or "")
        row = rules_engine.resolve_master_row(code)
        if not row:
            # the $0 value line and free-text lines the rulebook does not list
            it["price_source"] = "No exact Product Master / inFlow record — price unverified"
            it["price_verified"] = False
            if _qty(it) > 0 and float(it.get("unit_price") or 0) > 0:
                it["needs_engineering_review"] = True
                _append_note(
                    it,
                    "⚠ NO EXACT inFlow/Product Master RECORD for this code — price unverified; "
                    "confirm the SKU and live inFlow price before release (COMM-001, CORE-003).",
                )
                unverified.append(code)
            continue
        price = row.get("price_cad")
        src = row.get("price_source") or rules_engine.MASTER_SOURCE_LABEL
        sku = row.get("inflow_sku")
        it["price_source"] = src + (f" · SKU {sku}" if sku else "")
        it["price_verified"] = row.get("price_source_kind") == "inflow"
        if sku:
            it["inflow_sku"] = sku
        if price is None:
            it["needs_engineering_review"] = True
            _append_note(it, "⚠ No approved price on file for this SKU — HOLD until a price is published (COMM-001).")
            unverified.append(code)
            continue
        try:
            current = float(it.get("unit_price") or 0)
        except (TypeError, ValueError):
            current = 0.0
        if abs(current - float(price)) > 0.005:
            _append_note(
                it,
                f"ℹ Price set to ${float(price):,.2f} from {src} (was ${current:,.2f}).",
            )
            it["unit_price"] = float(price)
        if row.get("uom") and UOM_UNCONFIRMED_RE.search(str(row.get("uom"))):
            uom_lines.append(code)
            it["needs_engineering_review"] = True
            it["hold"] = True
            _append_note(
                it,
                "⚠ HOLD — 2C/2T universal door: inFlow names this item '2-DOORS/SET' while FTL quotes "
                "use physical panels. Quantity is the physical panel count (2 per opening); the price "
                "shown is the inFlow record's price PER PANEL and is PROVISIONAL until FTL closes "
                "I-004 (RT-017).",
            )
    quote["line_items"] = items
    if uom_lines:
        add_held_item(
            quote,
            item="2C/2T universal car door price basis",
            reason=(
                "inFlow ProductName says '2-DOORS/SET' but FTL's approved quotes use physical panel quantities; "
                "FTL has not normalized the unit of measure or confirmed the price basis (I-004 / RT-017)."
            ),
            rules=["RT-017", "I-004", "PANEL-002"],
            needed="FTL to normalize the inFlow UOM/ProductName and confirm the per-item price basis.",
        )
        _add_assumption(
            quote,
            "\U0001F6D1 HOLD — 2C/2T universal car doors: physical panel quantity kept (2 per opening) but the price "
            "basis is disputed. " + rules_engine.cite_trigger("RT-017") + " " + rules_engine.cite_conflict("I-004"),
        )
    # re-running this control (e.g. on a derived Quote 2) replaces, not stacks, the previous summary
    quote["held_items"] = [h for h in (quote.get("held_items") or []) if not str(h.get("item", "")).startswith("Lines with no exact inFlow record")]
    if unverified:
        add_held_item(
            quote,
            item="Lines with no exact inFlow record: " + ", ".join(dict.fromkeys(unverified)),
            reason="No exact active inFlow / Product Master record, so the price cannot be verified.",
            rules=["COMM-001", "CORE-003"],
            needed="Confirm the SKU exists in inFlow and enter its live price.",
            level="REVIEW",
        )
    return quote


# ---------------------------------------------------------------------------------------------
# 2. No automatic discounts (COMM-002, COMM-003, RT-007, RT-015, T-015)
# ---------------------------------------------------------------------------------------------
_DISCOUNT_LINE_RE = re.compile(r"\b(discount|rebate|incentive|special\s+pric|promo|price\s+break|credit\s+memo)\b", re.IGNORECASE)
_DISCOUNT_MENTION_RE = re.compile(
    r"\b(discount(?:s|ed)?|special\s+pric(?:e|ing)|volume\s+pric(?:e|ing)|price\s+break|price\s+match|bulk\s+pric(?:e|ing))\b",
    re.IGNORECASE,
)
# The ONLY line that is legitimately $0: FTL's customer-facing restriction-function value line.
ZERO_VALUE_LINE_CODES = {"SGV2_CAR_DOOR_RESTRICTOR"}


def enforce_no_automatic_discounts(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """FTL: no discount is ever applied automatically — not from a historical quote, customer
    history, volume or bundle assumptions. Concretely:
      - a negative qty/price or a discount/rebate/incentive line is removed;
      - a subtotal_override that is not the plain qty x price is cleared, except on the one $0
        value line;
      - a line priced at $0 that has a real price on file (e.g. a 'free' programming tool) is
        restored to its price.
    Each change is recorded so an estimator can see what was undone; the estimator can still
    discount manually (the revision editor records who/why)."""
    quote = dict(quote)
    kept: List[Dict[str, Any]] = []
    notes: List[str] = []
    for it in [dict(x) for x in _lines(quote)]:
        code = str(it.get("product_code") or "")
        desc = str(it.get("description") or "")
        try:
            price = float(it.get("unit_price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if _qty(it) < 0 or price < 0 or _DISCOUNT_LINE_RE.search(code) or _DISCOUNT_LINE_RE.search(desc):
            notes.append(f"removed discount/credit line {code or desc!r}")
            continue
        override = it.get("subtotal_override")
        if override is not None and code not in ZERO_VALUE_LINE_CODES:
            try:
                plain = _qty(it) * price
                if abs(float(override) - plain) > 0.005:
                    notes.append(
                        f"cleared a ${float(override):,.2f} subtotal override on {code} (plain subtotal is ${plain:,.2f})"
                    )
                    _append_note(
                        it,
                        "ℹ Subtotal override removed — FTL applies no discount or free-of-charge pricing "
                        "automatically; a manual estimator approval is required (COMM-002).",
                    )
                    it["subtotal_override"] = None
            except (TypeError, ValueError):
                it["subtotal_override"] = None
        row = rules_engine.resolve_master_row(code)
        if (
            row
            and code not in ZERO_VALUE_LINE_CODES
            and (row.get("price_cad") or 0) > 0
            and price == 0
            and _qty(it) > 0
        ):
            it["unit_price"] = float(row["price_cad"])
            it["subtotal_override"] = None
            notes.append(f"restored the price of {code} that had been set to $0")
            _append_note(it, "ℹ Price restored — never free/discounted automatically (COMM-002, BUNDLE-002).")
        kept.append(it)
    quote["line_items"] = kept
    if notes:
        _add_assumption(quote, "ℹ No automatic discounts (COMM-002): " + "; ".join(notes) + ".")
    if candidate_text and _DISCOUNT_MENTION_RE.search(scope_rules._email_block(candidate_text) or candidate_text):
        _add_assumption(
            quote,
            "⚠ REVIEW — the RFQ mentions a discount / special pricing. None was applied: all discounts and "
            "special pricing need manual FTL estimator approval. " + rules_engine.cite_trigger("RT-015"),
        )
        add_held_item(
            quote,
            item="Discount / special pricing mentioned in the RFQ",
            reason="Discounts are never applied automatically (COMM-002 / RT-015).",
            rules=["COMM-002", "RT-015"],
            needed="Written / recorded estimator approval.",
            level="REVIEW",
        )
    return quote


# ---------------------------------------------------------------------------------------------
# 3. Programming tool — one per project, never auto-free (BUNDLE-002, RT-007)
# ---------------------------------------------------------------------------------------------
_TOOL_OWNED_RE = re.compile(
    r"(already\s+(?:own|have)s?|existing|has\s+its\s+own|have\s+our\s+own|own\s+(?:a|our))\s+[^.\n]{0,40}programm?ing\s+(?:tool|unit|keypad)",
    re.IGNORECASE,
)


def enforce_programming_tool(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    quote = dict(quote)
    items = [dict(it) for it in _lines(quote)]
    tools = [it for it in items if it.get("category") == "door_tools" or it.get("product_code") == "SGV2_DOOR_TOOLS"]
    has_operator = any(it.get("category") == "door_operator" for it in items)
    if not tools:
        row = rules_engine.door_tools_row()
        if not has_operator or not row:
            return quote
        items.append(_survey_line(
            row,
            "door_tools",
            1.0,
            "ℹ AUTO-ADDED — one Wittur programming tool per project whenever door operators are quoted (BUNDLE-002).",
            "FTL_RULE",
        ))
        quote["line_items"] = items
        tools = [items[-1]]
    row = rules_engine.product_master_row("SGV2_DOOR_TOOLS") or {}
    price = row.get("price_cad")
    for it in tools:
        if price and (float(it.get("unit_price") or 0) == 0 or it.get("subtotal_override") is not None):
            it["unit_price"] = float(price)
            it["subtotal_override"] = None
            _append_note(
                it,
                "ℹ Charged at the live inFlow price — the programming tool is never auto-provided at $0 "
                "(BUNDLE-002); an estimator may waive it manually.",
            )
        if _qty(it) != 1:
            _append_note(it, f"ℹ Quantity set to 1 (was {_qty(it):g}) — one programming tool per project (BUNDLE-002).")
            it["qty"] = 1
    quote["line_items"] = items
    if candidate_text and _TOOL_OWNED_RE.search(candidate_text):
        _add_assumption(
            quote,
            "⚠ REVIEW — the RFQ suggests the customer may already own a programming tool. It is still "
            "quoted: FTL removes it only on confirmed ownership or a manual estimator waiver. "
            + rules_engine.cite_trigger("RT-007"),
        )
    return quote


# ---------------------------------------------------------------------------------------------
# 4. Freight — controlled single-SGV2 rule (COMM-004, I-009, T-014)
# ---------------------------------------------------------------------------------------------
_ZONES_PATH = os.path.join(os.path.dirname(__file__), "data", "freight_zones.json")


def _load_zones() -> Dict[str, Any]:
    try:
        with open(_ZONES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_FSA_RE = re.compile(r"\b([A-Z])\d[A-Z]\s?\d[A-Z]\d\b")


def _match_city(text: str, cities: List[str]) -> Optional[str]:
    # longest names first so "north york" wins over a shorter hit
    for city in sorted(cities, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z])" + re.escape(city) + r"(?![A-Za-z])", text, re.IGNORECASE):
            return city
    return None


def classify_ship_to(text: str) -> Tuple[str, Optional[str]]:
    """('gta' | 'within_150km' | 'unknown', matched place). Postal prefix M = City of Toronto. An
    unlisted place is 'unknown' — never guessed."""
    zones = _load_zones()
    if not text or not zones:
        return "unknown", None
    m = _FSA_RE.search(text.upper())
    if m and m.group(1) == zones.get("toronto_postal_prefix", "M"):
        return "gta", "Toronto (postal code " + m.group(0) + ")"
    gta = _match_city(text, zones.get("gta", []))
    near = _match_city(text, zones.get("within_150km", []))
    if gta and not near:
        return "gta", gta
    if near and not gta:
        return "within_150km", near
    return "unknown", gta or near


def apply_controlled_freight(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """FTL v1.1: ONE SGV2 package -> GTA $375, up to ~150 km outside downtown Toronto $450, beyond
    that manual. Anything larger (more than one operator, or governors / guides / safeties on the
    same estimate) follows the larger-project rules, i.e. manual freight review — it is NOT priced
    from a historical quote ($850 / $975 are explicitly forbidden, T-014). Manual review prices
    freight at $0 with a prominent review flag; the estimator enters the figure."""
    quote = dict(quote)
    zones = _load_zones()
    rates = zones.get("rates_cad", {"gta": 375.0, "within_150km": 450.0})
    ops = total_qty(quote, "door_operator")
    larger = any(
        total_qty(quote, c) > 0 for c in ("governor", "roller_guide", "car_safety")
    )
    ship_text = str(quote.get("shipping_address") or "")
    proj_text = str(quote.get("project_name") or "")
    zone_ship, place_ship = classify_ship_to(ship_text) if not is_placeholder(ship_text) else ("unknown", None)
    zone_proj, place_proj = classify_ship_to(proj_text)
    zone, place, source = zone_ship, place_ship, "shipping address"
    if zone == "unknown" and zone_proj != "unknown":
        zone, place, source = zone_proj, place_proj, "project address"
    conflict = zone_ship != "unknown" and zone_proj != "unknown" and zone_ship != zone_proj
    sv = survey_form.survey_facts_from_text(candidate_text)
    survey_site = str((sv or {}).get("project") or "").strip()
    if survey_site and not conflict:
        zone_site, place_site = classify_ship_to(survey_site)
        if zone_site == "unknown" and zone_ship != "unknown":
            # the survey's job-site is somewhere the controlled lists cannot place, but the ship-to address is in the lists
            conflict = True
            place_proj = survey_site
        elif zone_site != "unknown" and zone_ship != "unknown" and zone_site != zone_ship:
            conflict, place_proj = True, place_site

    if zone == "unknown" and survey_site and not conflict:
        z_s, p_s = classify_ship_to(survey_site)
        if z_s != "unknown":
            zone, place, source = z_s, p_s, "survey project address"

    basis: Dict[str, Any] = {"rule": "COMM-004", "operators": ops, "larger_project": larger, "zone": zone, "place": place}
    if ops == 1 and not larger and not conflict and zone in ("gta", "within_150km"):
        amount = float(rates["gta" if zone == "gta" else "within_150km"])
        quote["freight_estimate"] = amount
        quote["freight_note"] = (
            f"Freight for one SGV2 package, {'within the GTA' if zone == 'gta' else 'up to approx. 150 km outside downtown Toronto'}; "
            "confirmed at order."
        )
        basis.update(mode="controlled", amount=amount, source=source)
        _add_assumption(
            quote,
            f"ℹ Freight ${amount:,.2f} — FTL single-SGV2-package rule (COMM-004): ship-to {place} "
            f"({'GTA' if zone == 'gta' else 'approx. ≤150 km outside downtown Toronto'}, from the {source}). "
            "The city lists behind this live in data/freight_zones.json.",
        )
    else:
        if ops != 1 or larger:
            reason = (
                "this is not a single SGV2 package "
                + (f"({ops:g} door operators" if ops else "(no door operator")
                + (", plus governors/guides/safeties" if larger else "")
                + ") — larger and multi-unit projects follow the manual project-freight review"
            )
        elif conflict:
            reason = f"the shipping address ({place_ship}) and the project address ({place_proj}) disagree"
        else:
            reason = "the ship-to location is not stated or is outside the controlled GTA / ≤150 km list"
        quote["freight_estimate"] = 0.0
        quote["freight_note"] = "Freight to be quoted separately — confirmed at order."
        basis.update(mode="manual", amount=0.0, reason=reason)
        _add_assumption(
            quote,
            f"⚠ REVIEW — FREIGHT NOT PRICED: {reason}. Do not copy a historical freight figure "
            "($850 / $975). " + rules_engine.cite_trigger("RT-015") + " " + rules_engine.cite_conflict("I-009"),
        )
        add_held_item(
            quote,
            item="Freight",
            reason=reason,
            rules=["COMM-004", "RT-015"],
            needed="Estimator enters project freight (manual review).",
            level="REVIEW",
        )
    quote["freight_basis"] = basis
    return quote


# ---------------------------------------------------------------------------------------------
# 5. Customer information — never replace what the RFQ already says with TBD
# ---------------------------------------------------------------------------------------------
_FWD_NOTE_RE = re.compile(r"is:\s*(?P<name>[^<\n]+?)\s*<(?P<email>[^>\s]+@[^>\s]+)>")


def ensure_customer_info(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """The forwarded sender's name is parsed from the email by extract.py and stated in the
    candidate text. If the model left contact_name blank / TBD even though that is known, fill it —
    'TBD' must only appear where the RFQ genuinely does not say."""
    m = _FWD_NOTE_RE.search(candidate_text or "")
    if not m:
        return quote
    name = m.group("name").strip().strip("'\"")
    if not name or "@" in name:
        return quote
    if is_placeholder(quote.get("contact_name")):
        quote = dict(quote)
        quote["contact_name"] = name
        _add_assumption(quote, f"ℹ Contact name '{name}' filled from the customer's own email (it had been left blank / TBD).")
    return quote


# ---------------------------------------------------------------------------------------------
# 5b. Car-door decision and opening reconciliation (CAR DOOR RULE, DOOR-004..006, OUT-003)
# ---------------------------------------------------------------------------------------------
_CD_SENTENCE_SPLIT_RE = re.compile(r"[.\n;]+")
_CD_MENTION_RE = re.compile(r"\bcar[\s-]*door|\bcab[\s-]*door", re.IGNORECASE)
_CD_REPLACE_RE = re.compile(
    r"\b(new|replace(?:ment)?|re-?skin(?:ning)?|skin(?:ning)?|re-?surface|resurfac\w*|universal|re-?panel)\b",
    re.IGNORECASE,
)
_CD_RETAIN_RE = re.compile(
    r"\b(retain(?:ed)?|re-?use(?:d)?|keep|remain(?:s|ing)?|existing\s+(?:car\s+)?doors?\s+(?:to\s+)?remain|not\s+(?:be\s+)?replac\w*)\b",
    re.IGNORECASE,
)
_CD_ALT_RE = re.compile(r"\b(alternate|alternative|alt\.?|optional|add\s+alternate)\b", re.IGNORECASE)
_CD_NEGATED_NEW_RE = re.compile(r"no\s+new\s+(?:car\s+)?door|do(?:es)?\s+not\s+(?:include|require)\s+new\s+(?:car\s+)?door", re.IGNORECASE)


def car_door_decision(candidate_text: str) -> Dict[str, Any]:
    """What the RFQ says about the car door PANELS: 'replace' (new / skinned doors are in the base
    scope -> universal car door logic), 'retain' (existing doors stay -> OEM panel adaptor),
    'alternate_only' (replacement is priced only as an alternate -> the base quote retains the
    existing doors), or 'unclear' (no statement, or the base scope contradicts itself -> HOLD FOR
    REVIEW). Sentence-level and deliberately conservative: it never infers from silence."""
    text = survey_form.strip_survey_template_text(candidate_text or "")
    base_replace: List[str] = []
    base_retain: List[str] = []
    alt_replace: List[str] = []
    sv = survey_form.survey_facts_from_text(text)
    if sv and sv.get("filled"):
        for n, c in (sv.get("configs") or {}).items():
            raw = str(c.get("cab_panel") or "").strip()
            if raw == "0":
                base_retain.append(f"Survey config #{n}, 10a Cab door panel: 0 = retain existing")
            elif re.match(r"^\d+(\.\d+)?\s*[- ]?\s*[A-Fa-f]$", raw):
                base_replace.append(f"Survey config #{n}, 10a Cab door panel: {raw} = new double-skin panel")
    if scope_rules.scope_table_confirms_new(text, ("car door",)):
        base_replace.append("Equipment scope table: Car Door Equipment = New")
    for raw in _CD_SENTENCE_SPLIT_RE.split(text):
        sent = raw.strip()
        if not sent or not _CD_MENTION_RE.search(sent):
            continue
        is_alt = bool(_CD_ALT_RE.search(sent))
        if _CD_NEGATED_NEW_RE.search(sent) or _CD_RETAIN_RE.search(sent):
            base_retain.append(sent[:160])
            continue
        if _CD_REPLACE_RE.search(sent):
            (alt_replace if is_alt else base_replace).append(sent[:160])
    if base_replace and base_retain:
        decision = "unclear"
    elif base_replace:
        decision = "replace"
    elif base_retain:
        decision = "alternate_only" if alt_replace else "retain"
    elif alt_replace:
        decision = "alternate_only"
    else:
        decision = "unclear"
    return {
        "decision": decision,
        "base_replace": base_replace[:3],
        "base_retain": base_retain[:3],
        "alt_replace": alt_replace[:3],
        # True when the RFQ says NOTHING about the car doors (as opposed to contradicting itself): any panel /
        # adaptor line on the estimate is then a guess and is removed.
        "silent": not (base_replace or base_retain or alt_replace),
    }


_PANEL_AUTO_ADDED_MARK = "AUTO-ADDED — every SGV2 door operator needs a matching car door panel"


def enforce_car_door_decision(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """FTL CAR DOOR RULE. Replacement / skinning in the BASE scope -> universal car doors. Existing
    doors retained -> the OEM panel adaptor (needs OEM, configuration, opening, hand and the complete
    top-of-door bolt pattern). Replacement only as an ALTERNATE -> the base quote retains the
    existing doors and the alternate quote shows new universal doors. UNCLEAR -> HOLD FOR REVIEW.

    A universal-door line the agent added on its own (because an operator needs SOME panel) is a
    guess when the RFQ does not call for new doors, so it is removed and recorded as a hold instead
    of being priced. A panel line that came from the model's reading of the spec is kept and flagged."""
    quote = dict(quote)
    items = [dict(it) for it in _lines(quote)]
    if not any(it.get("category") == "door_operator" for it in items):
        return quote
    info = car_door_decision(candidate_text)
    decision = info["decision"]
    quote["car_door_decision"] = info
    panels = [it for it in items if it.get("category") == "car_door_panel"]
    adaptors = [it for it in items if it.get("category") == "panel_adaptor"]
    if decision == "replace":
        if adaptors and not panels:
            _add_assumption(
                quote,
                "⚠ REVIEW — the RFQ puts new / skinned car doors in the base scope but this estimate carries panel "
                "adaptors (existing doors retained). Confirm the car-door decision (DOOR-004).",
            )
        return quote
    auto_added = [it for it in panels if _PANEL_AUTO_ADDED_MARK in str(it.get("note") or "")]
    if decision in ("retain", "alternate_only"):
        why = (
            "the RFQ retains the existing car doors"
            if decision == "retain"
            else "replacement appears only as an alternate, so the base quote retains the existing doors"
        )
        if panels:
            if auto_added:
                items = [it for it in items if it not in auto_added]
                _add_assumption(
                    quote,
                    f"ℹ Removed the default universal-door line the agent had added: {why} (DOOR-006). "
                    "New doors belong on the alternate quote.",
                )
            else:
                for it in panels:
                    it["needs_engineering_review"] = True
                    _append_note(it, f"⚠ REVIEW — {why}; a universal door line is not expected in the base scope.")
        if not adaptors:
            add_held_item(
                quote,
                item="Car door panel adaptor",
                reason=f"{why}. The adaptor SKU depends on the existing OEM, configuration, opening, hand and the complete top-of-door bolt pattern, none of which the RFQ gives.",
                rules=["DOOR-006", "RT-004", "OTIS-001"],
                needed="Existing OEM, configuration, opening, hand, complete top-of-door bolt pattern (survey).",
            )
    else:  # unclear
        if info.get("silent") and (panels or adaptors):
            # The RFQ never mentions the car doors, so ANY panel / adaptor the model priced is a guess — and it
            # guesses differently run to run. Remove it; the estimate carries a hold instead (never guess).
            removed = ", ".join(sorted({str(it.get("product_code") or it.get("description") or "?") for it in panels + adaptors}))
            items = [it for it in items if it not in panels and it not in adaptors]
            _add_assumption(
                quote,
                f"\U0001F6D1 HOLD FOR REVIEW — car doors: the RFQ says nothing about the car doors, so the door line(s) the agent had "
                f"guessed ({removed}) were removed rather than priced. " + rules_engine.cite_trigger("RT-004"),
            )
            auto_added = []
            panels, adaptors = [], []
            add_held_item(
                quote,
                item="Car doors — new universal doors vs retained doors",
                reason="HOLD FOR REVIEW: the RFQ does not say whether the car doors are replaced / skinned or retained, so no door line is priced (CAR DOOR RULE).",
                rules=["DOOR-004", "DOOR-005", "DOOR-006"],
                needed="Estimator confirms: replace / skin (universal doors) or retain (OEM adaptor + bolt pattern).",
            )
        if auto_added:
            items = [it for it in items if it not in auto_added]
            add_held_item(
                quote,
                item="Car doors — new universal doors vs retained doors",
                reason="HOLD FOR REVIEW: the RFQ does not say whether the car doors are replaced / skinned or retained, so no door line is priced (CAR DOOR RULE).",
                rules=["DOOR-004", "DOOR-005", "DOOR-006"],
                needed="Estimator confirms: replace / skin (universal doors) or retain (OEM adaptor + bolt pattern).",
            )
            _add_assumption(
                quote,
                "\U0001F6D1 HOLD FOR REVIEW — car doors: the RFQ does not say whether to replace / skin or retain them; the default "
                "universal-door line was removed rather than guessed. " + rules_engine.cite_trigger("RT-004"),
            )
        elif panels or adaptors:
            for it in panels + adaptors:
                it["needs_engineering_review"] = True
                _append_note(it, "⚠ REVIEW — the RFQ is unclear whether car doors are replaced or retained (CAR DOOR RULE).")
    quote["line_items"] = items
    return quote


def reconcile_opening_counts(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """OUT-003 (BLOCK on failure): operators, clutches, detectors and $0 value lines must reconcile
    to the opening count, and universal panel quantities to the door type (1 per 1S opening, 2 per
    2C/2T opening). A mismatch is recorded as a held item — it is never silently 'fixed' here
    (the quantity enforcement functions earlier in the pipeline do the fixing; this is the final
    check that they all agree). Categories the customer excluded ("not required from FTL") are
    skipped."""
    quote = dict(quote)
    ops = total_qty(quote, "door_operator")
    if ops <= 0:
        return quote
    excluded: set = set()
    if candidate_text:
        for _label, cats in scope_rules.not_required_from_ftl(candidate_text):
            excluded.update(cats)
    problems: List[str] = []

    def expect(cat: str, label: str, expected: float, code: Optional[str] = None) -> None:
        if cat in excluded:
            return
        have = sum(
            _qty(it) for it in _lines(quote)
            if (it.get("product_code") == code if code else it.get("category") == cat)
        )
        if have == 0 and cat == "door_protective_device":
            return  # a retained / out-of-scope detector is legitimate (DET scope handled elsewhere)
        if abs(have - expected) > 0.01:
            problems.append(f"{label}: {have:g} on the estimate vs {expected:g} expected")

    expect("clutch", "SGV2 clutches (one per operator, complete with car-door interlock)", ops)
    expect("door_protective_device", "Formula 3D detectors (one per opening)", ops)
    expect("other", "Restriction-function value lines (one per opening)", ops, code="SGV2_CAR_DOOR_RESTRICTOR")
    panel_qty = total_qty(quote, "car_door_panel")
    adaptor_qty = total_qty(quote, "panel_adaptor")
    if panel_qty:
        expected = 0.0
        for it in _lines(quote):
            if it.get("category") != "door_operator":
                continue
            m = re.search(r"SGV2_DOOR_OP_(1S|2C|2T)", str(it.get("product_code") or ""))
            if m:
                expected += _qty(it) * (1 if m.group(1) == "1S" else 2)
        if expected and abs(panel_qty - expected) > 0.01 and "car_door_panel" not in excluded:
            problems.append(f"Universal door panels: {panel_qty:g} vs {expected:g} expected (1 per 1S opening, 2 per 2C/2T opening)")
    if adaptor_qty and "panel_adaptor" not in excluded and abs(adaptor_qty - ops) > 0.01:
        problems.append(f"Panel adaptors: {adaptor_qty:g} vs {ops:g} openings")
    if problems:
        add_held_item(
            quote,
            item="Opening-count reconciliation failed",
            reason="; ".join(problems) + " (OUT-003).",
            rules=["OUT-003", "QTY-002", "BUNDLE-001", "PANEL-001", "PANEL-002"],
            needed="Confirm the car-by-car, front/rear opening schedule and correct the quantities.",
        )
        _add_assumption(quote, "\U0001F6D1 HOLD — " + "; ".join(problems) + ". " + rules_engine.cite_trigger("RT-001"))
    return quote


# ---------------------------------------------------------------------------------------------
# 6. Final pass — five-question trace per line, internal notes, release status
# ---------------------------------------------------------------------------------------------
_CATEGORY_LABEL = {
    "door_operator": "SGV2 door operator",
    "clutch": "SGV2 clutch complete with car-door interlock",
    "panel_adaptor": "OEM car-door panel adaptor (existing door retained)",
    "car_door_panel": "universal car door panel",
    "roller_guide": "roller guide set",
    "governor": "governor",
    "car_safety": "car safety",
    "door_protective_device": "Formula VisionPlus 3D door detector",
    "door_tools": "door programming tool",
    "other": "line",
}
_CATEGORY_RULES = {
    "door_operator": ["QTY-001", "QTY-002", "DOOR-001", "DOOR-002", "BUNDLE-001"],
    "clutch": ["BUNDLE-001", "DOOR-002", "OTIS-001"],
    "panel_adaptor": ["DOOR-006", "DOOR-008", "RT-004"],
    "car_door_panel": ["DOOR-004", "PANEL-001", "PANEL-002"],
    "roller_guide": ["WRG-001", "WRG-002", "WRG-003"],
    "governor": ["GOV-001", "GOV-002", "GOV-003", "GOV-005"],
    "car_safety": ["SAFE-001", "SAFE-002", "SAFE-003", "SAFE-004"],
    "door_protective_device": ["DET-001", "DET-002", "BUNDLE-001"],
    "door_tools": ["BUNDLE-002", "RT-007"],
    "other": ["BUNDLE-001", "I-015"],
}
_RULE_ADDED_CATEGORIES = {"door_tools"}
_RULE_ADDED_CODES = {"SGV2_CAR_DOOR_RESTRICTOR"}


def _origin(it: Dict[str, Any]) -> str:
    note = str(it.get("note") or "")
    code = str(it.get("product_code") or "")
    if code in _RULE_ADDED_CODES or "AUTO-ADDED" in note or "tension" in code.lower() or "swingarm" in code.lower():
        return "FTL_RULE"
    if it.get("origin") in ("ESTIMATOR_OVERRIDE", "CUSTOMER_RECORD", "FTL_RULE", "SPEC"):
        return it["origin"]
    return "SPEC"


def _qty_source(it: Dict[str, Any], facts: Dict[str, Any]) -> str:
    cat = it.get("category")
    code = str(it.get("product_code") or "")
    q = _qty(it)
    openings = facts.get("openings")
    cars = facts.get("cars")
    how = facts.get("openings_how")
    if code == "SGV2_CAR_DOOR_RESTRICTOR":
        return f"{q:g} = one value line per SGV2 opening (BUNDLE-001), matching the {total_ops(facts):g} door operator(s)."
    if "tension" in code.lower() or "swingarm" in code.lower():
        return f"{q:g} = one separate tension/swingarm line per OL governor (GOV-003)."
    if cat in ("door_operator", "clutch", "door_protective_device"):
        if openings and how in ("computed", "single_entrance") and abs(q - openings) < 0.01:
            return f"{q:g} = confirmed door openings in the spec's schedule ({openings})."
        return f"{q:g} taken from the door schedule / RFQ; the opening count is not independently confirmed — verify front/rear entrances."
    if cat == "car_door_panel":
        return f"{q:g} physical panel(s): 1 per 1S opening, 2 per 2C/2T opening (PANEL-001/002)."
    if cat == "panel_adaptor":
        return f"{q:g} = one adaptor per retained car-door panel opening (DOOR-006)."
    if cat == "governor":
        if cars:
            return f"{q:g} governor(s); {cars} car(s) in scope per the spec." if q <= cars else f"{q:g} governor(s) — exceeds the {cars} car(s) in scope; verify."
        return f"{q:g} governor(s) from the RFQ; car count not independently confirmed."
    if cat == "roller_guide":
        return f"{q:g} pieces = the customer's stated full sets x 4 (WRG-002)."
    if cat == "door_tools":
        return "1 = one programming tool per project (BUNDLE-002)."
    return f"{q:g} as stated in the RFQ scope."


def total_ops(facts: Dict[str, Any]) -> float:
    return float(facts.get("operators") or 0)


def _validated_by(it: Dict[str, Any], row: Optional[Dict[str, Any]]) -> str:
    if it.get("hold"):
        return "NOT VALIDATED — held (see note)."
    if it.get("needs_engineering_review"):
        return "NOT YET VALIDATED — flagged for engineering / estimator review (see note)."
    if not row:
        return "NOT VALIDATED — no exact Product Master record."
    gate = row.get("release_gate")
    base = f"Product Master row {row.get('ftl_product_code')} ({row.get('automation_status')})"
    return base + (f"; release gate: {gate}" if gate else "") + "."


def _why(it: Dict[str, Any], row: Optional[Dict[str, Any]], origin: str) -> str:
    label = _CATEGORY_LABEL.get(str(it.get("category")), "line")
    desc = (row or {}).get("description") or it.get("description") or it.get("product_code")
    if origin == "FTL_RULE":
        return f"Added by FTL rule — {label}: {desc}."
    return f"RFQ scope calls for the {label}; selected {it.get('product_code')} — {desc}."


def scope_facts(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    cars = scope_rules.known_car_count(candidate_text) if candidate_text else None
    openings, how = scope_rules.car_door_count(candidate_text) if candidate_text else (None, "unknown")
    return {
        "cars": cars,
        "openings": openings,
        "openings_how": how,
        "operators": total_qty(quote, "door_operator"),
    }


def annotate_line_traceability(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """FTL's five-question standard for every quoted line: why this product, where the quantity
    came from, which FTL rule applied, what validated it, where the price came from. A line that
    cannot answer all five is marked auto_release=False (OUT-004, v1.1 'Live testing')."""
    quote = dict(quote)
    facts = scope_facts(quote, candidate_text)
    items: List[Dict[str, Any]] = []
    for raw in _lines(quote):
        it = dict(raw)
        row = rules_engine.resolve_master_row(str(it.get("product_code") or ""))
        origin = _origin(it)
        rules = list(_CATEGORY_RULES.get(str(it.get("category")), []))
        if row and row.get("automation_status") in ("HOLD", "REVIEW"):
            rules.append(f"status {row['automation_status']}")
        trace = {
            "why": _why(it, row, origin),
            "qty_source": _qty_source(it, facts),
            "rules": rules,
            "validated_by": _validated_by(it, row),
            "price_source": it.get("price_source") or "unknown",
            "origin": origin,
        }
        answered = all(
            trace[k] and not str(trace[k]).startswith("unknown") for k in ("why", "qty_source", "price_source")
        ) and bool(rules)
        trace["auto_release"] = bool(
            answered
            and not it.get("needs_engineering_review")
            and not it.get("hold")
            and it.get("price_verified", True)
            and (row is not None or it.get("product_code") in ZERO_VALUE_LINE_CODES)
        )
        it["origin"] = origin
        it["trace"] = trace
        items.append(it)
    quote["line_items"] = items
    return quote


# ---------------------------------------------------------------------------------------------
# Wittur SGV survey form — the answers typed into the PDF are the specification
# ---------------------------------------------------------------------------------------------
_SURVEY_DOOR_CATEGORIES = ("door_operator", "clutch", "panel_adaptor", "car_door_panel", "door_protective_device", "door_tools")
_SURVEY_CLUTCH_OEM = {"GAL/ECI": "G.M.D.", "MAC": "G.M.D.", "DOVER": "G.M.D.", "OTIS 6940": "OTIS", "WH E5A": "WEST"}
_SURVEY_ADAPTOR_OEM = {"GAL": "GAL", "MAC": "MAC", "DOVER": "DOV"}
_SURVEY_RULES = ["QTY-002", "RT-001"]


def _survey_line(row: Dict[str, Any], category: str, qty: float, note: str, origin: str = "SPEC") -> Dict[str, Any]:
    return {
        "product_code": row["ftl_product_code"],
        "description": row.get("description") or row["ftl_product_code"],
        "category": category,
        "qty": qty,
        "unit_price": float(row.get("price_cad") or 0),
        "needs_engineering_review": True,
        "note": note,
        "origin": origin,
    }


_SGV2_RFQ_RE = re.compile(
    r"\bwittur\b|\bsgv2\b|\bsgv\b|4c-r|door\s+operator|freight\s+car\s+door",
    re.IGNORECASE,
)
_OP_CODE_RE = re.compile(r"SGV2_DOOR_OP_(1S|2C|2T)(\d+)_(LH|RH|L|R)$", re.IGNORECASE)
_DEFAULT_OPERATOR_CODES = ("SGV2_DOOR_OP_2C42_L", "SGV2_DOOR_OP_2C42_LH")


def _sgv2_door_rfq(candidate_text: str) -> bool:
    body = (candidate_text or "").split(UNREAD_ATTACHMENTS_MARKER)[0]
    return bool(_SGV2_RFQ_RE.search(body))


def _operator_hand(code: str) -> str:
    match = _OP_CODE_RE.search(code or "")
    if not match:
        return "L"
    suffix = match.group(3).upper()
    return "R" if suffix in {"R", "RH"} else "L"


def _master_row(*codes: str) -> Optional[Dict[str, Any]]:
    for code in codes:
        row = rules_engine.resolve_master_row(code)
        if row:
            return row
    return None


def ensure_core_door_package(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """Quote 1 for a Wittur/SGV2 door-operator email is operator + clutch (+ restrictor + tool later).

    The model omits the operator on some runs of the same photo-only EML (Seth's Gradio log of
    145 Richmond flipped 0/1/2/4/5 lines). When the RFQ is clearly that job and no Wittur survey
    form is driving the package, insert the same placeholder 2C 42 L + GAL clutch Seth's good
    runs use, flagged for engineering review. Detector and car doors stay off Quote 1.
    """
    if survey_form.survey_facts_from_text(candidate_text):
        return quote
    if not _sgv2_door_rfq(candidate_text):
        return quote
    if any("door_operator" in cats for _t, cats in scope_rules.not_required_from_ftl(candidate_text or "")):
        return quote

    quote = dict(quote)
    items = [dict(it) for it in _lines(quote)]
    ops = [it for it in items if it.get("category") == "door_operator"]
    if not ops:
        row = _master_row(*_DEFAULT_OPERATOR_CODES)
        if row:
            items.append(_survey_line(
                row,
                "door_operator",
                1.0,
                "⚠ AUTO-ADDED — door type, width, hand and opening quantity were not in the email; "
                "placeholder SGV2 2C 42\" L based on Wittur 4C-R / SGV2 wording. Confirm from the "
                "survey before release.",
                "FTL_RULE",
            ))
            add_held_item(
                quote,
                item="Door operator configuration",
                reason="Email names a Wittur/SGV2 operator but not door type, width, hand or quantity; Quote 1 uses a 2C 42\" L placeholder.",
                rules=["RT-001", "QTY-002"],
                needed="Estimator confirms door type (1S/2C/2T), width, hand and opening count from the survey.",
            )
            ops = [items[-1]]

    op_qty = sum(_qty(it) for it in ops)
    if ops and not any(it.get("category") == "clutch" for it in items) and op_qty > 0:
        hand = _operator_hand(str(ops[0].get("product_code") or ""))
        cl_row = _master_row(f"SGV2_CLUTCH_G.M.D._{hand}", f"SGV2_CLUTCH_G.M.D._{hand}H")
        if cl_row:
            items.append(_survey_line(
                cl_row,
                "clutch",
                op_qty,
                "⚠ AUTO-ADDED — clutch OEM/hand were not in the email; GAL G.M.D. matching the "
                "operator hand. Confirm from the survey before release.",
                "FTL_RULE",
            ))
            add_held_item(
                quote,
                item="Clutch OEM",
                reason="Clutch OEM is not in the email; Quote 1 uses GAL G.M.D. matching the operator hand.",
                rules=["OTIS-001", "RT-004"],
                needed="Estimator confirms clutch interface (GAL/Otis/West) from the survey.",
            )
    quote["line_items"] = items
    return quote


def enforce_survey_form(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """When the RFQ is a Wittur SGV survey form, quantities, door type, size, clutch and detector
    come from the typed form fields — never from the model's reading of the blank template."""
    sv = survey_form.survey_facts_from_text(candidate_text)
    if not sv:
        return quote
    quote = dict(quote)
    items = [dict(it) for it in _lines(quote)]
    survey_cats = _SURVEY_DOOR_CATEGORIES

    if not sv.get("filled"):
        kept = [
            it for it in items
            if it.get("category") not in survey_cats and it.get("product_code") != "SGV2_CAR_DOOR_RESTRICTOR"
        ]
        if len(kept) != len(items):
            _add_assumption(
                quote,
                "\U0001F6D1 HOLD — the Wittur survey form attached to this RFQ is NOT filled in (no operator quantity, door type or "
                "opening size), so the door package was removed rather than guessed.",
            )
        quote["line_items"] = kept
        add_held_item(
            quote,
            item="Wittur survey form not filled in",
            reason="The PDF form has no operator quantity, door type or opening size, so no SGV2 door package can be sized or priced.",
            rules=_SURVEY_RULES,
            needed="The completed Wittur SGV survey form (sections 1-11) from the customer.",
        )
        return quote

    configs = sv.get("configs") or {}
    problems: List[str] = []
    new_lines: List[Dict[str, Any]] = []
    total_ops = 0.0
    model_ops = sum(_qty(it) for it in items if it.get("category") == "door_operator")

    for n, c in sorted(configs.items(), key=lambda kv: int(kv[0])):
        qty = float(c.get("operator_qty") or 0)
        if qty <= 0:
            continue
        total_ops += qty
        fam, hand = c.get("door_family"), c.get("hand")
        width = c.get("clear_opening_in")
        tag = f"survey config #{n} ({c.get('door_type')}, CO {width:g} in, qty {qty:g})" if width else f"survey config #{n}"
        op_row = None
        if fam in ("1S", "2C", "2T") and hand in ("L", "R") and width:
            op_row = rules_engine.resolve_master_row(f"SGV2_DOOR_OP_{fam}{int(width)}_{hand}")
        if op_row:
            new_lines.append(_survey_line(op_row, "door_operator", qty, f"✓ From {tag}. Door hand is as written on the form (Wittur's top-down convention is opposite to the usual North American one — confirm).", "SPEC"))
        else:
            problems.append(f"Config #{n}: door type {c.get('door_type') or 'blank'} / width {width or 'blank'} has no stocked SGV2 operator in the Product Master")
            add_held_item(
                quote,
                item=f"Door operator — survey config #{n}",
                reason=f"Door type {c.get('door_type') or '(blank)'} at {width or '(blank)'} in clear opening cannot be matched to a stocked SGV2 operator.",
                rules=["PANEL-001", "RT-001"],
                needed="Confirm door type (1S / 2C / 2T), hand and clear opening on the Wittur form.",
            )
            continue
        iface = str(c.get("clutch_interface") or "").strip().upper()
        oem = _SURVEY_CLUTCH_OEM.get(iface)
        cl_row = rules_engine.resolve_master_row(f"SGV2_CLUTCH_{oem}_{hand}") if oem else None
        if cl_row:
            new_lines.append(_survey_line(cl_row, "clutch", qty, f"✓ From {tag}: clutch interface {iface}, one clutch c/w car-door interlock per operator.", "SPEC"))
        else:
            add_held_item(
                quote,
                item=f"Clutch — survey config #{n}",
                reason=f"Clutch interface '{iface or '(blank)'}' is not a stocked clutch (GAL/ECI, MAC, DOVER, OTIS 6940, WH E5A). AMD and OTHER need a custom quote with the lock-roller survey.",
                rules=["OTIS-001", "RT-004"],
                needed="Final clutch interface, or the custom-clutch survey and a photo.",
            )
        cab = str(c.get("cab_panel") or "").strip()
        panel_iface = str(c.get("cab_panel_interface") or "").strip().upper()
        if cab == "0":
            ad_row = None
            if fam == "1S" and hand in ("L", "R") and width and panel_iface in _SURVEY_ADAPTOR_OEM:
                ad_row = rules_engine.resolve_master_row(f"SGV2(1S)_PANEL_ADAPTOR_{_SURVEY_ADAPTOR_OEM[panel_iface]}{int(width)}_{hand}H")
            if ad_row:
                new_lines.append(_survey_line(ad_row, "panel_adaptor", qty, f"✓ From {tag}: existing cab door retained, panel interface {panel_iface}. Confirm the bolt pattern (A-F, S).", "SPEC"))
            else:
                add_held_item(
                    quote,
                    item="Car door panel adaptor",
                    reason=(
                        f"Survey config #{n}: the existing cab door is retained but 10b cab door panel interface is "
                        f"{panel_iface or 'NOT SELECTED'}"
                        + (" (OTHER = custom quote required)" if panel_iface == "OTHER" else "")
                        + " — no stocked adaptor can be chosen."
                    ),
                    rules=["DOOR-006", "RT-004", "OTIS-001"],
                    needed="10b panel interface (AMD / MAC / DOVER / GAL / ARMOR), or the pendent-bolt survey and photo for a custom quote.",
                )
        elif re.match(r"^\d+(\.\d+)?\s*[- ]?\s*[A-Fa-f]$", cab):
            pn_row = rules_engine.universal_panel_row(fam or "", width) if fam in ("1S", "2C", "2T") else None
            if pn_row:
                new_lines.append(_survey_line(pn_row, "car_door_panel", qty * (1 if fam == "1S" else 2), f"✓ From {tag}: new double-skin panel {cab}. Confirm finish / clear height.", "SPEC"))
            else:
                add_held_item(quote, item="Car door panel", reason=f"New panel requested ({cab}) but no stocked universal door matches {fam} {width} in x {c.get('clear_height_in') or '?'} in.", rules=["DOOR-004", "PANEL-001"], needed="Door type, width x height and finish.")
        else:
            add_held_item(quote, item="Car door panel", reason=f"Survey config #{n}: 10a cab door panel answer '{cab or '(blank)'}' is not 0 (retain) or a thickness + finish code.", rules=["DOOR-004", "DOOR-006"], needed="Retain existing, or new panel thickness and finish.")
        mount = str(c.get("mounting") or "").strip()
        if mount and mount.upper() not in ("V2-01", "0"):
            add_held_item(
                quote,
                item=f"Operator mounting — survey config #{n}",
                reason=f"9 Operator mounting is '{mount}', not the standard V2-01 brackets or 0 (none); no mounting hardware is priced.",
                rules=["RT-001"],
                needed="What 'Custom' mounting requires (drawing / photo).",
                level="REVIEW",
            )

    det = sv.get("detector") or {}
    det_type = str(det.get("type") or "").strip()
    det_qty = det.get("qty")
    items = [it for it in items if it.get("category") not in ("door_operator", "clutch", "panel_adaptor", "car_door_panel")]
    for it in items:
        if it.get("product_code") == "SGV2_CAR_DOOR_RESTRICTOR":
            it["qty"] = total_ops
    items += new_lines
    if re.search(r"vision\s*plus", det_type, re.IGNORECASE):
        d_row = rules_engine.detector_row()
        items = [it for it in items if it.get("category") != "door_protective_device"]
        q_det = float(det_qty) if det_qty not in (None, 0) else total_ops
        if d_row:
            items.append(_survey_line(d_row, "door_protective_device", q_det, f"✓ From survey 10d: {det_type}, qty {q_det:g}. FTL standard detector is the Formula VisionPlus 3D (FZU-1442US01); the form says '2D/3D' — confirm 3D.", "SPEC"))
        if abs(q_det - total_ops) > 0.01:
            add_held_item(quote, item="Detector quantity", reason=f"Survey 10d detector qty {q_det:g} differs from the operator qty {total_ops:g}.", rules=["DET-001"], needed="Confirm one detector per opening.", level="REVIEW")
    else:
        items = [it for it in items if it.get("category") != "door_protective_device"]
        if det_type and det_type.lower() not in ("none", ""):
            add_held_item(quote, item="Door protection", reason=f"Survey 10d selects '{det_type}', which is not FTL's standard Formula VisionPlus detector — not priced.", rules=["DET-001"], needed="Confirm the detector (FTL standard: Formula VisionPlus 3D FZU-1442US01).", level="REVIEW")

    tool_qty = [c.get("programming_tool_qty") for c in configs.values() if c.get("programming_tool_qty") is not None]
    if tool_qty and max(tool_qty) <= 0 and any(it.get("category") == "door_tools" for it in items):
        add_held_item(quote, item="Programming tool", reason="Survey section 11 asks for 0 programming tools; FTL's rule is one per project at its inFlow price.", rules=["BUNDLE-002"], needed="Estimator confirms whether the customer already owns one.", level="REVIEW")

    if sv.get("custom_clutch") and any(str(c.get("clutch_interface") or "").upper() in _SURVEY_CLUTCH_OEM for c in configs.values()):
        add_held_item(
            quote,
            item="Custom clutch survey vs standard clutch interface",
            reason="Section 8 selects a standard clutch interface, yet the custom clutch/coupler page was also filled in (" + "; ".join(f"{k}={v}" for k, v in list(sv["custom_clutch"].items())[:5]) + ").",
            rules=["OTIS-001", "RT-004"],
            needed="Confirm whether a standard clutch is enough or a custom clutch is needed.",
            level="REVIEW",
        )
    c_name = str((sv.get("contact") or {}).get("name") or "").strip()
    q_contact = str(quote.get("contact_name") or "").strip()
    if c_name and q_contact and c_name.lower() != q_contact.lower() and not is_placeholder(q_contact):
        add_held_item(quote, item="Contact differs from the survey", reason=f"The survey lists {c_name} as contact; the enquiry came from {q_contact}.", rules=["SRC-001"], needed="Confirm who the quote is addressed to.", level="REVIEW")
    site = str(sv.get("project") or "")
    subj_nums = set(re.findall(r"\b\d{1,5}\b", str(quote.get("project_name") or "")))
    site_nums = set(re.findall(r"\b\d{1,5}\b", site))
    if site and subj_nums and site_nums and not (subj_nums & site_nums):
        add_held_item(quote, item="Project address mismatch", reason=f"The survey's project address is '{site}', the enquiry / quote says '{quote.get('project_name')}'.", rules=["SRC-001"], needed="Confirm the correct job-site address.", level="REVIEW")

    if abs(model_ops - total_ops) > 0.01 and model_ops:
        _add_assumption(quote, f"✎ Operator quantity set to the survey's {total_ops:g} (the draft had {model_ops:g}) — quantities come from the customer's form, never inferred.")
    for p in problems:
        _add_assumption(quote, "\U0001F6D1 HOLD — " + p + ".")
    quote["line_items"] = items
    return quote


def build_internal_notes(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, List[str]]:
    """What was confirmed (and from where), what is unknown, what needs technical review and what
    survey information is still needed — never used to justify a guessed product or quantity."""
    facts = scope_facts(quote, candidate_text)
    confirmed: List[str] = []
    unknown: List[str] = []
    review: List[str] = []
    survey: List[str] = []

    if facts["cars"]:
        confirmed.append(f"Cars in scope: {facts['cars']} (from the RFQ / spec elevator count).")
    else:
        unknown.append("Number of cars is not stated in a form the agent can rely on — never inferred.")
    if facts["openings"] and facts["openings_how"] in ("computed", "single_entrance"):
        confirmed.append(f"SGV2 door openings: {facts['openings']} (from the spec's door schedule; operator qty = openings).")
    elif facts["operators"]:
        unknown.append(
            f"Door openings are not independently confirmed (operators quoted: {facts['operators']:g}). "
            "Number of elevators ≠ number of operators — confirm front/rear entrances car by car (QTY-001/002)."
        )
    for label, key in (
        ("Customer", "customer_name"),
        ("Contact", "contact_name"),
        ("Contact phone", "contact_phone"),
        ("Billing address", "billing_address"),
        ("Shipping address", "shipping_address"),
    ):
        if is_placeholder(quote.get(key)):
            unknown.append(f"{label} is not stated in the RFQ.")
        else:
            confirmed.append(f"{label}: {quote.get(key)}")
    if not is_placeholder(quote.get("project_name")):
        confirmed.append(f"Project: {quote.get('project_name')}")

    sv = survey_form.survey_facts_from_text(candidate_text)
    if sv and sv.get("filled"):
        for n, c in (sv.get("configs") or {}).items():
            confirmed.append(
                f"Survey config #{n}: elevator {c.get('elevator', '?')} side {c.get('side', '?')}, {c.get('operator_qty'):g} x {c.get('door_type')}, "
                f"CO {c.get('clear_opening_in')} in x CH {c.get('clear_height_in')} in, clutch {c.get('clutch_interface')}, cab door "
                f"{'retained' if str(c.get('cab_panel')) == '0' else c.get('cab_panel')}, programming tool {c.get('programming_tool_qty')}."
            )
        if (sv.get("detector") or {}).get("type"):
            confirmed.append(f"Survey 10d detector: {sv['detector']['type']} x {sv['detector'].get('qty')}.")
        if sv.get("controller"):
            confirmed.append(f"Survey 10c main controller: {sv['controller']}.")
        for m in sv.get("missing") or []:
            survey.append(f"Survey form: {m} is not filled in.")
    elif sv:
        unknown.append("The Wittur survey form attached to this RFQ is not filled in — no quantities, door type or sizes are known.")
        survey.append("The completed Wittur SGV survey form (sections 1-11).")

    cats = {it.get("category") for it in _lines(quote)}
    if "panel_adaptor" in cats:
        survey.append(
            "Retained car doors: existing OEM, configuration, opening, hand and the COMPLETE top-of-door bolt pattern "
            "(plus A–F and S measurements) for every adaptor line (DOOR-006 / DOOR-008)."
        )
    if "car_door_panel" in cats:
        survey.append("New universal car doors: clear opening width, door height (84 in standard), finish (DOOR-004).")
    if "door_operator" in cats:
        survey.append("Hall-view door hand / coupler position, existing OEM and landing-lock plan for each opening (DOOR-002).")
    if "governor" in cats:
        survey.append("Governor: rated speed, application (car/CWT), pulley & rope diameter, trip direction/hand, mounting, remote/encoder option.")
    if "roller_guide" in cats:
        survey.append("Roller guides: car or CWT, rated speed, guide-rail type/head width, car weight + rated load (WRG-001).")

    for h in quote.get("held_items") or []:
        review.append(f"[{h.get('level', 'HOLD')}] {h.get('item')}: {str(h.get('reason') or '').rstrip('.')}. Needs: {str(h.get('needed') or 'estimator decision').rstrip('.')}.")
    for it in _lines(quote):
        if it.get("needs_engineering_review") and not it.get("hold"):
            review.append(f"Line {it.get('product_code')}: flagged for review — see its note.")

    return {"confirmed": confirmed, "unknown": unknown, "technical_review": review, "survey_needed": survey}


def release_status(quote: Dict[str, Any]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    level = "CLEAR"
    for h in quote.get("held_items") or []:
        reasons.append(f"{h.get('item')} — {h.get('reason')}")
        if h.get("level", "HOLD") == "HOLD":
            level = "HOLD"
        elif level != "HOLD":
            level = "REVIEW"
    for it in _lines(quote):
        tr = it.get("trace") or {}
        if not tr.get("auto_release", True):
            if level == "CLEAR":
                level = "REVIEW"
            if it.get("hold"):
                level = "HOLD"
            reasons.append(f"Line {it.get('product_code')} cannot auto-release.")
    label = {
        "HOLD": "HOLD — technical / commercial review required before release",
        "REVIEW": "REVIEW — estimator sign-off required before release",
        "CLEAR": "CLEAR — all five line questions answered; estimator sign-off still required for discounts and freight",
    }[level]
    return label, list(dict.fromkeys(reasons))


_DETECTION_MENTION_RE = re.compile(
    r"\bdetectors?\b|\bdetection\b|protective\s+device|light\s+curtain|cabsafe|safety\s+edge|door\s+edge|\b3d\b|visionplus|\bpana\b|\bformula\b",
    re.IGNORECASE,
)


def detection_mentioned(candidate_text: str) -> bool:
    """True when the RFQ talks about door detection / a 3D edge anywhere (spec clause, survey 10d, email).
    The 'Detected structure signal' heading is the extractor's own label, not the customer's words."""
    text = re.sub(r"##\s*Detected structure signal[^\n]*", " ", candidate_text or "")
    text = text.split(UNREAD_ATTACHMENTS_MARKER)[0]
    return bool(_DETECTION_MENTION_RE.search(text))


def enforce_detector_scope(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """Quote 1 carries a door detector only when the RFQ mentions door detection. When it never does, a
    detector line is the model's own addition (it adds one on some runs and not on others — which made
    Quote 2 appear and disappear), so it is removed here and offered on Quote 2 as FTL's recommendation
    (DET-001, QUOTE-002). A detector that came from a filled Wittur survey (10d) or from the spec is untouched."""
    items = [dict(it) for it in _lines(quote)]
    dets = [it for it in items if it.get("category") == "door_protective_device"]
    if not dets or not any(it.get("category") == "door_operator" for it in items):
        return quote
    if detection_mentioned(candidate_text):
        return quote
    quote = dict(quote)
    quote["line_items"] = [it for it in items if it not in dets]
    _add_assumption(
        quote,
        "ℹ No door detector on Quote 1: the RFQ never mentions door detection, so a detector line is not part of the customer's "
        "specification. FTL's Formula VisionPlus 3D detector is offered on Quote 2 (DET-001, QUOTE-001/002).",
    )
    return quote


def enforce_unread_attachments(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    """The email attaches files the estimator cannot read (photographed / scanned survey, CAD, spreadsheets).
    Whatever they say is unknown: the estimate is held so nobody releases a price built on the email alone."""
    names = has_unread_attachments(candidate_text)
    if not names:
        return quote
    quote = dict(quote)
    add_held_item(
        quote,
        item="Attached files not read",
        reason=(
            "The email attaches " + ", ".join(names[:4]) + (f" (+{len(names) - 4} more)" if len(names) > 4 else "")
            + ", which the estimator cannot read (photographed / scanned survey or drawing). Door type, opening size, hand, "
            "clutch / OEM, panels, detector and quantities in this estimate come from the email text only."
        ),
        rules=["OUT-003", "QUOTE-001"],
        needed="Estimator reads the attachment(s) and confirms or corrects the operator type, quantities and door package — or the customer sends a fillable / typed survey.",
        level="HOLD",
    )
    return quote


def finalize_quote(quote: Dict[str, Any], candidate_text: str = "") -> Dict[str, Any]:
    quote = enforce_car_door_decision(quote, candidate_text)
    quote = reconcile_opening_counts(quote, candidate_text)
    quote = annotate_line_traceability(quote, candidate_text)
    quote["internal_notes"] = build_internal_notes(quote, candidate_text)
    label, reasons = release_status(quote)
    quote["release_status"] = label
    quote["release_reasons"] = reasons
    quote["rulebook_version"] = rules_engine.rulebook_version()
    quote["price_basis"] = rules_engine.INFLOW_SOURCE_LABEL
    return quote
