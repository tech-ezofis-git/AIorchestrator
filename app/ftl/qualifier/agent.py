"""
The Qualifier's agentic decision loop: skill instructions + candidate text (from extract.py) ->
bounded tool-calling rounds against search_pricelist -> a forced final tool call that produces the
structured decision. Same shape as the SALES CRM reference project's qualifier_agent.py, but
DB-free: search_pricelist reads pricelist_store.py's local JSON index instead of a SQL table.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

try:
    from app.ftl.qualifier.pricelist_store import search_pricelist
except ImportError:
    from pricelist_store import search_pricelist

MAX_LOOKUP_ROUNDS = 4


# extract.py's render_candidate_text_for_model always opens with this line — a hard, deterministic
# signal computed in code from PART 1/PART 2/PART 3 heading detection, independent of anything the
# model reasons about.
_STRUCTURE_SIGNAL_RE = re.compile(r"^## Detected structure signal:\s*(\S+)", re.MULTILINE)


def _enforce_new_construction_disqualify(decision: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A deterministic backstop for Step 5's project-type override: repeated live testing showed
    the model, even when it correctly names the "new_construction defaults to disqualify" policy in
    its own reasoning, keeps talking itself out of applying it whenever Step 1 also found several
    grounded in-scope items — landing on `qualify` (or a wavering `needs_review`) instead. Since
    extract.py's structure_signal is already a hard, code-computed value (not a model judgment) and
    the skill's own policy is unconditional ("default to disqualify even when in-scope items are
    technically requested"), this enforces that policy in code rather than leaving it to a nano-tier
    model to keep re-deriving reliably. Deliberately narrow: only fires when the code-level signal
    positively identifies new_construction_single_spec (a confident, low-false-positive detection —
    PART 1/2/3 heading structure) — it never touches a genuinely uncertain ("unknown") case, which
    should still be free to land on needs_review or qualify per the model's own judgment."""
    m = _STRUCTURE_SIGNAL_RE.search(candidate_text or "")
    if not m or m.group(1) != "new_construction_single_spec":
        return decision
    if decision.get("qualify") == "disqualify":
        return decision
    decision = dict(decision)
    original_call = decision.get("qualify")
    decision["qualify"] = "disqualify"
    decision["project_type"] = "new_construction"
    decision["reasoning"] = (
        f"(Auto-overridden from '{original_call}' to 'disqualify': extract.py's code-level structure "
        "detection confidently identified this as a new-construction, single-spec tender (PART 1/"
        "PART 2/PART 3 structure), and FTL's stated policy is to default to disqualify for this "
        "tender type even when in-scope items are technically requested — see Step 5. This override "
        "is enforced deterministically because repeated live testing showed the model correctly "
        "naming this policy in its own reasoning but then not applying it when several in-scope "
        "items were also found.)\n\n" + (decision.get("reasoning") or "")
    )
    decision["flags"] = list(decision.get("flags") or []) + [
        "auto_overridden_new_construction_disqualify"
    ]
    return decision


# Keyword tags for the categories Step 4 explicitly says count toward the qualify threshold even
# when only "ambiguous" (ROLLER_GUIDE/GOVERNOR/CAR_SAFETY/CLUTCH/PANEL_ADAPTOR), versus the two
# door-package tags (DOOR_OPERATOR/DETECTOR) that Step 3/Step 4 say can NEVER carry the decision on
# their own when unpaired — plus POWER_SUPPLY, which Step 3 says is a pure derivative of an unpaired
# detector and can never count as an independent category either.
_ITEM_CATEGORY_KEYWORDS = {
    "ROLLER_GUIDE": ("roller guide",),
    "GOVERNOR": ("governor",),
    "CAR_SAFETY": ("car safety", "car safeties", "unidirectional", "safeties", "safety"),
    "CLUTCH": ("clutch",),
    "PANEL_ADAPTOR": ("panel adaptor", "panel adapter", "car door panel", "door panel"),
    "DOOR_OPERATOR": ("door operator", "operator"),
    "DETECTOR": ("detector", "protective device"),
    "POWER_SUPPLY": ("power supply",),
}
# Categories Step 4 says count independently, even on an "ambiguous" match, toward "a single
# grounded item is enough to qualify."
_INDEPENDENT_CATEGORIES = {"ROLLER_GUIDE", "GOVERNOR", "CAR_SAFETY", "CLUTCH", "PANEL_ADAPTOR"}


