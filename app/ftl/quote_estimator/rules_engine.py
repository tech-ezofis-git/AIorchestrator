"""
FTL Stage 2 rulebook data layer: loads the versioned JSON extracted from FTL's
"AI Estimating Knowledge Base — Stage 2" workbook and "Qualification Rulebook — Stage 2" document
(data/rulebook/*.json — see scripts/build_rulebook_data.py for how they were generated from the
source .xlsx) and exposes small, targeted lookup/citation helpers used by agent.py's post-
processing pipeline.

This module is a DATA layer, not a general rule interpreter — it does not try to parse the 48
Decision Rules' free-text trigger/action columns into executable logic generically (that would be
guessing at intent from prose, the exact failure mode this whole rulebook exists to eliminate).
Instead, agent.py has one targeted enforcement function per high-value, unambiguous rule (safety
gear, WRG gating, governor tension companions, the OL100/programming-tool/freight/universal-door
CRITICAL conflicts) and each of those functions calls into this module only to fetch the exact
product/price/citation text, never to re-derive the rule itself.

Golden rule, straight from the rulebook: "If any trigger is true, the agent may summarize the issue
but must not invent the missing selection, SKU, price or quantity." Every conflict in conflicts.json
stays OPEN here — nothing in this module silently picks a winner between two competing values.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "rulebook")


def _load(name: str) -> List[Dict[str, Any]]:
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


PRODUCT_MASTER: List[Dict[str, Any]] = _load("product_master.json")
DECISION_RULES: List[Dict[str, Any]] = _load("decision_rules.json")
REQUIRED_INPUTS: List[Dict[str, Any]] = _load("required_inputs.json")
TECHNICAL_LIMITS: List[Dict[str, Any]] = _load("technical_limits.json")
REVIEW_TRIGGERS: List[Dict[str, Any]] = _load("review_triggers.json")
CONFLICTS: List[Dict[str, Any]] = _load("conflicts.json")
TRAINING_EXAMPLES: List[Dict[str, Any]] = _load("training_examples.json")
SOURCE_REGISTER: List[Dict[str, Any]] = _load("source_register.json")

# Price book overlay — FTL's Contractor Price Book (.docx) layered on the Stage 2 Product Master.
# product_master.json goes stale when FTL publishes a new price list; the overlay is generated from
# the price book (app/ftl/qualifier/price_book.py) and wins wherever both describe an item. Rows the
# price book doesn't list stay as the Product Master has them. No overlay file => behaviour unchanged.
def _load_overlay() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "price_book_overlay.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


PRICE_BOOK: Dict[str, Any] = _load_overlay()


def rulebook_status() -> Dict[str, int]:
    """Row counts of every loaded rulebook table."""
    return {
        "product_master": len(PRODUCT_MASTER),
        "decision_rules": len(DECISION_RULES),
        "required_inputs": len(REQUIRED_INPUTS),
        "technical_limits": len(TECHNICAL_LIMITS),
        "review_triggers": len(REVIEW_TRIGGERS),
        "conflicts": len(CONFLICTS),
        "training_examples": len(TRAINING_EXAMPLES),
        "source_register": len(SOURCE_REGISTER),
        "price_book_rows": len(PRICE_BOOK.get("rows") or []),
    }


_ALIAS_TO_CANONICAL: Dict[str, str] = {}

_PRICE_BOOK_FAMILY_TO_MASTER = {
    "door_operator": "Door System",
    "door_tools": "Door System",
    "panel_adaptor": "Door System",
    "clutch": "Door System",
    "roller_guide": "Roller Guides",
    "governor": "Governors",
    "car_safety": "Safety Gear",
}


def _apply_price_book() -> None:
    rows = PRICE_BOOK.get("rows") or []
    if not rows:
        return
    source = PRICE_BOOK.get("source", "price book")
    position = {p.get("ftl_product_code"): i for i, p in enumerate(PRODUCT_MASTER)}
    for row in rows:
        code = row["code"]
        aliases = list(row.get("aliases") or [])
        idx = position.get(code)
        if idx is None:
            for alias in aliases:
                if alias in position:
                    idx = position[alias]
                    break
        if idx is not None:
            old = PRODUCT_MASTER[idx]
            new = dict(old)
            old_code = old.get("ftl_product_code")
            new["ftl_product_code"] = code
            old_price = old.get("price_cad")
            new["price_cad"] = row["price_cad"]
            if old_price is not None and abs(float(old_price) - float(row["price_cad"])) > 0.005:
                new["price_book_previous_price_cad"] = old_price
            new["source"] = source
            if aliases:
                new["aliases"] = aliases
            PRODUCT_MASTER[idx] = new
            position.pop(old_code, None)
            position[code] = idx
        else:
            PRODUCT_MASTER.append(
                {
                    "family": _PRICE_BOOK_FAMILY_TO_MASTER.get(row.get("family"), "Door System"),
                    "type": row.get("type") or "",
                    "oem": row.get("oem") or "ALL",
                    "ftl_product_code": code,
                    "description": row.get("description") or code,
                    "hand": row.get("hand") or "N/A",
                    "width_in": row.get("width_in"),
                    "uom": "pair" if row.get("family") == "car_safety" else "each",
                    "price_cad": row["price_cad"],
                    "automation_status": "HOLD" if row.get("family") == "car_safety" else "CONDITIONAL",
                    "quantity_basis": "Per price book",
                    "companion_selection_rule": (
                        "Never auto-add; engineering selection required"
                        if row.get("family") == "car_safety"
                        else "Add only when scope calls for it"
                    ),
                    "source": source,
                    "notes_issue": "Added from the price book; not in the Stage 2 Product Master.",
                    "aliases": aliases,
                }
            )
            position[code] = len(PRODUCT_MASTER) - 1
        for alias in aliases:
            _ALIAS_TO_CANONICAL[alias] = code

    for product in PRODUCT_MASTER:
        if product.get("ftl_product_code") == "SGV2_DOOR_TOOLS" and "I-001" in (PRICE_BOOK.get("resolves_conflicts") or {}):
            product["notes_issue"] = "I-001 resolved: " + PRICE_BOOK["resolves_conflicts"]["I-001"]


_apply_price_book()

_CONFLICTS_BY_ID: Dict[str, Dict[str, Any]] = {c["issue_id"]: c for c in CONFLICTS if c.get("issue_id")}
_TRIGGERS_BY_ID: Dict[str, Dict[str, Any]] = {t["trigger_id"]: t for t in REVIEW_TRIGGERS if t.get("trigger_id")}
_RULES_BY_ID: Dict[str, Dict[str, Any]] = {r["rule_id"]: r for r in DECISION_RULES if r.get("rule_id")}
_PRODUCT_BY_CODE: Dict[str, Dict[str, Any]] = {
    p["ftl_product_code"]: p for p in PRODUCT_MASTER if p.get("ftl_product_code")
}


def canonical_code(code: str) -> str:
    """Current price-book code when `code` is an old spelling the price book renamed; otherwise unchanged."""
    return _ALIAS_TO_CANONICAL.get(code, code)


def all_known_codes() -> set:
    """Every code the price book defines, plus the old codes it replaced."""
    codes = {row["code"] for row in (PRICE_BOOK.get("rows") or [])}
    codes.update(_ALIAS_TO_CANONICAL)
    return codes


def price_book_resolution(issue_id: str) -> Optional[str]:
    return (PRICE_BOOK.get("resolves_conflicts") or {}).get(issue_id)


def cite_conflict(issue_id: str) -> str:
    """Renders a Controlled Conflict Register entry as a citation string suitable for an
    `assumptions`/`note` field — always includes the safe interim rule and who owns the decision,
    so a human reader never has to go dig up the rulebook to know what to do next. An entry the
    price book has since settled is rendered as RESOLVED."""
    c = _CONFLICTS_BY_ID.get(issue_id)
    if not c:
        return f"[{issue_id}] (conflict register entry not found — check data/rulebook/conflicts.json)"
    resolution = price_book_resolution(issue_id)
    if resolution:
        return (
            f"[{issue_id}, RESOLVED by {PRICE_BOOK.get('source', 'price book')}] "
            f"{c.get('issue')} — {resolution}"
        )
    return (
        f"[{issue_id}, {c.get('priority')}/{c.get('status')}] {c.get('issue')} — safe interim rule: "
        f"{c.get('safe_interim_rule')} (decision needed from: {c.get('decision_needed')})"
    )


def cite_trigger(trigger_id: str) -> str:
    t = _TRIGGERS_BY_ID.get(trigger_id)
    if not t:
        return f"[{trigger_id}] (review trigger not found — check data/rulebook/review_triggers.json)"
    return (
        f"[{trigger_id}, {t.get('severity')}] {t.get('condition')} — {t.get('agent_response')}. "
        f"Required resolution: {t.get('required_resolution')}"
    )


def cite_rule(rule_id: str) -> str:
    r = _RULES_BY_ID.get(rule_id)
    if not r:
        return f"[{rule_id}] (decision rule not found — check data/rulebook/decision_rules.json)"
    return f"[{rule_id}] {r.get('trigger_spec_says')} -> {r.get('agent_action')} ({r.get('rule_status')})"


def product_master_row(code: str) -> Optional[Dict[str, Any]]:
    return _PRODUCT_BY_CODE.get(code) or _PRODUCT_BY_CODE.get(_ALIAS_TO_CANONICAL.get(code, ""))


def governor_tension_companion(size: str) -> Optional[Dict[str, Any]]:
    """size: '35' or '100'. Returns the REQUIRED COMPANION product-master row for that governor
    family (OL35_TENSION_SHEAVE_SWINGARM / TBD_OL100_TENSION_ASSEMBLY) — the mandatory tension
    assembly every OL governor requires per the Aug 2026 JR/Francine controlling rule (S-029),
    regardless of what any survey/reuse option elsewhere says."""
    for p in PRODUCT_MASTER:
        if p.get("family") != "Governors" or p.get("automation_status") != "REQUIRED COMPANION":
            continue
        code = (p.get("ftl_product_code") or "").upper()
        if size == "35" and "OL35" in code:
            return p
        if size == "100" and "OL100" in code:
            return p
    return None


def safety_gear_row() -> Optional[Dict[str, Any]]:
    for p in PRODUCT_MASTER:
        if p.get("family") == "Safety Gear":
            return p
    return None


def wrg_row(size_token: str) -> Optional[Dict[str, Any]]:
    """size_token like 'WRG150HD', 'WRG80', matched against product_master's `type` column."""
    for p in PRODUCT_MASTER:
        if p.get("family") == "Roller Guides" and (p.get("type") or "").upper() == size_token.upper():
            return p
    return None


def door_tools_row() -> Optional[Dict[str, Any]]:
    return product_master_row("SGV2_DOOR_TOOLS")


def detector_row() -> Optional[Dict[str, Any]]:
    return product_master_row("FS_VISIONPLUS_3D_CAR_DOOR_DET")


def detector_power_supply_row() -> Optional[Dict[str, Any]]:
    return product_master_row("VISIONPLUS_POWERSUPPLY")


def universal_door_panel_rows() -> List[Dict[str, Any]]:
    return [p for p in PRODUCT_MASTER if "Universal Car Door" in (p.get("type") or "")]
