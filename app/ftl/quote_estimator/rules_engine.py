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

_CONFLICTS_BY_ID: Dict[str, Dict[str, Any]] = {c["issue_id"]: c for c in CONFLICTS if c.get("issue_id")}
_TRIGGERS_BY_ID: Dict[str, Dict[str, Any]] = {t["trigger_id"]: t for t in REVIEW_TRIGGERS if t.get("trigger_id")}
_RULES_BY_ID: Dict[str, Dict[str, Any]] = {r["rule_id"]: r for r in DECISION_RULES if r.get("rule_id")}
_PRODUCT_BY_CODE: Dict[str, Dict[str, Any]] = {
    p["ftl_product_code"]: p for p in PRODUCT_MASTER if p.get("ftl_product_code")
}


def cite_conflict(issue_id: str) -> str:
    """Renders a Controlled Conflict Register entry as a citation string suitable for an
    `assumptions`/`note` field — always includes the safe interim rule and who owns the decision,
    so a human reader never has to go dig up the rulebook to know what to do next."""
    c = _CONFLICTS_BY_ID.get(issue_id)
    if not c:
        return f"[{issue_id}] (conflict register entry not found — check data/rulebook/conflicts.json)"
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
    return _PRODUCT_BY_CODE.get(code)


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