def _tags_for_item(entry: Dict[str, Any]) -> set:
    text = " ".join(
        str(entry.get(k) or "") for k in ("item", "category", "note")
    ).lower()
    tags = set()
    for tag, keywords in _ITEM_CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)
    return tags


def _has_qualifying_grounded_item(matched_items: Any) -> bool:
    """Shared by both backstops below: does this matched_items array contain at least one item that
    Step 4 says counts toward "a single grounded item is enough to qualify" — i.e. anything besides
    the lone, unpaired door-operator-or-detector (and its derivative power-supply) Step 3 excludes
    from counting alone? A cleanly-matched operator+detector PAIR counts too (Step 4 explicitly
    allows "a door operator/detector pairing that IS cleanly matched together"); it's only the LONE,
    unpaired one of the two that's excluded. Used to distinguish a real "zero independently-grounded
    items" case (which Step 4 says is a confident disqualify) from a case that merely has nothing
    BUT the excluded door-pairing item(s) — the latter is functionally the same "zero" case for this
    purpose, which is exactly the distinction a real test run got wrong (see
    _enforce_unknown_project_type_needs_review)."""
    if not isinstance(matched_items, list):
        return False
    independent_hit = False
    has_operator = False
    has_detector = False
    for entry in matched_items:
        if not isinstance(entry, dict):
            continue
        if entry.get("match") not in ("exact", "ambiguous"):
            continue
        tags = _tags_for_item(entry)
        if tags & _INDEPENDENT_CATEGORIES:
            independent_hit = True
            break
        if "DOOR_OPERATOR" in tags:
            has_operator = True
        if "DETECTOR" in tags:
            has_detector = True
    if not independent_hit and has_operator and has_detector:
        independent_hit = True
    return independent_hit


def _enforce_unknown_project_type_needs_review(decision: Dict[str, Any]) -> Dict[str, Any]:
    """A deterministic backstop for Step 5's "unknown means needs_review, full stop" rule:
    repeated live testing (this exact case confirmed live, plus two earlier confirmed instances)
    showed the model writing down project_type: "unknown", correctly stating in its own reasoning
    that this means needs_review — and then, in the same breath, computing a verdict via
    item-matching anyway ("Step 1/4 indicate insufficient confidently countable items... overall
    recommendation is disqualify") and landing on `disqualify` (or, in principle, `qualify`)
    instead. Step 5 is explicit and unconditional here: once project_type is "unknown", qualify is
    needs_review, with exactly one carve-out — Step 4's "zero grounded items" case, where zero-items
    wins and the call is `disqualify` instead. Uses the same door-pairing-aware
    _has_qualifying_grounded_item check as _enforce_ambiguous_item_qualify_threshold below (NOT a
    naive "any matched_items entries" check) — a lone detector/power-supply-only matched_items array
    is functionally "zero grounded items" for this purpose, per Step 3/Step 4, and must still resolve
    to disqualify, not needs_review; an earlier version of this function got exactly this case wrong
    on a live sample where the model's own reasoning was already the CORRECT disqualify (lone
    detector, no other grounded categories) and this override wrongly bumped it to needs_review.
    Only acts when project_type is exactly "unknown" — a confidently-classified new_construction has
    already been handled (and project_type overwritten away from "unknown") by
    _enforce_new_construction_disqualify above, so this never fights that rule; a confidently-
    classified modernization is untouched here and is left to
    _enforce_ambiguous_item_qualify_threshold below."""
    if decision.get("project_type") != "unknown":
        return decision
    matched_items = decision.get("matched_items") or []
    has_items = _has_qualifying_grounded_item(matched_items)
    target = "needs_review" if has_items else "disqualify"
    if decision.get("qualify") == target:
        return decision
    decision = dict(decision)
    original_call = decision.get("qualify")
    decision["qualify"] = target
    # NOTE: the model's own original reasoning text (appended below, unedited) almost always ends
    # on ITS OWN concluding sentence — e.g. "Therefore: disqualify based on..." — because that's
    # the verdict the model computed via item-matching before this override ran. Left as the very
    # last thing in the field, that stale conclusion reads as if it were the final word, directly
    # contradicting the override note above it and the actual enforced `qualify` value. A live
    # sample confirmed exactly this: qualify == "needs_review" while reasoning's last sentence still
    # said "Therefore: disqualify...". So a short closing correction is appended AFTER the model's
    # original text too, not just prepended before it — whoever reads reasoning top-to-bottom or
    # jumps straight to its last paragraph both land on the correct, enforced verdict.
    original_reasoning = decision.get("reasoning") or ""
    decision["reasoning"] = (
        f"(Auto-overridden from '{original_call}' to '{target}': project_type is 'unknown', and "
        "Step 5's policy is that this settles the qualify verdict on its own — needs_review when "
        "at least one independently-grounded item is present (which item-matching cannot override "
        "back to disqualify or forward to qualify), or disqualify only in the zero-grounded-items "
        "case per Step 4's mirror-case carve-out (a lone, unpaired door-operator/detector item counts "
        "as zero for this purpose, same as Step 3 says for the qualify threshold itself). This "
        "override is enforced deterministically because repeated live testing showed the model "
        "naming this exact rule in its own reasoning and then computing a verdict via item-matching "
        "anyway.)\n\n" + original_reasoning + (
            f"\n\n(Note: the enforced, final verdict is '{target}' — any concluding sentence above "
            f"this note that the model itself wrote (e.g. naming a different verdict) reflects its "
            f"pre-override item-matching pass, superseded by the auto-override explained at the top "
            f"of this field.)" if original_reasoning else ""
        )
    )
    decision["flags"] = list(decision.get("flags") or []) + [
        f"auto_overridden_unknown_project_type_{target}"
    ]
    return decision


def _enforce_ambiguous_item_qualify_threshold(decision: Dict[str, Any]) -> Dict[str, Any]:
    """A deterministic backstop for Step 4's qualify threshold: repeated live testing (at least
    three separate confirmed instances, on three different real RFQs) showed the model naming
    matched_items like a governor, roller guide assemblies, or car safeties as grounded and
    in-scope-or-ambiguous per Step 1/Step 2 — then disqualifying (or hedging to needs_review)
    anyway because none of those items had a fully-specified catalog variant ("no exact matchable
    purchasable variant," "sizing clarity" not confirmed). Step 2 and Step 4 are explicit that an
    ambiguous match — missing only its exact variant/size, not its category or its "was this
    requested" grounding — still counts toward the qualify threshold; only Step 1's checks (was it
    requested for this unit, right product type, supported OEM) can drop an item, and "we don't know
    which variant" is not one of those checks. Since the model's own matched_items array is already
    the record of what it itself decided was grounded, this only has to look at that array — it
    never re-derives grounding itself — and only overrides when the model's own matched_items
    already contain a category besides the door-operator/detector pairing Step 3 excludes from
    counting alone. Deliberately skipped when project_type is new_construction (Step 5's override
    is a different, higher-precedence dimension — see _enforce_new_construction_disqualify) or
    unknown (Step 5's "unknown means needs_review, full stop" rule is not an item-counting question
    either — see _enforce_unknown_project_type_needs_review) so this never fights either of those
    rules."""
    if decision.get("qualify") == "qualify":
        return decision
    project_type = decision.get("project_type")
    if project_type in ("new_construction", "unknown"):
        return decision
    matched_items = decision.get("matched_items") or []
    if not _has_qualifying_grounded_item(matched_items):
        return decision

    decision = dict(decision)
    original_call = decision.get("qualify")
    decision["qualify"] = "qualify"
    decision["reasoning"] = (
        f"(Auto-overridden from '{original_call}' to 'qualify': the model's own matched_items "
        "already include a grounded, in-scope-or-ambiguous item outside the door-operator/detector "
        "pairing Step 3 excludes from counting alone (e.g. roller guide, governor, car safety, "
        "clutch, or panel adaptor) — Step 4 says a single such item, even an ambiguous one missing "
        "only its exact catalog variant, is enough to qualify. This override is enforced "
        "deterministically because repeated live testing showed the model naming these items as "
        "grounded and then disqualifying/reviewing anyway for lacking full variant/sizing detail, "
        "which Step 2 explicitly says is not a disqualifying gap.)\n\n" + (decision.get("reasoning") or "")
    )
    decision["flags"] = list(decision.get("flags") or []) + [
        "auto_overridden_ambiguous_item_qualify_threshold"
    ]
    return decision


def _apply_policy_overrides(decision: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """Apply Git's deterministic qualify backstops without changing the live model client."""
    decision = _enforce_new_construction_disqualify(decision, candidate_text)
    decision = _enforce_unknown_project_type_needs_review(decision)
    return _enforce_ambiguous_item_qualify_threshold(decision)


# Cosine-similarity floor below which search_pricelist's top result is treated as "nothing real
# matched" rather than a genuine catalog hit — see the warning built in _run_tool_call. This is a
# heuristic, not a calibrated value from real embedding data (no live pricelist index was available
# to tune it against) — adjust if it proves too strict/loose once run against the real Wittur
# pricelist embeddings.
_LOW_CONFIDENCE_SCORE = 0.35

_SEARCH_PRICELIST_TOOL = {
    "type": "function",
    "function": {
        "name": "search_pricelist",
        "description": (
            "Search FTL's Wittur pricelist (the agent's only knowledge base) for a catalog match "
            "to an RFQ line item. Call this once per distinct item you need to classify — don't "
            "guess at a match without checking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short description of the item to look up, e.g. 'center opening door operator 42 inch GAL' or 'counterweight roller guide 3 inch rail'.",
                }
            },
            "required": ["query"],
        },
    },
}

_AI_INSIGHT_DESCRIPTION = (
    "2-4 sentences for the FTL sales team: how attractive this opportunity is for FTL, the main "
    "risk or open question to clarify with the customer, and the recommended next step. Do not "
    "repeat the matched/excluded item lists or the reasoning."
)

_SUBMIT_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_qualification_decision",
        "description": "Submit your final qualification decision for this RFQ. Always call this exactly once, as your last action.",
        "parameters": {
            "type": "object",
            "properties": {
                "qualify": {"type": "string", "enum": ["qualify", "disqualify", "needs_review"]},
                "project_type": {"type": "string", "enum": ["modernization", "new_construction", "unknown"]},
                "matched_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "category": {"type": "string"},
                            "match": {"type": "string", "enum": ["exact", "ambiguous"]},
                            "catalog_ref": {"type": ["string", "null"]},
                            "note": {"type": "string"},
                        },
                        "required": ["item", "category", "match"],
                    },
                },
                "excluded_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"item": {"type": "string"}, "reason": {"type": "string"}},
                        "required": ["item", "reason"],
                    },
                },
                "flags": {"type": "array", "items": {"type": "string"}},
                "deadline": {"type": ["string", "null"]},
                "project_name": {"type": "string"},
                "customer_name": {
                    "type": "string",
                    "description": "Company that sent the RFQ (the customer / contractor), from the email or spec. Empty if not stated.",
                },
                "reasoning": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "ai_insight": {"type": "string", "description": _AI_INSIGHT_DESCRIPTION},
            },
            "required": ["qualify", "project_type", "matched_items", "excluded_items", "flags", "reasoning", "confidence", "ai_insight"],
        },
    },
}


def build_system_prompt(skill: Dict[str, Any], is_json_mode: bool = False) -> str:
    """IMPORTANT: this must fold in skill['references'] (and templates), not just
    skill['instructions'] — a prior version of this function silently dropped references
    entirely, so anything edited into a reference doc never reached the model no matter how the
    skill was edited via the UI. Same bug was found and fixed in the Quote Estimator's agent.py;
    ported here for consistency since both projects share the identical skill-store shape."""
    instructions = (skill.get("instructions") or "").strip()
    parts = [instructions] if instructions else []

    references = skill.get("references") or []
    ref_blocks = []
    for ref in references:
        title = (ref.get("title") or "Untitled").strip()
        content = (ref.get("content") or "").strip()
        if content:
            ref_blocks.append(f"### {title}\n\n{content}")
    if ref_blocks:
        parts.append("## Reference material (binding — treat exactly like the instructions above)\n\n" + "\n\n".join(ref_blocks))

    templates = skill.get("templates") or []
    tpl_blocks = []
    for tpl in templates:
        name = (tpl.get("name") or "Untitled").strip()
        content = (tpl.get("content") or "").strip()
        if content:
            tpl_blocks.append(f"### {name}\n\n{content}")
    if tpl_blocks:
        parts.append("## Output/notification templates (style reference only)\n\n" + "\n\n".join(tpl_blocks))

    if is_json_mode:
        parts.append(
            "## Available Tools & Response Format\n"
            "You MUST respond ONLY with a single valid JSON object (no markdown, no backticks, no text outside JSON).\n\n"
            "1. To look up an item in FTL's catalog/pricelist:\n"
            '{"action": "search_pricelist", "query": "item description to look up"}\n\n'
            "2. When you have verified all items and are ready to submit your qualification decision:\n"
            '{"action": "submit_qualification_decision", "decision": {\n'
            '  "qualify": "qualify" | "disqualify" | "needs_review",\n'
            '  "project_type": "modernization" | "new_construction" | "unknown",\n'
            '  "matched_items": [{"item": "...", "category": "...", "match": "exact" | "ambiguous", "catalog_ref": "..." | null, "note": "..."}],\n'
            '  "excluded_items": [{"item": "...", "reason": "..."}],\n'
            '  "flags": ["..."],\n'
            '  "deadline": "..." | null,\n'
            '  "project_name": "...",\n'
            '  "customer_name": "company that sent the RFQ, or empty",\n'
            '  "reasoning": "...",\n'
            '  "confidence": 0.0 to 1.0,\n'
            '  "ai_insight": "..."\n'
            '}}\n\n'
            f"ai_insight: {_AI_INSIGHT_DESCRIPTION}\n\n"
            "You will receive candidate text extracted from an RFQ. Use search_pricelist to verify catalog items before deciding. When complete, output your decision JSON."
        )
    else:
        parts.append(
            "You will be given the candidate text extracted from one RFQ (email + targeted spec "
            "excerpts). Use the search_pricelist tool to check any item you're unsure matches FTL's "
            "catalog before deciding. When you're done, call submit_qualification_decision exactly "
            "once with your full structured decision — do not respond with plain text."
        )

    return "\n\n---\n\n".join(parts)


def _run_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    if name == "search_pricelist":
        results = search_pricelist(arguments.get("query", ""), top_k=5)
        if not results:
            return json.dumps({"results": [], "note": "No pricelist matches found for this query."}, default=str)
        payload: Dict[str, Any] = {"results": results}
        top_score = results[0].get("score", 0.0) if results else 0.0
        if top_score < _LOW_CONFIDENCE_SCORE:
            # search_pricelist always returns its top-k closest chunks by cosine similarity, even
            # when nothing in the catalog is actually a real hit (e.g. a real case: querying for a
            # "sliding guide" — a product Wittur doesn't sell — still returned roller-guide chunks
            # as the "closest" match, which the model then wrongly reported as a matched item). A
            # bare results list can't distinguish "here's your part" from "here's the least-bad
            # option in an unrelated catalog," so flag it explicitly rather than relying on the
            # model to notice the low score on its own.
            payload["warning"] = (
                f"Best match score is only {top_score:.2f} (cosine similarity, 1.0 = identical) — "
                "this is likely NOT a genuine catalog match, just the closest thing on file. Do "
                "not report this as an 'exact' or 'ambiguous' match on the strength of this result "
                "alone; treat the item as out of scope/excluded unless the result text itself "
                "independently confirms it's the same product the RFQ is asking for."
            )
        return json.dumps(payload, default=str)
    return json.dumps({"error": f"Unknown tool: {name}"}, default=str)


def _run_qualification_json_mode(client: OpenAI, model_name: str, skill: Dict[str, Any], candidate_text: str) -> Tuple[Dict[str, Any], int]:
    system_prompt = build_system_prompt(skill, is_json_mode=True)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": candidate_text},
    ]
    total_tokens = 0

    for round_num in range(MAX_LOOKUP_ROUNDS + 1):
        force_final = round_num == MAX_LOOKUP_ROUNDS
        if force_final:
            messages.append(
                {
                    "role": "user",
                    "content": "Please submit your final qualification decision now using action submit_qualification_decision in valid JSON.",
                }
            )

        create_kwargs: Dict[str, Any] = {"model": model_name, "messages": messages}
        if "gpt-5" not in model_name.lower():
            create_kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**create_kwargs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            continue

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Your response was not valid JSON. Please return valid JSON only."})
            continue

        action = data.get("action")
        # Check if decision was returned directly or via action
        if action == "submit_qualification_decision" or "qualify" in data or (isinstance(data.get("decision"), dict) and "qualify" in data["decision"]):
            decision = data.get("decision") if (isinstance(data.get("decision"), dict) and "qualify" in data["decision"]) else data
            # Fill default values if missing
            decision = _apply_policy_overrides(decision, candidate_text)
            decision.setdefault("qualify", "needs_review")
            decision.setdefault("project_type", "unknown")
            decision.setdefault("matched_items", [])
            decision.setdefault("excluded_items", [])
            decision.setdefault("flags", [])
            decision.setdefault("deadline", None)
            decision.setdefault("project_name", "")
            decision.setdefault("customer_name", "")
            decision.setdefault("reasoning", "")
            decision.setdefault("confidence", 0.8)
            decision.setdefault("ai_insight", "")
            return decision, total_tokens

        if action == "search_pricelist":
            query = data.get("query", "")
            result = _run_tool_call("search_pricelist", {"query": query})
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Tool search_pricelist result for '{query}':\n{result}"})
            continue

        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": "Please respond with either {\"action\": \"search_pricelist\", \"query\": \"...\"} or {\"action\": \"submit_qualification_decision\", \"decision\": {...}}.",
            }
        )

    raise RuntimeError(f"Model did not submit a decision within {MAX_LOOKUP_ROUNDS} rounds.")


def _run_qualification_native_tools(client: Any, model_name: str, skill: Dict[str, Any], candidate_text: str) -> Tuple[Dict[str, Any], int]:
    system_prompt = build_system_prompt(skill, is_json_mode=False)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": candidate_text},
    ]
    tools = [_SEARCH_PRICELIST_TOOL, _SUBMIT_DECISION_TOOL]
    total_tokens = 0

    for round_num in range(MAX_LOOKUP_ROUNDS + 1):
        force_final = round_num == MAX_LOOKUP_ROUNDS
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice=(
                {"type": "function", "function": {"name": "submit_qualification_decision"}}
                if force_final
                else "auto"
            ),
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

        choice = resp.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "Please respond only via a tool call — either search_pricelist or, if you're ready, submit_qualification_decision.",
                }
            )
            continue

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        decision_call = next((tc for tc in tool_calls if tc.function.name == "submit_qualification_decision"), None)
        if decision_call is not None:
            try:
                decision = json.loads(decision_call.function.arguments)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Model returned invalid JSON for its decision: {e}") from e
            decision = _apply_policy_overrides(decision, candidate_text)
            return decision, total_tokens

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_tool_call(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    raise RuntimeError(f"Model did not submit a decision within {MAX_LOOKUP_ROUNDS} tool-call rounds.")


def run_qualification(
    skill: Dict[str, Any],
    candidate_text: str,
    llm_overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], int]:
    """Runs the bounded agentic loop and returns (decision_dict, total_tokens).

    Model and API key come from the same preset overrides other agents use
    (catalog / tenant selection, or the process default ezofis-gpu-box).
    """
    from app.ftl.llm import open_client, prefers_json_mode, resolve_llm_config

    config = resolve_llm_config(llm_overrides)
    client, deploy_model = open_client(config)
    model_name = str(config.get("model") or deploy_model)

    if prefers_json_mode(model_name):
        return _run_qualification_json_mode(client, deploy_model, skill, candidate_text)
    try:
        return _run_qualification_native_tools(client, deploy_model, skill, candidate_text)
    except Exception as e:
        if "tool" in str(e).lower() or "400" in str(e):
            return _run_qualification_json_mode(client, deploy_model, skill, candidate_text)
        raise

