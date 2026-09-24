"""
The Quote Estimator's agentic loop: skill instructions + candidate text (from extract.py) ->
bounded tool-calling rounds against search_pricelist -> a forced final tool call that produces the
structured quote. Same shape as the Qualifier project's agent.py, separate copy, quote-specific
tool schema. The model supplies line items, quantities, unit prices and any bundled-item $0
overrides; it never computes Subtotal/Freight/HST/Total — that math is done deterministically in
quote_template.py so it can never be wrong or inconsistent from run to run.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI, AzureOpenAI

try:
    from app.ftl.quote_estimator.pricelist_store import (
        car_door_panel_prices,
        door_operator_prices,
        governor_prices,
        known_product_codes,
        panel_set_prices,
        search_pricelist,
    )
    from app.ftl.quote_estimator import rules_engine
except ImportError:
    from pricelist_store import (
        car_door_panel_prices,
        door_operator_prices,
        governor_prices,
        known_product_codes,
        panel_set_prices,
        search_pricelist,
    )
    import rules_engine

# Categories whose real product codes are clean single tokens found verbatim in the pricelist text
# (so a whitelist check is meaningful). IMPORTANT — roller_guide belongs here: a direct check of the
# live pricelist_index.json showed the real roller-guide PRODUCT NAME column values ARE plain single
# tokens present verbatim in the source PDF (e.g. "WRG_MOTION_GEAR150", "WRG_MOTION_GEAR80") — an
# earlier round of this skill's own history wrongly assumed these were a "derived" convention not
# checkable this way (based on a stale reference to a DIFFERENT, older project's invoice that used a
# now-superseded code style, "WRG150_CAR" — ordinary pricelist-revision drift, not a fabrication),
# and even added a regex guard enforcing that wrong pattern for one round before this was caught and
# reverted. Car door panels are still excluded: their real "code" in this pricelist is actually the
# entire multi-word PRODUCT NAME column text (spaces, dimensions and all — there is no separate short
# SKU for that section), which the single-token whitelist regex can't represent.
_WHITELIST_CHECKED_CATEGORIES = {
    "door_operator", "clutch", "panel_adaptor", "governor", "car_safety",
    "door_protective_device", "door_tools", "roller_guide",
}
# Known-bundled codes that are never expected to appear in the pricelist itself (per skill
# instructions, e.g. the car door restrictor is bundled with the operator and isn't its own SKU).
_WHITELIST_KNOWN_EXTRAS = {"SGV2_CAR_DOOR_RESTRICTOR"}

AGENT_VERSION = "2.0.0"
MAX_LOOKUP_ROUNDS = 8
# NOTE: this was bumped to 12 for one round of testing (to reduce forced-truncation quotes) but
# reverted after live testing showed a real regression: with the higher cap, a meaningful fraction
# of real runs (3 of 9 in one back-to-back batch) spiraled into far more search_pricelist rounds
# and total_tokens 4-6x a normal run (380k-570k vs a normal ~80k) without any corresponding gain in
# quote quality. The now-much-stronger candidate_text (see extract.py's computed door package
# breakdown) means the model needs far fewer rounds to get a correct quote in the first place, and
# the force_final incomplete-quote guard below already turns a genuine truncation into a clean,
# retryable error instead of a silently-bad quote — so the lower cap is the better tradeoff.
QUOTE_CHAT_MODEL = (
    os.getenv("QUOTE_CHAT_MODEL") or os.getenv("LLM_MODEL") or "qwen3.5-9b"
).strip()


def get_client() -> Union[OpenAI, AzureOpenAI]:
    """Create the configured LLM client without embedded credentials.

    Qwen-compatible endpoints use the OpenAI-compatible client. Other models use
    OpenAI when an sk-* key is configured, otherwise Azure OpenAI when its
    endpoint/key pair is present. Missing credentials fail closed.
    """
    model_name = (
        os.getenv("QUOTE_CHAT_MODEL") or os.getenv("LLM_MODEL") or "qwen3.5-9b"
    ).strip().lower()

    qwen_ep = (
        os.getenv("QWEN_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
    )
    qwen_key = (
        os.getenv("QWEN_API_KEY")
        or os.getenv("QWEN_MAC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )

    if "qwen" in model_name:
        if not qwen_ep:
            raise RuntimeError(
                "Qwen model selected but no endpoint is configured. Set QWEN_API_BASE "
                "or OPENAI_BASE_URL."
            )
        if not qwen_key:
            raise RuntimeError(
                "Qwen model selected but no API key is configured. Set QWEN_API_KEY "
                "or QWEN_MAC_API_KEY."
            )
        return OpenAI(base_url=qwen_ep, api_key=qwen_key)

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and openai_key.startswith("sk-"):
        return OpenAI(api_key=openai_key)

    azure_ep = (
        os.getenv("AZURE_OPENAI_ENDPOINT")
        or (
            os.getenv("AZURE_EAST_US_API_BASE", "https://api-4omin-ez.openai.azure.com")
            if "4o" in model_name
            else (os.getenv("AZURE_SOUTH_INDIA_API_BASE") or "https://ezazopenai.openai.azure.com")
        )
    )
    azure_key = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or (
            os.getenv("AZURE_EAST_US_API_KEY")
            if "4o" in model_name
            else (os.getenv("AZURE_SOUTH_INDIA_API_KEY") or os.getenv("AZURE_EAST_US_API_KEY"))
        )
    )
    if azure_ep and azure_key:
        return AzureOpenAI(
            azure_endpoint=azure_ep,
            api_key=azure_key,
            api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", "2025-01-01-preview"
            ),
        )

    if openai_key:
        # Support compatible gateways that use a non-sk-* key, but require an
        # explicit base URL so the key is never sent to an unintended endpoint.
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        if base_url:
            return OpenAI(base_url=base_url, api_key=openai_key)

    raise RuntimeError(
        "No valid LLM configuration found. Configure QWEN_API_BASE + QWEN_API_KEY, "
        "OPENAI_API_KEY, or AZURE_OPENAI_ENDPOINT + an Azure OpenAI API key."
    )


_SEARCH_PRICELIST_TOOL = {
    "type": "function",
    "function": {
        "name": "search_pricelist",
        "description": (
            "Search FTL's Wittur pricelist (the agent's only source of catalog codes and prices) "
            "for a match to an item the spec calls for. Call this once per distinct item — don't "
            "guess a product code or price without checking, and don't reuse a stale result for a "
            "differently-configured item (different door hand, width, OEM, rail size, etc. can all "
            "change the catalog code and price)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short description of the item to look up, e.g. 'center opening door operator 42 inch right hand' or 'car roller guide rail size 100'.",
                }
            },
            "required": ["query"],
        },
    },
}

_SUBMIT_QUOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_quote",
        "description": (
            "Submit the final structured Sales Estimate for this RFQ. Always call this exactly "
            "once, as your last action. Do not include Subtotal/Freight/HST/Total math — provide "
            "unit prices, quantities and any subtotal overrides; totals are computed separately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project or tender name, e.g. '120 Bloor Street East, Toronto, ON'."},
                "customer_name": {"type": "string"},
                "contact_name": {"type": "string"},
                "contact_phone": {"type": "string"},
                "billing_address": {"type": "string"},
                "shipping_address": {"type": "string"},
                "bdm": {"type": "string", "description": "FTL executive / business development manager on the account, if known — else leave blank."},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_code": {"type": "string", "description": "The FTL/Wittur catalog code from the pricelist, e.g. WRG100_CAR."},
                            "description": {"type": "string", "description": "Plain-language description, mirroring the pricelist's own description text."},
                            "category": {
                                "type": "string",
                                "enum": [
                                    "door_operator",
                                    "clutch",
                                    "panel_adaptor",
                                    "car_door_panel",
                                    "roller_guide",
                                    "governor",
                                    "car_safety",
                                    "door_protective_device",
                                    "door_tools",
                                    "other",
                                ],
                            },
                            "qty": {"type": "number"},
                            "unit_price": {"type": "number", "description": "Published unit price from the pricelist."},
                            "subtotal_override": {
                                "type": ["number", "null"],
                                "description": "Set to 0 (or another value) only when this line is bundled at no extra charge with a parent item on this same estimate. Leave null otherwise — subtotal defaults to qty * unit_price.",
                            },
                            "note": {"type": "string", "description": "Optional short note, e.g. 'included with SGV Operator MOD kit' or an uncertainty flag explanation."},
                            "needs_engineering_review": {
                                "type": "boolean",
                                "description": "True if this item's configuration doesn't cleanly match a pricelist line (unusual OEM, size, or variant) and a human should confirm before quoting.",
                            },
                        },
                        "required": ["product_code", "description", "category", "qty", "unit_price"],
                    },
                },
                "freight_estimate": {"type": "number", "description": "0 if there's no reasonable basis for an estimate — note that in freight_note."},
                "freight_note": {"type": "string", "description": "e.g. 'Lead times / freight confirmed at order.' if freight_estimate is 0."},
                "payment_terms": {
                    "type": "string",
                    "description": (
                        "Leave this BLANK unless the RFQ/email/customer record itself states specific "
                        "payment terms, or FTL's quoting rules require them for this customer/project. "
                        "Do not insert a default like '50/50 Net 30' just because it's common — a real "
                        "confirmed failure did exactly that on an estimate whose real counterpart had no "
                        "payment-terms line at all. When blank, the rendered quote omits this field "
                        "entirely, matching FTL's own real quotes that don't always state terms."
                    ),
                },
                "remarks": {
                    "type": "string",
                    "description": "Free-text remarks naming the project/tender and any provisional or confirm-before-release items, mirroring FTL's real phrasing.",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Open assumptions a human reviewer should double-check before this estimate goes out.",
                },
            },
            "required": ["project_name", "line_items", "freight_estimate", "remarks", "assumptions"],
        },
    },
}


def build_system_prompt(skill: Dict[str, Any], is_json_mode: bool = False) -> str:
    """IMPORTANT: this must fold in skill['references'] (and templates), not just
    skill['instructions'] — a prior version of this function silently dropped references
    entirely, so anything edited into a reference doc (e.g. the governor-default rule) never
    reached the model no matter how the skill was edited via the UI. Confirmed via a real quote
    that kept picking the wrong governor variant despite the "fix" living only in references."""
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
            "1. To search FTL's Wittur pricelist for an item:\n"
            '{"action": "search_pricelist", "query": "item description with dimensions/options"}\n\n'
            "2. When you have found all items and prices and are ready to submit your quote:\n"
            '{"action": "submit_quote", "quote": {\n'
            '  "project_name": "...",\n'
            '  "customer_name": "...",\n'
            '  "contact_name": "...",\n'
            '  "contact_phone": "...",\n'
            '  "billing_address": "...",\n'
            '  "shipping_address": "...",\n'
            '  "bdm": "...",\n'
            '  "line_items": [\n'
            '    {\n'
            '      "product_code": "...",\n'
            '      "description": "...",\n'
            '      "category": "door_operator" | "clutch" | "panel_adaptor" | "car_door_panel" | "roller_guide" | "governor" | "car_safety" | "door_protective_device" | "door_tools" | "other",\n'
            '      "qty": 1,\n'
            '      "unit_price": 100.0,\n'
            '      "subtotal_override": null,\n'
            '      "note": "...",\n'
            '      "needs_engineering_review": false\n'
            '    }\n'
            '  ],\n'
            '  "freight_estimate": 0,\n'
            '  "freight_note": "...",\n'
            '  "payment_terms": "",\n'
            '  "remarks": "...",\n'
            '  "assumptions": ["..."]\n'
            '}}\n\n'
            "You will be given the candidate text extracted from an RFQ. Use search_pricelist to find exact catalog codes and prices before submitting."
        )
    else:
        parts.append(
            "You will be given the candidate text extracted from one already-qualified RFQ (email + "
            "targeted spec excerpts). Use the search_pricelist tool to find the exact catalog code and "
            "price for every item before including it. When you're done, call submit_quote exactly "
            "once with your full structured estimate — do not respond with plain text."
        )

    return "\n\n---\n\n".join(parts)


_SCOPE_TABLE_SECTION_RE = re.compile(
    r"## Equipment scope table.*?\n(.*?)(?:\n##|\Z)", re.IGNORECASE | re.DOTALL
)
_SCOPE_TABLE_NEW_RE = re.compile(r"^\s*new\b", re.IGNORECASE)

# Category keywords that map cleanly onto this agent's own `category` enum, used ONLY to check
# whether the "Equipment scope table" (see extract.py) explicitly marks that category something
# other than New. Deliberately narrow — this is a targeted backstop for one specific, twice-
# reproduced real failure (see _enforce_scope_table below), not a general scope-table parser.
_SCOPE_TABLE_CATEGORY_ROW_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "roller_guide": ("car guide", "guide rail", "counterweight guide"),
}

# Some categories fail differently: the scope table doesn't mark them Refurbish/Retain, it simply
# never mentions them at all, because the project has no such scope item. A real, client-confirmed
# quote (EST-261132) had NO "Car Safety" row whatsoever in its scope table, yet a test run still
# added a full car-safety line — and did so inconsistently even after a prose-only fix (present in
# one run, absent in the next), so this absence case gets its own code-level check. Only applied
# when the table has enough rows to make silence meaningful (a 1-2 row table proves nothing).
_SCOPE_TABLE_MIN_ROWS_FOR_ABSENCE_CHECK = 6
_ABSENCE_CATEGORY_LABEL_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "car_safety": ("safet",),
}

# The counterweight tension weight/swingarm/idler assembly (e.g. OL35_TENSION_SHEAVE_SWINGARM) is
# never its own row in the Equipment scope table, so it can't be caught by the row-based check
# above — but it has been wrongly added in every real test run where it appeared (also explicitly
# named by the client as an error on EST-261132), and never once confirmed legitimately needed.
# Stripped by keyword match on product_code/description whenever a real scope table is present,
# same "auto-removed, re-add deliberately if genuinely needed" treatment as the row-based cases.
_SUSPECT_KEYWORD_LINE_ITEMS: Tuple[Tuple[str, ...], ...] = (
    ("swingarm",),
    ("tension", "sheave"),
    ("tension", "idler"),
)


def _line_item_matches_suspect_keywords(item: Dict[str, Any]) -> bool:
    text = f"{item.get('product_code') or ''} {item.get('description') or ''}".lower()
    return any(all(kw in text for kw in group) for group in _SUSPECT_KEYWORD_LINE_ITEMS)


def _parse_scope_table_rows(scope_text: str) -> List[Tuple[str, str]]:
    """The scope table (see extract.py's _find_equipment_scope_table) is a flat list of
    alternating label/status lines (e.g. "Car guides" then "Refurbish") once the "Equipment"/
    "Scope" column-header pair is skipped. Parsed line-by-line rather than with a single regex
    since the exact row count/order varies by project."""
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


def _enforce_scope_table(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A deterministic backstop for one specific, twice-reproduced real failure: the model
    correctly IDENTIFIES, in its own note/assumptions text, that the RFQ's own "Equipment scope
    table" marks a category (e.g. "Car guides") as Refurbish rather than New — and then includes a
    full-price new-equipment line for it anyway, "as provisional" or "pending confirmation." Two
    consecutive real test runs on the same RFQ did exactly this for roller guides, despite an
    explicit instruction forbidding it (see Step 1's "Equipment scope table" guidance) — prose
    alone has not reliably stopped a nano-tier model from hedging with a caveat-laden line instead
    of actually omitting it, so this enforces the exclusion in code instead of asking a sixth time.

    Deliberately narrow in scope: only strips a line when candidate_text actually contains a real
    "Equipment scope table" section (so this never fires on an RFQ that doesn't have one) AND that
    table's own row for a category this function knows how to recognize (see
    _SCOPE_TABLE_CATEGORY_ROW_KEYWORDS) explicitly says something other than New. Stripped lines
    move to `assumptions` with a note identifying exactly which scope-table row justified the
    removal, so a human can see the reasoning and overrule it if the mapping was wrong."""
    m = _SCOPE_TABLE_SECTION_RE.search(candidate_text or "")
    if not m:
        return quote
    rows = _parse_scope_table_rows(m.group(1))
    if not rows:
        return quote

    excluded_categories: Dict[str, str] = {}
    for category, keywords in _SCOPE_TABLE_CATEGORY_ROW_KEYWORDS.items():
        for label, status in rows:
            label_lower = label.lower()
            if any(kw in label_lower for kw in keywords) and not _SCOPE_TABLE_NEW_RE.match(status):
                excluded_categories[category] = f"{label}: {status}"
                break

    if len(rows) >= _SCOPE_TABLE_MIN_ROWS_FOR_ABSENCE_CHECK:
        for category, keywords in _ABSENCE_CATEGORY_LABEL_KEYWORDS.items():
            if category in excluded_categories:
                continue
            if not any(any(kw in label.lower() for kw in keywords) for label, _status in rows):
                excluded_categories[category] = (
                    f"no row for this category anywhere in the table (table lists {len(rows)} "
                    "other equipment categories)"
                )

    kept: List[Dict[str, Any]] = []
    dropped_notes: List[str] = list(quote.get("assumptions") or [])
    for item in quote.get("line_items") or []:
        cat = item.get("category")
        if cat in excluded_categories:
            desc = item.get("description") or item.get("product_code") or "an item"
            dropped_notes.append(
                f"Removed from the estimate: {desc} ({item.get('product_code')}) — the RFQ's own "
                f"Equipment scope table says \"{excluded_categories[cat]}\", so this is not new "
                f"scope; do not add a new-equipment line for it. (Auto-removed — a nano-tier model "
                f"has repeatedly included this category anyway despite correctly noting the "
                f"exclusion in its own reasoning; if this project genuinely needs new equipment "
                f"here despite the scope table, a human should re-add it deliberately.)"
            )
            continue
        if _line_item_matches_suspect_keywords(item):
            desc = item.get("description") or item.get("product_code") or "an item"
            dropped_notes.append(
                f"Removed from the estimate: {desc} ({item.get('product_code')}) — this looks like "
                "a counterweight tension weight/swingarm/idler assembly line. A real, client-"
                "confirmed quote (EST-261132) explicitly flagged this exact category as wrongly "
                "added, and the RFQ's own Equipment scope table has no row calling for it as new "
                "equipment. (Auto-removed — if this project genuinely needs this item despite the "
                "scope table, a human should re-add it deliberately.)"
            )
            continue
        kept.append(item)

    if len(kept) == len(quote.get("line_items") or []):
        return quote

    quote = dict(quote)
    quote["line_items"] = kept
    quote["assumptions"] = dropped_notes
    return quote


# Parses the "## COMPUTED door package breakdown" section extract.py renders into candidate_text
# (see compute_door_package_breakdown) back out, so the model's actual submission can be checked
# against it. Matches the exact format render_candidate_text_for_model produces.
_DOOR_PACKAGE_OPENING_GROUP_RE = re.compile(
    r"-\s*SGV2\s+(?P<family>\S+)\s+(?P<width>[\d.]+)\"\s*:\s*qty\s+(?P<qty>\d+)"
)
_DOOR_PACKAGE_PANEL_GROUP_RE = re.compile(
    r"-\s*(?P<family>\S+)\s+(?P<width>[\d.]+)\"\s*car door panels:\s*qty\s+(?P<qty>\d+)"
)
_DOOR_PACKAGE_TOTAL_OPENINGS_RE = re.compile(r"total openings\s*=\s*(\d+)", re.IGNORECASE)


def _check_door_package_completeness(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A deterministic completeness check, not an auto-fixer: extract.py now computes the exact
    expected door-operator/panel breakdown in code (see compute_door_package_breakdown) and renders
    it into candidate_text as an authoritative table. A real test run still got AGGREGATE totals
    right (8 clutches, 8 detectors, 8 restrictors) while completely omitting the entire center-
    opening door-operator AND panel group for two of five cars — a silent, high-value omission that
    the model's own notes never flagged. Rather than have code invent a missing line item with a
    guessed product_code/price (real risk of a wrong SKU), this only ADDS a prominent, specific
    assumption naming exactly which expected group is missing or short, so a human reviewer can
    never miss it and the model's own future context could self-correct against it if re-run."""
    section_m = re.search(
        r"## COMPUTED door package breakdown.*?(?=\n## |\Z)", candidate_text or "", re.DOTALL
    )
    if not section_m:
        return quote
    section = section_m.group(0)
    expected_operator_groups = {
        (m.group("family").upper(), m.group("width")): int(m.group("qty"))
        for m in _DOOR_PACKAGE_OPENING_GROUP_RE.finditer(section)
    }
    expected_panel_groups = {
        (m.group("family").upper(), m.group("width")): int(m.group("qty"))
        for m in _DOOR_PACKAGE_PANEL_GROUP_RE.finditer(section)
    }
    if not expected_operator_groups:
        return quote

    line_items = quote.get("line_items") or []

    def _group_key(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        # Real product codes concatenate family+width with no separator (e.g. "1S42", "2C42" inside
        # "SGV2_DOOR_OP_1S42_LH"), so a \b-anchored family/width match would never fire — "1","S","4"
        # are all \w characters with no boundary between them. Width is instead just the first 2-3
        # digit run in the text (reliable here since these codes/descriptions never contain any
        # other 2+ digit number before the width, e.g. "SGV2" only has a single digit); family is
        # matched with an alnum-boundary lookaround instead of \b, since \b treats "_" as non-
        # boundary too.
        text = f"{item.get('product_code') or ''} {item.get('description') or ''}".upper()
        width_m = re.search(r"\d{2,3}", text)
        if not width_m:
            return None
        width = width_m.group(0)
        if re.search(r"(?<![A-Z0-9])1S(?![A-Z])|SSSO|1SSO", text):
            return ("1S", width)
        if re.search(r"(?<![A-Z0-9])2C(?![A-Z])|2PCO", text):
            return ("2C", width)
        return None

    actual_operator_totals: Dict[Tuple[str, str], float] = {}
    for item in line_items:
        if item.get("category") != "door_operator":
            continue
        key = _group_key(item)
        if key:
            actual_operator_totals[key] = actual_operator_totals.get(key, 0) + (item.get("qty") or 0)

    actual_panel_totals: Dict[Tuple[str, str], float] = {}
    for item in line_items:
        if item.get("category") != "car_door_panel":
            continue
        key = _group_key(item)
        if key:
            actual_panel_totals[key] = actual_panel_totals.get(key, 0) + (item.get("qty") or 0)

    missing_notes: List[str] = []
    for (family, width), expected_qty in expected_operator_groups.items():
        actual_qty = actual_operator_totals.get((family, width), 0)
        if actual_qty != expected_qty:
            missing_notes.append(
                f"⚠ AUTO-CHECK: the computed door package breakdown expects {expected_qty}x "
                f"{family} {width}\" door_operator (and matching clutch/door_protective_device/"
                f"restrictor) line(s), but this quote only has {actual_qty}. A real test run "
                "silently dropped an entire door-type group this way — please verify this group "
                "wasn't missed before releasing this quote."
            )
    for (family, width), expected_qty in expected_panel_groups.items():
        actual_qty = actual_panel_totals.get((family, width), 0)
        if actual_qty != expected_qty:
            missing_notes.append(
                f"⚠ AUTO-CHECK: the computed door package breakdown expects {expected_qty}x "
                f"{family} {width}\" car_door_panel(s), but this quote only has {actual_qty}. "
                "Please verify this group wasn't missed before releasing this quote."
            )

    total_m = _DOOR_PACKAGE_TOTAL_OPENINGS_RE.search(section)
    if total_m:
        expected_total = int(total_m.group(1))
        for category, label in (
            ("clutch", "clutch"), ("door_protective_device", "door protective device/detector"),
        ):
            actual_total = sum(
                (item.get("qty") or 0) for item in line_items if item.get("category") == category
            )
            if actual_total != expected_total:
                missing_notes.append(
                    f"⚠ AUTO-CHECK: the computed door package breakdown expects {expected_total} "
                    f"total openings, so {label} quantity should sum to {expected_total}, but this "
                    f"quote's {label} line(s) sum to {actual_total}. Please verify before releasing "
                    "this quote."
                )
        # The door restrictor is its own line (category "other", $0 bundled) rather than its own
        # enum category, so it can't be matched by category name alone — a real test run correctly
        # got clutch/detector/panels/operators all complete and STILL dropped the restrictor line
        # entirely (its own notes flagged the omission, but only as one line buried among many —
        # easy to miss). Matched by keyword within the "other" category specifically, so this never
        # fires against an unrelated "other" line.
        actual_restrictor_total = sum(
            (item.get("qty") or 0)
            for item in line_items
            if item.get("category") == "other"
            and "restrict" in f"{item.get('product_code') or ''} {item.get('description') or ''}".lower()
        )
        if actual_restrictor_total != expected_total:
            missing_notes.append(
                f"⚠ AUTO-CHECK: the computed door package breakdown expects {expected_total} total "
                f"openings, so the car door restrictor quantity should sum to {expected_total}, but "
                f"this quote's restrictor line(s) sum to {actual_restrictor_total} (or the line is "
                "missing entirely). Please verify before releasing this quote."
            )

    if not missing_notes:
        return quote

    quote = dict(quote)
    quote["assumptions"] = list(quote.get("assumptions") or []) + missing_notes
    return quote


_DOOR_PACKAGE_PER_CAR_LINE_RE = re.compile(r"^-\s*Car\s+\S+:", re.MULTILINE)

_GOVERNOR_SCOPE_ROW_KEYWORDS = ("governor",)


def _scope_table_confirms_new(candidate_text: str, keywords: Tuple[str, ...]) -> bool:
    """True only when candidate_text has a real Equipment scope table AND that table has a row
    matching one of `keywords` whose status explicitly starts with "New" — i.e. a positive,
    code-verifiable confirmation that this category is in scope, not merely "the table didn't say
    Refurbish." Deliberately the mirror image of _enforce_scope_table's exclusion checks above:
    that function only ever REMOVES a line using scope-table evidence; this one is used to decide
    whether it's safe to ADD one (see _enforce_governor_presence below), so it demands the stronger,
    positive signal rather than just an absence of a negative one."""
    m = _SCOPE_TABLE_SECTION_RE.search(candidate_text or "")
    if not m:
        return False
    rows = _parse_scope_table_rows(m.group(1))
    for label, status in rows:
        if any(kw in label.lower() for kw in keywords) and _SCOPE_TABLE_NEW_RE.match(status):
            return True
    return False


def _enforce_governor_presence(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A deterministic backstop for a distinct, confirmed real failure: on a live-tested run of this
    exact RFQ, the model's OWN reasoning claimed "governor quantities/types are not provided in the
    excerpts" and quoted zero governor lines — despite the Equipment scope table explicitly reading
    "Governor: New" and the full engineering clause (a dedicated numbered subsection describing the
    governor assembly, rope, and remote tripping/resetting requirement) being present verbatim in
    the same candidate_text the model was given. This is not a data-availability gap (confirmed by
    direct inspection of what was actually sent to the model) — it is the model asserting missing
    information that is actually present, then using that false premise to skip an entire priced
    category rather than a mismatched detail within one. _check_governor_count below already warns
    when governor quantity doesn't match car count, including the zero-quantity case — but a warning
    alone left this exact category (the single largest line item on the real reference quote for
    this project) completely absent from the estimate the business actually works from.

    This only acts when TWO independent, code-verifiable conditions both hold: (1) the Equipment
    scope table itself positively confirms "Governor: New" (see _scope_table_confirms_new) — never
    fires on a project without such a table, or where governors are Refurbish/absent/not stated, so
    a hydraulic elevator with no governor system at all is never affected; (2) the quote's own
    line_items contain ZERO governor-category lines. When both hold, it adds ONE baseline governor
    line — qty equal to the project's total car count (from the same per-car breakdown
    _check_governor_count reads), priced from governor_prices()'s live pricelist lookup (never a
    hardcoded literal, so this can't go stale if the pricelist changes) — preferring the RC (remote
    control) variant as the standard baseline used across every real reference quote seen for this
    business, falling back to whichever single variant governor_prices() can find if RC isn't
    indexed. Always flagged `needs_engineering_review` and clearly marked as a code-inserted
    placeholder, specifically so _check_governor_variant_differentiation (further below) still gets
    a chance to flag the single-variant case if the per-car table also shows a gearless/high-speed
    outlier car needing a different governor. If governor_prices() finds nothing at all in the
    indexed pricelist, this adds nothing — inventing a product/price this function can't verify
    would be worse than leaving the existing warning-only gap visible.

    "Already has a governor line" must mean a REAL line, not a zero-qty placeholder — a confirmed
    real run had the model submit a governor-category line with qty 0 and a bare TYPE-column code
    (e.g. "OL35-RC" instead of the real WG_OL35_GOVERNOR_RC product code), presumably as its way of
    flagging "I know this is needed but can't fill it in." That satisfied the old any(category ==
    "governor") check, so this function skipped adding a real line — and then _sanitize_quote's
    zero-qty stripping rule (see its own docstring, point 1) removed that same placeholder
    afterward, leaving the FINAL quote with zero governor lines despite this backstop having run.
    Requiring qty > 0 here closes that gap: a placeholder no longer counts as "already covered."""
    if not _scope_table_confirms_new(candidate_text, _GOVERNOR_SCOPE_ROW_KEYWORDS):
        return quote
    line_items = quote.get("line_items") or []
    if any(
        item.get("category") == "governor" and (item.get("qty") or 0) > 0
        for item in line_items
    ):
        return quote

    section_m = re.search(
        r"## COMPUTED door package breakdown.*?(?=\n## |\Z)", candidate_text or "", re.DOTALL
    )
    if not section_m:
        return quote
    total_cars = len(_DOOR_PACKAGE_PER_CAR_LINE_RE.findall(section_m.group(0)))
    if total_cars < 1:
        return quote

    available = governor_prices()
    if not available:
        return quote
    chosen = next((g for g in available if g.get("variant") == "RC"), available[0])

    quote = dict(quote)
    # Drop any zero/negative-qty governor placeholder(s) now, rather than leaving them to sit
    # alongside the real added line until _sanitize_quote strips them later — avoids a confusing
    # intermediate state where the quote briefly has both a fake and a real governor line.
    line_items = [
        item
        for item in line_items
        if not (item.get("category") == "governor" and (item.get("qty") or 0) <= 0)
    ]
    new_line = {
        "product_code": chosen["code"],
        "description": f"OL{chosen['size']} governor ({chosen['variant']}) — code-inserted baseline placeholder",
        "category": "governor",
        "qty": total_cars,
        "unit_price": chosen["price"],
        "note": (
            f"⚠ AUTO-ADDED: the Equipment scope table confirms \"Governor: New\", but this quote's "
            f"own line_items had zero governor lines — the model's reasoning claimed governor "
            f"details were missing from the provided text, which is incorrect (the full governor "
            f"clause is present). Inserted a baseline {chosen['code']} line at qty {total_cars} "
            f"(one per car, current indexed price) so this category isn't silently missing from the "
            f"estimate. Confirm the correct governor variant per car (some projects need more than "
            f"one variant — see the next check if flagged) before releasing this quote."
        ),
        "needs_engineering_review": True,
    }
    quote["line_items"] = list(line_items) + [new_line]
    return quote


_DOOR_PACKAGE_SECTION_RE = re.compile(
    r"## COMPUTED door package breakdown.*?(?=\n## |\Z)", re.DOTALL
)
_DOOR_PACKAGE_CAR_DETAIL_RE = re.compile(
    r"^-\s*Car\s+(\S+?):.*?—\s*(front \+ rear|front only)\s*=\s*(\d+)\s*opening",
    re.MULTILINE | re.IGNORECASE,
)


def _format_car_list(designations: List[str]) -> str:
    """'1, 2, 3' -> 'Cars 1, 2, and 3'; a single value -> 'Car 3' — matches the plain, factual
    phrasing style of this business's own real quote remarks (e.g. EST-261132: 'Cars 1, 2, and 3
    each require separate front and rear door-operator sets.')."""
    ds = [d.strip() for d in designations if d and d.strip()]
    if not ds:
        return ""
    if len(ds) == 1:
        return f"Car {ds[0]}"
    if len(ds) == 2:
        return f"Cars {ds[0]} and {ds[1]}"
    return f"Cars {', '.join(ds[:-1])}, and {ds[-1]}"


def _compose_remarks(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A confirmed real client review (comparing an early agent quote against this business's own
    accepted reference quote for this exact project) called out the customer-facing remarks
    specifically: the model's free-text remarks tended to narrate its OWN uncertainty ("car count
    was not available, so quantities were assumed to be four") instead of stating the project's
    actual known facts — precisely what a customer-facing remarks section should never do. The real
    reference quote's remarks instead read as a short, plain list of known facts: which project,
    how many elevators/door-operator sets, which cars have front+rear vs front-only openings, that a
    survey is needed to confirm door details before order release, and how many governors of which
    kind go where.

    Rather than continuing to hope prose instructions get a nano-tier model to consistently write
    remarks in that exact register, this composes them directly from the same code-computed,
    authoritative per-car data already used to build and auto-correct the line items themselves (the
    "## COMPUTED door package breakdown" / "## COMPUTED governor breakdown" sections) — guaranteeing
    the same factual, non-speculative format every time, in the same voice as the real reference
    quote's remarks (confirmed phrase-for-phrase against EST-261132's actual remarks text).

    Only overrides `remarks` when the door package breakdown section is present and parses cleanly
    (i.e. extract.py's per-car table parse succeeded) — a differently-formatted RFQ without that
    table leaves the model's own remarks untouched rather than emitting an empty/wrong template."""
    door_section_m = _DOOR_PACKAGE_SECTION_RE.search(candidate_text or "")
    if not door_section_m:
        return quote
    car_details = _DOOR_PACKAGE_CAR_DETAIL_RE.findall(door_section_m.group(0))
    if not car_details:
        return quote

    front_rear_cars = [d for d, kind, _ in car_details if kind.lower() == "front + rear"]
    front_only_cars = [d for d, kind, _ in car_details if kind.lower() == "front only"]
    total_cars = len(car_details)
    total_openings = sum(int(n) for _, _, n in car_details)

    project_name = (quote.get("project_name") or "").strip() or "This project"
    elevator_word = "elevator" if total_cars == 1 else "elevators"
    set_word = "door-operator set" if total_openings == 1 else "door-operator sets"

    lines = [f"{project_name} modernization: {total_cars} {elevator_word} with {total_openings} {set_word}."]
    if front_rear_cars:
        verb = (
            "requires a separate front and rear door-operator set"
            if len(front_rear_cars) == 1
            else "each require separate front and rear door-operator sets"
        )
        lines.append(f"{_format_car_list(front_rear_cars)} {verb}.")
    if front_only_cars:
        verb = (
            "requires a front door-operator set only"
            if len(front_only_cars) == 1
            else "require front door-operator sets only"
        )
        lines.append(f"{_format_car_list(front_only_cars)} {verb}.")
    lines.append(
        "Wittur survey to be completed to confirm door size, configuration, and hand of all front "
        "and rear openings before order release."
    )

    gov_section_m = _GOVERNOR_BREAKDOWN_SECTION_RE.search(candidate_text or "")
    if gov_section_m:
        gov_section = gov_section_m.group(0)
        std_m = _GOVERNOR_BREAKDOWN_STANDARD_CARS_RE.search(gov_section)
        gearless_m = _GOVERNOR_BREAKDOWN_GEARLESS_CARS_RE.search(gov_section)
        std_designations = (
            [d.strip() for d in std_m.group(1).split(",") if d.strip() and d.strip().lower() != "none"]
            if std_m
            else []
        )
        gearless_designations = (
            [d.strip() for d in gearless_m.group(1).split(",") if d.strip() and d.strip().lower() != "none"]
            if gearless_m
            else []
        )
        if std_designations:
            n = len(std_designations)
            lines.append(
                f"{n} OL35 governor{'s are' if n != 1 else ' is'} included for "
                f"{_format_car_list(std_designations)}."
            )
        if gearless_designations:
            n = len(gearless_designations)
            lines.append(
                f"{n} OL100 (higher-capacity) governor{'s are' if n != 1 else ' is'} included for "
                f"{_format_car_list(gearless_designations)} (gearless/higher contract speed)."
            )

    quote = dict(quote)
    quote["remarks"] = "\n".join(lines)
    return quote


_GOVERNOR_BREAKDOWN_SECTION_RE = re.compile(
    r"## COMPUTED governor breakdown.*?(?=\n## |\Z)", re.DOTALL
)
_GOVERNOR_BREAKDOWN_STANDARD_RE = re.compile(
    r"Total cars needing STANDARD governor:\s*(\d+)", re.IGNORECASE
)
_GOVERNOR_BREAKDOWN_GEARLESS_RE = re.compile(
    r"Total cars needing GEARLESS/high-capacity governor:\s*(\d+)", re.IGNORECASE
)
_GOVERNOR_BREAKDOWN_STANDARD_CARS_RE = re.compile(
    r"Total cars needing STANDARD governor:\s*\d+\s*\(cars\s*([^)]*)\)", re.IGNORECASE
)
_GOVERNOR_BREAKDOWN_GEARLESS_CARS_RE = re.compile(
    r"Total cars needing GEARLESS/high-capacity governor:\s*\d+\s*\(cars\s*([^)]*)\)", re.IGNORECASE
)


def _autocorrect_governor_split(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """Governor quantity/variant has been, by a wide margin, the single most persistent failure
    category across every real repeated test run on this exact RFQ — across six separate live runs
    the model has submitted 0, 2, 3, or 5-but-all-one-variant governor lines, despite every run
    being given the same per-car Drive Method data. _check_governor_count and
    _check_governor_variant_differentiation (below) kept correctly flagging the mismatch after the
    fact, but a warning never changed the actual number reaching the customer-facing total — the
    same "prose alone doesn't fix it" pattern already seen once before with the SSSO/1S entrance-
    type mistake, which got escalated from a flag to an outright auto-correct for the same reason.
    This does the same escalation for governors, now that extract.py's compute_governor_breakdown
    can derive the correct split straight from each car's own Drive Method field (code-verifiable,
    same trust bar as the door package breakdown) rather than trusting model reasoning about it.

    Only acts when the "## COMPUTED governor breakdown" section is present in candidate_text (i.e.
    extract.py's equipment-inventory-table parse succeeded — a differently-formatted spec without
    that table leaves this a no-op, falling back to the existing warning-only checks unchanged) AND
    a real alternate/gearless-rated governor product can be found via governor_prices() whenever the
    breakdown says at least one car needs one (never invents a product/price it can't verify — if no
    such variant is indexed, this steps aside and lets _check_governor_variant_differentiation's
    warning surface the gap instead of forcing an incomplete split).

    Replaces the ENTIRE governor line-item set with the computed-correct one — not just a missing
    piece — since the failure modes seen include wrong quantities on lines that already exist, not
    only lines being absent. Idempotent: if the model's own governor lines already exactly match the
    computed split (same codes and quantities), this leaves them untouched instead of needlessly
    discarding a correct answer (and whatever legitimate note the model attached to it)."""
    section_m = _GOVERNOR_BREAKDOWN_SECTION_RE.search(candidate_text or "")
    if not section_m:
        return quote
    section_text = section_m.group(0)
    standard_m = _GOVERNOR_BREAKDOWN_STANDARD_RE.search(section_text)
    gearless_m = _GOVERNOR_BREAKDOWN_GEARLESS_RE.search(section_text)
    if not standard_m or not gearless_m:
        return quote
    n_standard = int(standard_m.group(1))
    n_gearless = int(gearless_m.group(1))
    if n_standard + n_gearless < 1:
        return quote

    available = governor_prices()
    if not available:
        return quote
    standard_choice = next((g for g in available if g.get("variant") == "RC"), available[0])
    gearless_choice = None
    if n_gearless > 0:
        gearless_choice = next(
            (g for g in available if g.get("size") != standard_choice.get("size")), None
        )
        if gearless_choice is None:
            # No alternate-size variant indexed to represent the gearless car(s) — forcing a split
            # here would either invent a product or silently under-quote it. Leave this to
            # _check_governor_variant_differentiation's warning instead of a wrong/partial fix.
            return quote

    line_items = quote.get("line_items") or []
    non_governor_items = [it for it in line_items if it.get("category") != "governor"]
    existing_governor_items = [it for it in line_items if it.get("category") == "governor"]

    new_governor_items: List[Dict[str, Any]] = []
    if n_standard > 0:
        new_governor_items.append(
            {
                "product_code": standard_choice["code"],
                "description": f"OL{standard_choice['size']} governor ({standard_choice['variant']})",
                "category": "governor",
                "qty": n_standard,
                "unit_price": standard_choice["price"],
                "needs_engineering_review": True,
                "note": (
                    "⚠ AUTO-CORRECTED: quantity/variant computed directly from each car's own "
                    f"Drive Method field — {n_standard} car(s) on a standard (non-gearless) drive "
                    "method need this variant. See the paired governor line for the gearless-"
                    "rated car(s), if any. Confirm before releasing this quote."
                ),
            }
        )
    if n_gearless > 0:
        new_governor_items.append(
            {
                "product_code": gearless_choice["code"],
                "description": f"OL{gearless_choice['size']} governor ({gearless_choice['variant']}) — temporary business-provided price pending an official pricelist update",
                "category": "governor",
                "qty": n_gearless,
                "unit_price": gearless_choice["price"],
                "needs_engineering_review": True,
                "note": (
                    "⚠ AUTO-CORRECTED: quantity/variant computed directly from each car's own "
                    f"Drive Method field — {n_gearless} car(s) run gearless/at a distinctly higher "
                    "contract speed and need this higher-capacity governor instead of the standard "
                    "variant used for the rest of the project. Confirm before releasing this quote."
                ),
            }
        )

    def _sig(items: List[Dict[str, Any]]) -> List[Tuple[Any, float]]:
        return sorted((it.get("product_code"), float(it.get("qty") or 0)) for it in items)

    if _sig(existing_governor_items) == _sig(new_governor_items):
        return quote

    quote = dict(quote)
    quote["line_items"] = non_governor_items + new_governor_items
    quote["assumptions"] = list(quote.get("assumptions") or []) + [
        f"⚠ AUTO-CORRECTED: governor line(s) were replaced with the code-computed split derived "
        f"directly from each car's own Drive Method field ({n_standard} standard + {n_gearless} "
        "gearless/high-capacity) — this overrides whatever governor line(s) the model itself "
        "submitted, since this category has repeatedly been miscounted or left undifferentiated "
        "across real test runs. The per-car Drive Method table is the authoritative source here, "
        "not model reasoning about it."
    ]
    return quote


def _check_governor_count(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """Governors (and car safeties, where in scope) are one per CAR, not per opening — the total car
    count is available for free from the same computed door package breakdown's per-car line list
    (one "- Car N: ..." line per car), so this checks the model's own cross-check arithmetic rather
    than trusting it. A real test run submitted 3 governors for a 5-car project (should have been 4
    of one variant + 1 of a different variant for the one car with a different drive method) — this
    flags any such mismatch instead of letting it ship unnoticed. Doesn't try to guess the missing
    car's correct governor variant/product_code itself — that depends on drive method/speed details
    this check doesn't parse — it only guarantees the shortfall is visible."""
    section_m = re.search(
        r"## COMPUTED door package breakdown.*?(?=\n## |\Z)", candidate_text or "", re.DOTALL
    )
    if not section_m:
        return quote
    total_cars = len(_DOOR_PACKAGE_PER_CAR_LINE_RE.findall(section_m.group(0)))
    if total_cars < 2:
        return quote

    line_items = quote.get("line_items") or []
    total_governor_qty = sum(
        (item.get("qty") or 0) for item in line_items if item.get("category") == "governor"
    )
    if total_governor_qty == total_cars:
        return quote

    quote = dict(quote)
    quote["assumptions"] = list(quote.get("assumptions") or []) + [
        f"⚠ AUTO-CHECK: this project has {total_cars} cars/elevators (per the per-car table), so "
        f"total governor quantity across all governor line(s) should sum to {total_cars}, but this "
        f"quote's governor line(s) sum to {total_governor_qty}. If one car needs a different "
        "governor variant than the rest (e.g. a gearless/higher-speed car), it needs its OWN "
        "governor line at its own variant, not to be dropped — a real test run undercounted this "
        "exact way. Please verify before releasing this quote."
    ]
    return quote


# Signals a car's drive method/speed is genuinely different from the rest of the project's cars —
# confirmed against a real spec's per-car "Drive Method"/"Contract Speed" table (e.g. "OH traction
# (geared)" at 500 fpm for most cars, "OH traction (gearless)" at 700 fpm for one outlier car).
# Deliberately narrow: only fires when BOTH a geared and a gearless mention appear in the same
# candidate text — i.e. a genuine MIX across cars, not just "this project happens to be all-geared"
# or "all-gearless" (uniform drive method across every car needs no governor differentiation at
# all, and flagging that case would just be noise).
_GEARLESS_RE = re.compile(r"\bgearless\b", re.IGNORECASE)
_GEARED_RE = re.compile(r"\b(?<!un)geared\b", re.IGNORECASE)


def _check_governor_variant_differentiation(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """A deterministic backstop for a distinct failure from _check_governor_count above: repeated
    live testing (against a real, confirmed ground-truth quote for this exact project) showed the
    model correctly finding all 5 governors' worth of scope, and even occasionally landing on the
    right TOTAL quantity, while still merging every one of them into a SINGLE governor product_code
    — even though the per-car table it was given plainly shows one car on a different drive method
    (gearless, higher contract speed) than the rest. The real quote for this project splits the
    governors into two variants for exactly this reason (a standard remote-control governor for the
    geared cars, a different/higher-capacity governor for the one gearless, higher-speed car) — a
    ~$13,000 line that a single-code governor quote misses entirely. _check_governor_count alone
    can't catch this: it only checks that quantities SUM to the car count, which this failure mode
    can satisfy by coincidence while still being structurally wrong (one variant instead of two).
    This check is deliberately a WARNING, not an auto-fix or auto-add: it doesn't know, from the
    text it has access to, HOW MANY cars actually need the alternate/higher-capacity governor (that
    depends on a per-car drive-method assignment this check doesn't parse), so it can't safely split
    quantities between the two variants on its own — doing that with a guessed count would be worse
    than leaving the split to a human. As of Sep 2026 a gearless/high-speed-rated governor (OL100-RE,
    a temporary business-provided price pending an official pricelist update — see the pricelist
    reindex) IS available in the indexed pricelist, so this message names the exact product/price to
    use instead of claiming none exists; before that addition, this same check used to say no such
    product could be found at all — keep that in mind if the pricelist reindex is ever reverted."""
    has_gearless = bool(_GEARLESS_RE.search(candidate_text or ""))
    has_geared = bool(_GEARED_RE.search(candidate_text or ""))
    if not (has_gearless and has_geared):
        return quote

    line_items = quote.get("line_items") or []
    governor_codes = {
        item.get("product_code") for item in line_items if item.get("category") == "governor"
    }
    if len(governor_codes) != 1:
        # Zero governor lines is _check_governor_count's concern (is a governor missing at all?),
        # not this check's; two or more distinct codes means differentiation already happened
        # (whether or not the specific codes/qtys are exactly right) — nothing to flag either way.
        return quote

    existing_code = next(iter(governor_codes)) or ""
    existing_size_m = re.match(r"^WG_OL(\d+)_", existing_code, re.IGNORECASE)
    existing_size = existing_size_m.group(1) if existing_size_m else None
    alt = next((g for g in governor_prices() if g.get("size") != existing_size), None)

    quote = dict(quote)
    if alt:
        message = (
            "⚠ AUTO-CHECK: this project's per-car table shows a MIX of geared and gearless drive "
            "methods (and/or a car at a distinctly different contract speed) across its cars, but "
            f"every governor line in this quote uses the same product_code ({existing_code}). A "
            "real, confirmed quote for a project shaped exactly like this one splits its governors "
            "into two different variants — the gearless/higher-speed car needs a different (and "
            "notably more expensive) governor than the geared cars, not the same catalog item "
            f"repeated. A matching higher-capacity governor is available: {alt['code']} at "
            f"${alt['price']:,.2f} (temporary business-provided price pending an official pricelist "
            "update). Please confirm how many cars actually need it per the drive-method mix in the "
            f"spec, add that many lines at {alt['code']}, and reduce the {existing_code} quantity by "
            "the same amount before releasing this quote."
        )
    else:
        message = (
            "⚠ AUTO-CHECK: this project's per-car table shows a MIX of geared and gearless drive "
            "methods (and/or a car at a distinctly different contract speed) across its cars, but "
            f"every governor line in this quote uses the same product_code ({existing_code}). A "
            "real, confirmed quote for a project shaped exactly like this one splits its governors "
            "into two different variants — the gearless/higher-speed car needs a different (and "
            "notably more expensive) governor than the geared cars, not the same catalog item "
            "repeated. As of this check, no gearless/high-speed-rated governor product could be "
            "found in the indexed pricelist at all — this may mean the pricelist itself is missing "
            "that catalog item and needs a manual addition before this can be quoted correctly. "
            "Please verify the drive-method split per car and confirm the correct governor "
            "variant/pricing for the outlier car(s) before releasing this quote."
        )
    quote["assumptions"] = list(quote.get("assumptions") or []) + [message]
    return quote


_HAND_SUFFIX_RE = re.compile(r"_(LH|RH)$", re.IGNORECASE)

_RECONCILE_CATEGORIES = {
    "door_operator", "clutch", "panel_adaptor", "governor", "roller_guide", "door_tools",
    "door_protective_device",
}


def _lookup_product_master(code: str) -> Optional[Dict[str, Any]]:
    """Exact-code lookup into FTL's Stage 2 Product Master, with a couple of narrow hand-suffix
    fallbacks — the workbook itself isn't fully consistent about whether a governor's hand lives
    on the code or not (WG_OL100_GOVERNOR_RE_LH/RH covers both hands in one row; other governor
    rows carry no hand at all; a model or an older pricelist convention might submit either a bare
    code or one with _LH/_RH appended). Never does a fuzzy/semantic match — an unmatched code is
    left for the caller to leave alone rather than risk a wrong association."""
    if not code:
        return None
    row = rules_engine.product_master_row(code)
    if row:
        return row
    row = rules_engine.product_master_row(code + "/RH")
    if row:
        return row
    stripped = _HAND_SUFFIX_RE.sub("", code)
    if stripped != code:
        row = rules_engine.product_master_row(stripped) or rules_engine.product_master_row(stripped + "_LH/RH")
        if row:
            return row
    return None


def _reconcile_against_product_master(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-checks every line item against FTL's Stage 2 Product Master (96 SKUs, versioned in
    data/rulebook/product_master.json) — the authoritative catalogue the rulebook shipped
    specifically so this estimator stops relying solely on semantic search over an embedded
    pricelist PDF, which can miss a price update, a brand-new SKU (the OL100 line, the canonical
    detector code, the OL100 tension assembly — none of which existed in the old pricelist at
    all), or return a near-match instead of the exact row. Compared directly against the current
    pricelist_index.json: no dollar drift was found for any family the old pricelist_store.py
    helpers already parse cleanly (door operators, OL35 governors, universal door panels, WRG,
    clutches) — this function exists for correctness going forward (the next time FTL updates
    either source and they drift) and for the SKUs that only exist in Product Master today.

    Deliberately conservative: only acts on a CONFIDENT exact-code match (see
    _lookup_product_master) and only corrects `unit_price` — never product_code, category, or
    quantity, and never invents a match for a code it doesn't recognize. car_door_panel is
    excluded on purpose: that family's per-panel-vs-set halving is already handled correctly by
    _autocorrect_panel_prices below using the pricelist's own set-price rows, and re-deriving it
    from Product Master's raw (un-halved) set price here risks reintroducing the exact doubled-
    price bug that function was built to fix. car_safety is excluded because it's removed from
    the quote entirely by _enforce_safety_gear_block before this ever sees it."""
    line_items = quote.get("line_items") or []
    changed = False
    for item in line_items:
        if item.get("category") not in _RECONCILE_CATEGORIES:
            continue
        row = _lookup_product_master(str(item.get("product_code") or ""))
        if not row:
            continue
        pm_price = row.get("price_cad")
        if pm_price is None:
            continue
        try:
            current_price = float(item.get("unit_price") or 0)
        except (TypeError, ValueError):
            current_price = 0.0
        if abs(current_price - float(pm_price)) > 0.01:
            old_price = current_price
            item["unit_price"] = float(pm_price)
            item["needs_engineering_review"] = True
            note = (
                f"⚠ AUTO-CORRECTED — unit_price ${old_price:,.2f} didn't match FTL's Stage 2 "
                f"Product Master price for {row.get('ftl_product_code')} "
                f"(${float(pm_price):,.2f}); corrected to the Product Master figure."
            )
            item["note"] = (item.get("note") + " " if item.get("note") else "") + note
            changed = True
    quote = dict(quote)
    if changed:
        quote["line_items"] = line_items
    return quote


def _enforce_safety_gear_block(quote: Dict[str, Any]) -> Dict[str, Any]:
    """RT-014 / I-008 / I-016 (FTL Stage 2 rulebook, received Sept 2026): safety gear must NEVER be
    auto-priced. The product master's own CSGB03-SYNC row is marked HOLD, "Never auto-add;
    engineering selection required" — the pricelist's one generic SYNC SKU can't yet be reconciled
    against the manufacturer's real SGB01/02/03/05 Fmax/rail-condition selection table (I-008), and
    FTL's own rail-only selection heuristic is itself flagged as wrong (I-016: Fmax, speed, rail
    condition AND rail compatibility govern selection — rail size alone is insufficient). Rulebook
    regression test T-007 ("Safety request") expects exactly this: "No priced safety until completed
    survey and CSGB/SGB mapping approval."

    This overrides anything upstream — the model's own reasoning, or this skill's older guidance
    about a default SYNC/duplex safety variant — unconditionally. Any car_safety line, however it
    was produced, is removed from the priced estimate and replaced with one clear, citation-backed
    note routing to safety engineering. Treated as a hard BLOCK rather than a REVIEW-and-keep like
    most other checks in this file: a wrong safety-gear line is a life-safety component, not just a
    pricing risk, so nothing here even guesses at qty or product_code."""
    line_items = quote.get("line_items") or []
    safety_items = [it for it in line_items if it.get("category") == "car_safety"]
    if not safety_items:
        return quote
    kept = [it for it in line_items if it.get("category") != "car_safety"]
    quote = dict(quote)
    quote["line_items"] = kept
    descs = "; ".join(
        f"{it.get('description') or it.get('product_code')} (qty {it.get('qty')})" for it in safety_items
    )
    quote["assumptions"] = list(quote.get("assumptions") or []) + [
        f"\U0001F6D1 BLOCK — removed from the estimate, never auto-priced: {descs}. "
        + rules_engine.cite_trigger("RT-014")
        + " "
        + rules_engine.cite_conflict("I-008")
        + " "
        + rules_engine.cite_conflict("I-016")
    ]
    return quote


_WRG_HT_RAIL_RE = re.compile(r"roll[- ]?formed\s+HT\s+rail|lubricated\s+rail", re.IGNORECASE)
_WRG_NONPASSENGER_RE = re.compile(r"non[- ]?passenger|freight\s+elevator|unbalanced\s+car", re.IGNORECASE)
_WRG_CWT_DESC_RE = re.compile(r"counterweight|\bCWT\b", re.IGNORECASE)


def _enforce_wrg_gating(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    """RT-011 / RT-012 / RT-013 and Training Example T-006 (WRG roller guides) — three independent,
    code-verifiable guards, each mapping to one review trigger from the Stage 2 rulebook:

    1. RT-013 — reject entirely on a roll-formed HT rail, a lubricated rail, or a non-passenger/
       unbalanced-car application; WRG's standard range is invalid there per its own technical
       catalogue. Strips any roller_guide line(s) rather than pricing something the manufacturer's
       own range doesn't cover.
    2. RT-012 — WRG150HD has no FTL-approved price (product master status HOLD, no price on file)
       — flags it rather than letting a guessed price through.
    3. T-006 ("never add both automatically") — car AND counterweight guide sets are only ever both
       in scope when the spec explicitly calls for both. This doesn't try to re-derive scope (the
       scope-table check elsewhere in this file already does that); it just makes it impossible to
       miss when both a car-guide-shaped and a CWT-guide-shaped line land on the same estimate."""
    line_items = quote.get("line_items") or []
    roller_items = [it for it in line_items if it.get("category") == "roller_guide"]
    if not roller_items:
        return quote
    quote = dict(quote)
    assumptions = list(quote.get("assumptions") or [])

    if _WRG_HT_RAIL_RE.search(candidate_text or "") or _WRG_NONPASSENGER_RE.search(candidate_text or ""):
        quote["line_items"] = [it for it in line_items if it.get("category") != "roller_guide"]
        assumptions.append(
            "\U0001F6D1 BLOCK — removed roller_guide line(s): the spec text mentions a roll-formed "
            "HT rail, a lubricated rail, or a non-passenger/unbalanced-car application, and WRG's "
            "standard range is invalid for those per its own technical catalogue. "
            + rules_engine.cite_trigger("RT-013")
        )
        quote["assumptions"] = assumptions
        return quote

    for it in roller_items:
        code = str(it.get("product_code") or "").upper()
        if "150HD" in code:
            it["needs_engineering_review"] = True
            row = rules_engine.wrg_row("WRG150HD")
            note = "⚠ " + rules_engine.cite_trigger("RT-012")
            if row:
                note += (
                    f" (product master status {row.get('automation_status')}, no approved price on "
                    "file — do not trust any price this line currently shows)"
                )
            it["note"] = (it.get("note") + " " if it.get("note") else "") + note

    has_car = any(not _WRG_CWT_DESC_RE.search(str(it.get("description") or "")) for it in roller_items)
    has_cwt = any(_WRG_CWT_DESC_RE.search(str(it.get("description") or "")) for it in roller_items)
    if has_car and has_cwt:
        assumptions.append(
            "⚠ REVIEW — this estimate has both a car-guide-shaped and a counterweight-guide-"
            "shaped roller_guide line. Confirm the spec explicitly calls for both car AND CWT guide "
            "replacement — WRG guides must never be added to both automatically (Training Example "
            "T-006)."
        )

    quote["line_items"] = line_items
    quote["assumptions"] = assumptions
    return quote


_GOVERNOR_SIZE_RE = re.compile(r"OL(35|100)", re.IGNORECASE)


def _enforce_governor_tension_companions(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Mandatory REQUIRED COMPANION rule (product master automation_status, sourced from the Aug 29
    2026 JR/Francine controlling instruction, source S-029 — "highest-priority commercial
    instruction in this release"): every OL governor, OL35 or OL100, requires exactly one pit
    tension/swingarm assembly alongside it — 1:1 per governor unit, never optional, even when an
    older survey form offers a "reuse existing / no tension" choice (I-014: "latest FTL rule
    controls quoting"). OL100 specifically requires the NEW $2,950 tension assembly line
    (TBD_OL100_TENSION_ASSEMBLY) that didn't exist before this rulebook — Telegram Mews' own
    approved EST-261132 total is explicitly now in question because of this exact rule (I-017:
    "confirm whether $13,200 already included the tension assembly" — do not treat that old total
    as regression truth until this is resolved).

    This is additive/corrective, not a full governor-selection rewrite: _autocorrect_governor_split
    / _enforce_governor_presence (above) already decide HOW MANY governors of which size are
    needed; this function only makes sure each one has its mandatory companion, adding a companion
    line if missing or correcting its quantity if it doesn't match 1:1. Also attaches the RT-010/
    I-006/I-007 citation whenever an OL100 governor is present — "no final SKU/BOM" and unhanded
    price-list codes are still open, so an OL100 line is never presented as fully resolved just
    because a price number exists for it."""
    line_items = quote.get("line_items") or []
    gov_qty_by_size: Dict[str, float] = {}
    for it in line_items:
        if it.get("category") != "governor":
            continue
        m = _GOVERNOR_SIZE_RE.search(str(it.get("product_code") or ""))
        if not m:
            continue
        try:
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        size = m.group(1)
        gov_qty_by_size[size] = gov_qty_by_size.get(size, 0) + qty
    if not gov_qty_by_size:
        return quote

    quote = dict(quote)
    assumptions = list(quote.get("assumptions") or [])
    working_items = list(line_items)
    changed = False

    for size, expected_qty in gov_qty_by_size.items():
        if expected_qty <= 0:
            continue
        companion_row = rules_engine.governor_tension_companion(size)
        if not companion_row:
            assumptions.append(
                f"⚠ REVIEW — {expected_qty:g}x OL{size} governor(s) on this estimate require a "
                "mandatory pit tension/swingarm companion line per the Aug 2026 FTL rule, but no "
                "matching product-master entry could be found to price it from. Add it manually "
                "before release."
            )
            continue
        companion_code = companion_row["ftl_product_code"]
        existing = [
            it for it in working_items
            if it.get("category") == "governor" and it.get("product_code") == companion_code
        ]
        existing_qty = sum(float(it.get("qty") or 0) for it in existing)
        if existing and existing_qty == expected_qty:
            continue
        working_items = [
            it for it in working_items
            if not (it.get("category") == "governor" and it.get("product_code") == companion_code)
        ]
        working_items.append(
            {
                "product_code": companion_code,
                "description": companion_row.get("description") or companion_code,
                "category": "governor",
                "qty": expected_qty,
                "unit_price": companion_row.get("price_cad") or 0,
                "needs_engineering_review": True,
                "note": (
                    f"⚠ AUTO-ADDED/CORRECTED — mandatory REQUIRED COMPANION per the FTL "
                    f"governor tension rule: every OL{size} governor needs exactly one of these "
                    f"(qty set to {expected_qty:g} to match the OL{size} governor qty on this "
                    "estimate)."
                    + (f" [{companion_row.get('notes_issue')}]" if companion_row.get("notes_issue") else "")
                ),
            }
        )
        changed = True
        if size == "100":
            assumptions.append(
                "⚠ REVIEW — this estimate includes an OL100 governor. "
                + rules_engine.cite_trigger("RT-010")
                + " "
                + rules_engine.cite_conflict("I-006")
                + " "
                + rules_engine.cite_conflict("I-007")
            )

    quote["assumptions"] = assumptions
    if changed:
        quote["line_items"] = working_items
    return quote


_SS441_RE = re.compile(r"#\s*4\s*SS\s*441", re.IGNORECASE)


def _apply_universal_door_panel_conflict_notes(quote: Dict[str, Any]) -> Dict[str, Any]:
    """I-004 (universal door UOM/price) and I-005 (stainless finish description) — both CRITICAL/
    HIGH, OPEN conflicts on the exact same car_door_panel family this codebase already auto-
    corrects the per-panel price for (see _autocorrect_panel_prices above). That auto-correction is
    still the best available number (a real, confirmed invoice-derived split) and is NOT reverted
    here — but the rulebook is explicit that automatic pricing for this family should be held
    pending a canonical SKU/UOM/price (I-004), so every car_door_panel line gets a citation making
    that unresolved status visible rather than looking as settled as an ACTIVE door-operator line.
    Separately, I-005's safe interim rule is to use neutral "#4 brushed finish" wording instead of
    either source's specific claim ("#4 SS 441" vs "304 stainless #4 brushed") pending manufacturing
    confirmation — applied here as a plain substitution wherever the pricelist's own "#4 SS 441"
    phrasing appears in a description."""
    line_items = quote.get("line_items") or []
    panel_items = [it for it in line_items if it.get("category") == "car_door_panel"]
    if not panel_items:
        return quote
    quote = dict(quote)
    for it in panel_items:
        desc = it.get("description")
        if desc and _SS441_RE.search(desc):
            it["description"] = _SS441_RE.sub("#4 brushed finish", desc)
        it["needs_engineering_review"] = True
        note = "⚠ " + rules_engine.cite_conflict("I-004") + " " + rules_engine.cite_conflict("I-005")
        it["note"] = (it.get("note") + " " if it.get("note") else "") + note
    quote["line_items"] = line_items
    return quote


_DETECTOR_ELECTRICAL_CLAIM_RE = re.compile(r"24\s*V\s*(AC|DC)", re.IGNORECASE)
_KNOWN_DETECTOR_CODES = {"FS_VISIONPLUS_3D_CAR_DOOR_DET", "VISIONPLUS_3D_DETECTOR"}


def _normalize_detector_lines(quote: Dict[str, Any]) -> Dict[str, Any]:
    """I-002 (SKU naming: pricelist VISIONPLUS_3D_DETECTOR vs FTL-stated FS_VISIONPLUS_3D_CAR_DOOR_
    DET) and I-003 (electrical claim: FTL says both sides 24 VAC, the SUPRA manual says +24 VDC/
    150mA at the IPD) — both CRITICAL/HIGH conflicts on the same detector line every SGV2 opening
    carries. I-002's safe interim rule is to use FTL's own stated product name in the draft while
    blocking release until the SKU is normalized against inventory/accounting; I-003's is to never
    publish a specific electrical claim or final wiring BOM until the Formula datasheet resolves it
    — this strips any such claim from the line's own text rather than let an unverified voltage
    spec reach a customer-facing estimate. Also enforces a DO-NOT-AUTO-ADD product-master entry:
    the detector's power supply (VISIONPLUS_POWERSUPPLY) is "not used automatically with SGV2" and
    must never be auto-added just because a detector line exists.

    IMPORTANT — also corrects, not just cites, a wrong product_code: a real live test run (25
    Telegram Mews) submitted "FZU-1442US01 -3D_LIGHT_CURTAIN" — an OEM/manufacturer reference
    number copied out of the pricelist's DESCRIPTION column, exactly the "wrong column" failure
    the skill instructions warn about, and NOT the same string as either of FTL's own real codes.
    Any door_protective_device line whose code isn't already one of the known detector codes gets
    corrected to FTL's stated canonical code (FS_VISIONPLUS_3D_CAR_DOOR_DET, per I-002's safe
    interim rule) and re-priced from Product Master — leaving a garbled code on a customer-facing
    estimate would be worse than picking the one FTL has actually told us to use in the draft."""
    line_items = quote.get("line_items") or []
    power_row = rules_engine.detector_power_supply_row()
    power_code = (power_row or {}).get("ftl_product_code")

    stripped = [it for it in line_items if power_code and it.get("product_code") == power_code]
    if stripped:
        line_items = [it for it in line_items if it not in stripped]

    detector_items = [it for it in line_items if it.get("category") == "door_protective_device"]
    if not detector_items and not stripped:
        return quote

    quote = dict(quote)
    canonical = rules_engine.detector_row()
    for it in detector_items:
        desc = it.get("description") or ""
        if _DETECTOR_ELECTRICAL_CLAIM_RE.search(desc):
            it["description"] = _DETECTOR_ELECTRICAL_CLAIM_RE.sub(
                "[electrical interface per FTL/SUPRA datasheet — confirm before wiring release]", desc
            )
        if canonical and str(it.get("product_code") or "").upper() not in _KNOWN_DETECTOR_CODES:
            old_code = it.get("product_code")
            it["product_code"] = canonical["ftl_product_code"]
            if canonical.get("price_cad") is not None:
                it["unit_price"] = canonical["price_cad"]
            it["note"] = (
                f"⚠ AUTO-CORRECTED — product_code was {old_code!r}, which looks like it was "
                "copied from the pricelist's DESCRIPTION column rather than the real product code; "
                f"corrected to FTL's stated canonical code ({canonical['ftl_product_code']}). "
                + (it.get("note") + " " if it.get("note") else "")
            )
        it["needs_engineering_review"] = True
        note = "⚠ " + rules_engine.cite_conflict("I-002") + " " + rules_engine.cite_conflict("I-003")
        it["note"] = (it.get("note") + " " if it.get("note") else "") + note

    quote["line_items"] = line_items
    if stripped:
        assumptions = list(quote.get("assumptions") or [])
        assumptions.append(
            "ℹ removed a detector power-supply line (VISIONPLUS_POWERSUPPLY) — product master "
            "marks this DO NOT AUTO-ADD (\"not used automatically with SGV2\"); add it back "
            "manually only if this project's detector wiring genuinely needs a separate supply."
        )
        quote["assumptions"] = assumptions
    return quote


def _check_door_tools_presence(quote: Dict[str, Any]) -> Dict[str, Any]:
    """A real test run correctly included every door_operator/panel/clutch/detector line at the
    right quantities, then talked itself out of including the door-programming tool at all
    ("quantities/ownership terms... not provided, confirm whether to charge") despite an explicit
    instruction that it's chargeable by default whenever door operators are being quoted (a real,
    client-confirmed quote billed it alongside 8 door operators). Since whether ANY door_tools line
    exists is a much simpler check than the per-group quantity checks above, this catches the
    "omitted entirely" failure specifically."""
    line_items = quote.get("line_items") or []
    has_door_operator = any(item.get("category") == "door_operator" for item in line_items)
    has_door_tools = any(item.get("category") == "door_tools" for item in line_items)
    if not has_door_operator or has_door_tools:
        return quote
    quote = dict(quote)
    quote["assumptions"] = list(quote.get("assumptions") or []) + [
        "⚠ AUTO-CHECK: this quote has door_operator line(s) but no door_tools (Wittur programming "
        "tool) line at all. Per this business's own quoting rules, the door-programming tool is "
        "normally included as its own chargeable line whenever door operators are being quoted (a "
        "real, client-confirmed quote billed it alongside 8 door operators) — a real test run "
        "omitted it entirely by second-guessing itself into leaving it off rather than defaulting "
        "to including it. Please verify before releasing this quote."
    ]
    return quote


# TEMPORARY flat freight default, per explicit instruction from the business (Sep 2026): "the
# freight is going to be $975 for now, and later I will update about it." Not derived from any
# real freight calculation — a fixed placeholder applied to every quote until a real per-project
# freight rule is provided. When that happens, replace this constant (and this function) rather
# than leaving both the old flat default and a new rule active at once.
#
# FTL's own Stage 2 rulebook independently flags freight as I-009 (CRITICAL/HIGH, OPEN): "no
# formally approved freight matrix... manual freight or approved customer/project rule only," and
# specifically calls out that Telegram Mews' own $975 shouldn't be read as a general threshold. That
# is a distinct question from whether THIS business keeps using $975 as its own working default —
# Seth's Sept 2026 instruction is a real business decision, not a guess, and is not overridden here.
# What changes is that this is no longer presented as a settled figure with no caveat: every quote
# now carries an internal (not customer-facing) citation making clear this flat number isn't an
# FTL-approved freight matrix, so a reviewer always sees the open conflict rather than assuming the
# freight line is as solid as an ACTIVE, priced-from-catalogue line.
_TEMPORARY_FLAT_FREIGHT = 975.0


def _apply_temporary_flat_freight(quote: Dict[str, Any]) -> Dict[str, Any]:
    quote = dict(quote)
    quote["freight_estimate"] = _TEMPORARY_FLAT_FREIGHT
    quote["freight_note"] = (
        f"Flat freight estimate of ${_TEMPORARY_FLAT_FREIGHT:,.2f} (temporary default set by the "
        "business pending a real per-project freight rule; confirm at order)."
    )
    quote["assumptions"] = list(quote.get("assumptions") or []) + [
        "ℹ Freight was set to the business's own flat $975 interim default (Seth, Sep 2026), "
        "not a guess — but note " + rules_engine.cite_conflict("I-009")
    ]
    return quote


def _sanitize_quote(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic safety net over the model's submit_quote call — catches mistakes that prose
    instructions alone have not reliably prevented across repeated real test runs (this is a
    nano-tier model; treat prose rules as best-effort, not guaranteed, for anything with a dollar
    impact), so they're enforced here in code instead of just asked for nicely again:

    1. Zero/negative-qty "placeholder" lines — strip and fold into assumptions instead of letting
       a fake row reach the rendered quote.
    2. product_code values with no underscore — every real FTL/Wittur product code has one; a bare
       TYPE/O.E.M./DESCRIPTION-column value (ALL, OL35-RC, CSGB03-SYNC, FZU-1442US01...) doesn't.
    3. product_code that's suspiciously short/numeric (e.g. "1") — seen in a real run where the
       model couldn't find a specific pricelist match and filled in junk rather than leaving the
       line out. Every real code in this catalog is 10+ characters.
    4. unit_price missing/None/<=0 on a line that isn't a declared $0 bundle (subtotal_override==0)
       — seen in the same real run paired with the "1" code: a null price silently renders as
       $0.00 on the quote, which looks like a correctly-priced free item rather than a missing
       lookup, and can silently undercount the total by thousands of dollars.

    None of these are auto-corrected (the real value isn't knowable here) — they're flagged with
    needs_engineering_review + a visible warning note so a human catches them before the quote
    ships, since a silently wrong/missing price is worse than an ugly one."""
    line_items = quote.get("line_items") or []
    kept: List[Dict[str, Any]] = []
    dropped_notes: List[str] = []
    try:
        whitelist = known_product_codes()
    except Exception:
        whitelist = set()

    _autocorrect_panel_prices(line_items, dropped_notes)

    # Car count proxy: the door operator line's own qty is normally one-per-car, so it's the best
    # available signal for how many cars this project has. Needed below (for the SSSO/1S panel qty
    # correction) as well as by the car-safety-quantity check further down.
    door_operator_qty = 0.0
    for item in line_items:
        if item.get("category") == "door_operator":
            try:
                door_operator_qty = max(door_operator_qty, float(item.get("qty") or 0))
            except (TypeError, ValueError):
                pass

    # SSSO (Single Speed Side Opening) always maps to 1S per the skill's entrance-type table — this
    # exact override recurred FIVE times across real runs, each time with the model narrating the
    # correct SSSO->1S mapping in its own note/assumptions and then picking a 2C/2T code anyway,
    # citing some other existing-equipment field (a brand/model name like "ECI 1000" or "GAL MOVFE
    # (ECI VFE2500)") as if it were conflicting evidence. Two rounds of prose fixes and then a
    # detect-only contradiction flag all failed to change the model's actual answer — the flag kept
    # firing correctly but the wrong number kept reaching the total. Since the correct 1S code/price
    # (door operator) and 1S panel row (code/price/qty) are now confirmed parseable straight from the
    # live pricelist (see door_operator_prices()/car_door_panel_prices() in pricelist_store.py), this
    # now actually auto-corrects both lines instead of just flagging them. The flag below still fires
    # as a fallback for the (should be rare) case where a matching pricelist row isn't found — e.g. an
    # unusual width — so nothing silently reaches the total uncorrected AND unflagged.
    #
    # IMPORTANT — this check MUST be per-line, not quote-wide. An earlier version scanned the whole
    # quote's remarks/assumptions/notes for "SSSO" and, if found ANYWHERE, applied the 2C/2T->1S
    # swap to EVERY 2C/2T operator/panel line in the quote. That was fine when a quote only ever had
    # ONE entrance-type configuration project-wide, but a real, confirmed multi-car-group project
    # legitimately has SOME cars at SSSO/1S and OTHER cars at CO/2C in the very same quote (e.g. 3
    # cars 1S + 2 cars 2C) — the blanket version wrongly swapped the genuinely-correct 2C operator
    # line for the CO cars over to 1S just because a DIFFERENT line's note happened to mention SSSO,
    # silently deleting an entire real door-operator configuration from the quote. Ground the check
    # in each line's OWN note instead: only treat a 2C/2T-coded line as a wrongly-overridden SSSO
    # item if THAT SAME line's own note says SSSO/SSO — a line whose own note says CO/2C is a
    # deliberate, different car group and must be left alone regardless of what any other line says.
    _2C2T_OPERATOR_RE = re.compile(r"^SGV2_DOOR_OP_2[CT]", re.IGNORECASE)
    _2C2T_PANEL_RE = re.compile(r"^2[CT]_UNIVERSAL_CAR_DOOR", re.IGNORECASE)

    def _item_note_says_1s(item: Dict[str, Any]) -> bool:
        return bool(re.search(r"\bSSSO\b|\bSSO\b", str(item.get("note") or "").upper()))

    _autocorrect_ssso_1s_override(line_items, door_operator_qty, _item_note_says_1s)

    # IMPORTANT — this used to unconditionally force SGV2_DOOR_TOOLS (the door-programming tool) to
    # a $0 subtotal whenever a door operator was on the same estimate, reasoning from ONE real Sales
    # Order (SO-261024) that happened to show unit_price $598.50 / subtotal $0.00. That turned out to
    # be a wrong overgeneralization: a real, client-confirmed quote (EST-261132, "25 Telegram Mews")
    # billed this exact line as chargeable ($698.50, no override) on an estimate that also carried
    # eight door operators — proof this item is NOT always bundled just because a door operator is
    # present. Forcing the override was actively harmful there: it would have silently zeroed a real
    # $698.50 charge regardless of what the model (correctly) submitted. Do not reintroduce a
    # blanket auto-override for this line without a per-project signal to key it off of (e.g. the
    # RFQ/customer record explicitly stating the tool is being supplied from stock/already owned) —
    # see the skill instructions' Step 4 for the corrected, non-blanket guidance. Only flag it now,
    # so a human can confirm either way rather than a silent code-level assumption deciding it.
    if door_operator_qty > 0:
        for item in line_items:
            if item.get("category") != "door_tools":
                continue
            if item.get("subtotal_override") == 0:
                continue
            price_val = item.get("unit_price")
            try:
                price_val = float(price_val) if price_val is not None else None
            except (TypeError, ValueError):
                price_val = None
            if price_val is None or price_val <= 0:
                continue
            item["needs_engineering_review"] = True
            note = (
                "ℹ door-programming tool billed as chargeable (no $0 override) — confirm this is "
                "correct for this project; it is NOT always bundled with a door operator purchase "
                "(a real quote, EST-261132, billed it chargeable alongside 8 door operators), but "
                "some historical orders have shown it supplied at $0. Verify against this "
                "customer's record/quoting rules before release. ⚠ " + rules_engine.cite_conflict("I-001")
            )
            item["note"] = (item.get("note") + " " if item.get("note") else "") + note

    for item in line_items:
        try:
            qty = float(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            desc = item.get("description") or item.get("product_code") or "an item"
            note = item.get("note") or ""
            dropped_notes.append(f"Excluded from the estimate: {desc}" + (f" — {note}" if note else ""))
            continue

        code = str(item.get("product_code") or "").strip()
        problems = []
        # car_door_panel is intentionally exempt from the length check: a direct check of the live
        # pricelist showed that section's real PRODUCT NAME values genuinely run ~130-150 characters
        # (e.g. "2C_UNIVERSAL_CAR_DOOR_PANEL - 42 X 84 (2-DOOR/SET) CAR_LANDING - DOOR_PANEL
        # REPLACEMENT ANY #4 SS 441 (FINISH)_CAR DOOR PANEL - MULTI SLOT (MOD)") — there is no
        # separate short SKU for this catalogue section, so a long code here is normal, not a sign
        # of raw/garbled multi-row text. Every other category's real codes are well under 100 chars.
        if len(code) > 100 and item.get("category") != "car_door_panel":
            problems.append(
                "product_code is implausibly long (>100 chars) — looks like raw search_pricelist "
                "result text got pasted in instead of a single code; a real code is always short"
            )
        if "_" not in code:
            problems.append("no underscore — likely read from the wrong pricelist column (TYPE/O.E.M./DESCRIPTION instead of PRODUCT NAME)")
        if item.get("category") == "car_door_panel" and code.upper().startswith("1S") and "SET" in code.upper():
            problems.append(
                "self-contradictory: this looks like a 1S panel code combined with '(N-DOOR/SET)' "
                "text — 1S doors have exactly one panel and are never sold as a set, so this code/"
                "price was likely blended from two different pricelist rows rather than copied from one"
            )
        if _item_note_says_1s(item) and (
            (item.get("category") == "door_operator" and _2C2T_OPERATOR_RE.match(code))
            or (item.get("category") == "car_door_panel" and _2C2T_PANEL_RE.match(code))
        ):
            problems.append(
                "self-contradictory: this line's OWN note says its Entrance Type is SSSO/SSO, which "
                "the skill's own mapping table says means 1S — but this line uses a 2C/2T code "
                "anyway. This exact override (citing some other existing-equipment field, e.g. a "
                "legacy operator brand/model name, as if it conflicts with the entrance-type "
                "mapping) has caused real overcharges of several thousand dollars in prior rounds. "
                "Re-verify the door type before release — the entrance type is very likely 1S here."
            )
        if len(code) < 10 or code.replace("_", "").replace(".", "").replace("-", "").isdigit():
            problems.append("too short or numeric to be a real pricelist code — looks fabricated, not copied from a search_pricelist result")
        elif (
            whitelist
            and item.get("category") in _WHITELIST_CHECKED_CATEGORIES
            and code not in whitelist
            and code not in _WHITELIST_KNOWN_EXTRAS
            # Governors are a real, confirmed exception to "the code must appear verbatim": the
            # pricelist's own PRODUCT NAME column has no hand suffix (e.g. "WG_OL35_GOVERNOR_RC"),
            # but real FTL invoices append one anyway (e.g. "WG_OL35_GOVERNOR_RC_LH", confirmed on
            # a real client quote, EST-261132) even though the governor itself isn't handed. Strip a
            # trailing _LH/_RH before the whitelist check for this one category so a correctly-
            # suffixed governor code isn't wrongly flagged as fabricated.
            and not (
                item.get("category") == "governor"
                and re.sub(r"_(LH|RH)$", "", code, flags=re.IGNORECASE) in whitelist
            )
        ):
            problems.append("not found verbatim anywhere in the indexed pricelist — likely fabricated, misspelled, or reconstructed from memory instead of copied from a fresh search_pricelist result")

        price = item.get("unit_price")
        is_declared_zero_bundle = item.get("subtotal_override") == 0
        try:
            price_val = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_val = None
        if not is_declared_zero_bundle and (price_val is None or price_val <= 0):
            problems.append("unit_price missing or zero — this is NOT a confirmed $0 bundled item (no subtotal_override:0), so this line is likely undercounting the total")

        if item.get("category") == "car_safety" and door_operator_qty > 0 and qty > door_operator_qty:
            problems.append(
                f"qty ({qty:g}) exceeds the door operator line's qty ({door_operator_qty:g}), which "
                f"is normally the car count — a 'SYNC' safety row is already one full car's worth of "
                f"equipment, so safety qty shouldn't exceed car count unless the spec explicitly "
                f"calls for a duplex/redundant configuration"
            )

        if problems:
            item["needs_engineering_review"] = True
            warning = "⚠ " + "; ".join(problems) + ". Verify against search_pricelist before release."
            item["note"] = (item.get("note") + " " if item.get("note") else "") + warning
        kept.append(item)

    kept = _collapse_duplicate_and_hand_pairs(kept, dropped_notes)

    categories_present = {item.get("category") for item in kept}
    if "door_operator" in categories_present and "car_door_panel" not in categories_present:
        # Informational only — deliberately NOT auto-added, since fabricating a code/price would
        # be worse than an honest gap. Most real quotes with a door operator also carry a matching
        # universal car door panel line (see the "Car Door Equipment" scope-checklist rule above);
        # a real test run omitted it with no explanation, missing ~$3,270 of real scope. Surface it
        # so a human notices the gap instead of assuming completeness.
        dropped_notes.append(
            "No car door panel line was included even though a door operator is being quoted — "
            "double-check whether one should have been added (see the 'Car Door Equipment' New "
            "Equipment checklist item and the door operator's own hand/width/door-type)."
        )

    quote["line_items"] = kept
    if dropped_notes:
        assumptions = quote.get("assumptions") or []
        quote["assumptions"] = list(assumptions) + dropped_notes
    return quote



_WRONG_DOOR_OP_CODE_RE = re.compile(r"^SGV2_DOOR_OP_2[CT](\d+)_(LH|RH)$", re.IGNORECASE)
_WRONG_PANEL_WIDTH_RE = re.compile(r"\b2[CT]_UNIVERSAL_CAR_DOOR[A-Z_]*\s*-\s*(\d+)\s*X\s*84", re.IGNORECASE)


def _autocorrect_ssso_1s_override(
    line_items: List[Dict[str, Any]],
    door_operator_qty: float,
    item_says_1s: "Callable[[Dict[str, Any]], bool]",
) -> None:
    """Swaps a 2C/2T door_operator line for the real 1S code+price at the same width/hand, and a
    2C/2T car_door_panel line for the real 1S panel row (code, un-halved per-row price, and qty
    corrected from "2 panels x cars" down to "1 panel x cars" — 1S doors have exactly one panel per
    car, never a set) — but ONLY for a line whose OWN note says its entrance type is SSSO/SSO (via
    `item_says_1s`), never quote-wide. See the caller in _sanitize_quote for why this must be
    per-line: a real, confirmed multi-car-group project legitimately has SOME cars at SSSO/1S and
    OTHER cars at CO/2C in the same quote, and an earlier quote-wide version of this check silently
    deleted an entire real 2C door-operator configuration because a DIFFERENT line's note mentioned
    SSSO. Does nothing if a matching pricelist row can't be found (leaves the line for the fallback
    self-contradiction flag in the main loop to catch instead)."""
    try:
        op_rows = door_operator_prices()
    except Exception:
        op_rows = []
    try:
        panel_rows = car_door_panel_prices()
    except Exception:
        panel_rows = []
    op_by_key = {(r["width"], r["hand"]): r for r in op_rows if r["door_type"] == "1S"}
    panel_by_width = {r["width"]: r for r in panel_rows if r["door_type"] == "1S"}

    for item in line_items:
        if not item_says_1s(item):
            continue
        category = item.get("category")
        code = str(item.get("product_code") or "")

        if category == "door_operator":
            m = _WRONG_DOOR_OP_CODE_RE.match(code)
            if not m:
                continue
            match = op_by_key.get((m.group(1), m.group(2).upper()))
            if match is None:
                continue
            old_code, old_price = item.get("product_code"), item.get("unit_price")
            item["product_code"] = match["code"]
            item["unit_price"] = match["price"]
            item["needs_engineering_review"] = True
            note = (
                f"⚠ auto-corrected: entrance type is SSSO (=1S per the mapping table), but this line "
                f"used a 2C/2T code ({old_code!r} at ${float(old_price or 0):,.2f}) — swapped to the "
                f"real 1S code/price for the same width/hand ({match['code']!r} at ${match['price']:,.2f})."
            )
            item["note"] = (item.get("note") + " " if item.get("note") else "") + note

        elif category == "car_door_panel":
            wm = _WRONG_PANEL_WIDTH_RE.search(code)
            if not wm:
                continue
            match = panel_by_width.get(wm.group(1))
            if match is None:
                continue
            old_code, old_price, old_qty = item.get("product_code"), item.get("unit_price"), item.get("qty")
            item["product_code"] = match["code"]
            item["unit_price"] = match["unit_price"]
            if door_operator_qty > 0:
                item["qty"] = door_operator_qty
            item["needs_engineering_review"] = True
            note = (
                f"⚠ auto-corrected: entrance type is SSSO (=1S), but this line used a 2C/2T panel row "
                f"— swapped to the real 1S panel row for the same width. Price ${float(old_price or 0):,.2f} "
                f"-> ${match['unit_price']:,.2f} (1S is never sold as a set, so this is the pricelist's "
                f"own per-row price, not halved); qty {old_qty!r} -> {item.get('qty')!r} (1S has exactly "
                f"one panel per car, not two)."
            )
            item["note"] = (item.get("note") + " " if item.get("note") else "") + note


def _autocorrect_panel_prices(line_items: List[Dict[str, Any]], dropped_notes: List[str]) -> None:
    """The car door panel line's unit_price has been wrong in multiple real test rounds — the model
    writing the pricelist's raw full "(2-DOOR/SET)" price ($3,180) into unit_price instead of the
    per-panel half ($1,590), roughly doubling that line's cost. Prose alone hasn't reliably fixed
    this, so: if a car_door_panel line's unit_price exactly matches a known raw SET price from the
    pricelist, correct it to the per-panel price in place. Mutates line_items in place.

    IMPORTANT — this function used to ALSO rewrite product_code to a short synthetic form (e.g.
    "2C_UNIVERSAL_CAR_DOOR_PANEL_42X84") whenever it corrected the price, and had a separate fallback
    that did the same for oversized codes generally. That was wrong and has been removed: a direct
    check of the live pricelist_index.json showed the real PRODUCT NAME value for this catalogue
    section genuinely IS a long multi-word string (~130-150 characters, e.g. "2C_UNIVERSAL_CAR_DOOR_
    PANEL - 42 X 84 (2-DOOR/SET) CAR_LANDING - DOOR_PANEL REPLACEMENT ANY #4 SS 441 (FINISH)_CAR DOOR
    PANEL - MULTI SLOT (MOD)") — there is no separate short SKU for this section at all. A model that
    copies that whole string into product_code is doing exactly what the "copy verbatim from
    search_pricelist" rule asks; the synthetic short codes this function used to substitute in
    (e.g. "2C_UNIVERSAL_CAR_DOOR_PANEL_42X84") don't exist anywhere in the real catalogue and would
    have sent a human reviewer looking for a SKU that isn't real. Only unit_price is corrected here
    now; product_code is left exactly as submitted."""
    try:
        sets = panel_set_prices()
    except Exception:
        return
    if not sets:
        return
    by_set_price = {round(s["set_price"], 2): s for s in sets}

    for item in line_items:
        if item.get("category") != "car_door_panel":
            continue
        try:
            price_val = round(float(item.get("unit_price") or 0), 2)
        except (TypeError, ValueError):
            continue
        match = by_set_price.get(price_val)
        if match is None:
            continue
        item["unit_price"] = match["per_panel_price"]
        item["needs_engineering_review"] = True
        note = (
            f"⚠ auto-corrected: unit_price was ${match['set_price']:,.2f}, the full "
            f"{match['doors_per_set']}-door SET price from the pricelist — the per-panel invoicing "
            f"convention requires dividing by {match['doors_per_set']} (${match['per_panel_price']:,.2f}"
            f"/panel)."
        )
        item["note"] = (item.get("note") + " " if item.get("note") else "") + note


def _normalize_description(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _collapse_duplicate_and_hand_pairs(
    items: List[Dict[str, Any]], dropped_notes: List[str]
) -> List[Dict[str, Any]]:
    """Two DIFFERENT real failure modes live in this one function, and they require opposite fixes
    — getting this wrong in either direction has caused real dollar errors, so read both before
    touching this logic again.

    Failure mode A — same physical item billed twice at full quantity (the original bug this
    function was built for): both hands (door operator/clutch at qty 6 LH AND again at qty 6 RH on
    a 6-car project), or the same item under two different-looking codes (`CSGB03-SYNC` the TYPE
    column value, and `WS_CSGB_CAR_SAFETIES` the real PRODUCT NAME value, both at the same qty/
    price) — a real case that cost +$29,993.70. The fix here is to KEEP ONLY ONE line.

    Failure mode B — a real per-car/per-opening breakdown correctly split across multiple partial-
    quantity lines that share the same category+price (e.g. governors resolved car-by-car: 2+1+1+1,
    correctly totaling 5 real governors to match a 5-car project) — a real, confirmed run produced
    exactly this shape, and an earlier version of this function collapsed it down to just the FIRST
    line's qty of 2, silently discarding 3 real governors (and, separately, silently dropped an
    entire real door-operator configuration for two different cars that coincidentally landed on
    the same category+price key as a different car's operator line). The fix here is to SUM the
    quantities into one consolidated line, not discard the rest.

    Telling these apart from (category, quantity, price) alone is NOT reliable — mode B routinely
    produces different quantities per line (that's the whole point of a per-car breakdown), and a
    same-code, same-price, DIFFERENT-quantity group is almost always mode B, never mode A (nobody
    accidentally submits the identical item three times at three different quantities). So: group by
    (category, price) only — not quantity — then decide per group using product_code and
    description, which is a far more reliable signal of "is this actually the same physical
    configuration":
    - If every item in the group shares the SAME product_code: this is one configuration split
      into partial quantities (mode B, even if by coincidence every split happened to use the same
      qty) — SUM the quantities into one line. Only flag for review if the quantities were all
      identical (a repeat of the exact same qty could still be an accidental full-duplicate
      resubmission rather than a deliberate split; summing is still the safer default since
      discarding real scope is the worse failure, but a human should confirm which it was).
    - If codes DIFFER but normalized descriptions are essentially the same text: this is the same
      physical item under different code spellings (mode A) — keep ONE survivor's code, and only
      SUM quantities if they differ (still mode B-shaped, just with an uncertain code); if
      quantities are equal, that equal amount is most likely the one true total submitted twice, so
      keep just that one quantity (not doubled) and discard the rest, flagged for review.
    - If codes differ AND descriptions clearly differ too: these are almost certainly genuinely
      DIFFERENT physical items that only coincidentally share category+price — a real confirmed
      case was exactly this (a 42" center-opening operator line and a 42" single-speed operator
      line for different cars, both real, that happened to share a price after one line's code was
      mis-copied). Do NOT collapse or discard anything here — pass every line through unchanged,
      each flagged to double-check its product_code, since a coincidental price match across
      visibly different descriptions is unusual enough to be worth a human's eye."""
    groups: Dict[Tuple[Any, float], List[Dict[str, Any]]] = {}
    order: List[Tuple[Any, float]] = []
    for item in items:
        try:
            price = round(float(item.get("unit_price") or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
        key = (item.get("category"), price)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    result: List[Dict[str, Any]] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            result.append(group[0])
            continue

        # Pre-pass: merge any EXACT product_code duplicates within this (category, price) group
        # first, before deciding mode A/B/flag-only for what's left. A real confirmed run mixed a
        # clean per-car split of ONE code (e.g. two "_LH" lines, qty 4 + qty 2 — should sum to 6)
        # together with a genuinely different-hand line (a "_RH" line) in the same category/price
        # group. Deciding mode A vs "flag only" on the group AS A WHOLE made the codes-differ /
        # descriptions-differ checks below see 2 distinct codes and 2 distinct descriptions and fall
        # through to "flag each, don't merge" — which left the two identical-code lines un-merged
        # too, even though they trivially share the same product_code and description and should
        # have summed to one line under the very first rule below. Collapsing same-code duplicates
        # here first — independent of whatever other codes happen to share this group — fixes that
        # without changing behavior for any group that was already single-code.
        by_code: Dict[Any, List[Dict[str, Any]]] = {}
        code_order: List[Any] = []
        for g in group:
            code = g.get("product_code")
            if code not in by_code:
                by_code[code] = []
                code_order.append(code)
            by_code[code].append(g)
        if any(len(v) > 1 for v in by_code.values()):
            merged_group: List[Dict[str, Any]] = []
            for code in code_order:
                subgroup = by_code[code]
                if len(subgroup) == 1:
                    merged_group.append(subgroup[0])
                    continue
                survivor = dict(subgroup[0])
                sub_qtys = [float(g.get("qty") or 0) for g in subgroup]
                survivor["qty"] = sum(sub_qtys)
                survivor["needs_engineering_review"] = True
                note = (
                    f"ℹ {len(subgroup)} lines with the identical product_code/price were merged "
                    f"into one (qty summed to {survivor['qty']:g}) — consolidated from what looks "
                    f"like a per-car/per-opening breakdown."
                )
                survivor["note"] = (survivor.get("note") + " " if survivor.get("note") else "") + note
                merged_group.append(survivor)
            group = merged_group
            if len(group) == 1:
                result.append(group[0])
                continue

        codes = {g.get("product_code") for g in group}
        descs = {_normalize_description(g.get("description")) for g in group}
        qtys = [float(g.get("qty") or 0) for g in group]
        same_qty = len(set(round(q, 4) for q in qtys)) == 1

        if len(codes) == 1:
            # Mode B: identical configuration, split into partial quantities — sum, don't discard.
            survivor = dict(group[0])
            survivor["qty"] = sum(qtys)
            if same_qty and len(group) > 1:
                survivor["needs_engineering_review"] = True
                note = (
                    f"ℹ {len(group)} lines with the identical product_code/price were merged into "
                    f"one (qty summed to {survivor['qty']:g}) — this is very likely a correct per-"
                    f"car/per-opening breakdown that should have been consolidated, but confirm it "
                    f"wasn't an accidental resubmission of the same quantity."
                )
            else:
                note = (
                    f"ℹ {len(group)} lines with the identical product_code/price were merged into "
                    f"one (qty summed to {survivor['qty']:g}) — consolidated from what looks like a "
                    f"per-car/per-opening breakdown."
                )
            survivor["note"] = (survivor.get("note") + " " if survivor.get("note") else "") + note
            result.append(survivor)
        elif len(descs) == 1:
            # Mode A: same physical item, different code spellings.
            survivor = dict(group[0])
            other_codes = [str(c) for c in codes if c != survivor.get("product_code")]
            if same_qty:
                survivor["needs_engineering_review"] = True
                warning = (
                    f"⚠ duplicate collapsed: also submitted as {', '.join(other_codes)} at the "
                    f"same quantity/price with an identical description — kept only this line to "
                    f"avoid double-billing the same physical item; confirm the correct code/hand/"
                    f"variant before release."
                )
                survivor["note"] = (survivor.get("note") + " " if survivor.get("note") else "") + warning
                result.append(survivor)
                for dup in group[1:]:
                    dropped_notes.append(
                        f"Duplicate removed from the estimate: {dup.get('description')} "
                        f"({dup.get('product_code')}) — submitted alongside "
                        f"{survivor.get('product_code')} at the same category/price/quantity with "
                        f"an identical description; kept only one line pending code confirmation."
                    )
            else:
                survivor["qty"] = sum(qtys)
                survivor["needs_engineering_review"] = True
                note = (
                    f"ℹ {len(group)} lines with an identical description but different codes "
                    f"({', '.join(other_codes)}) and different quantities were merged (qty summed "
                    f"to {survivor['qty']:g}) — confirm which product_code is correct before "
                    f"release."
                )
                survivor["note"] = (survivor.get("note") + " " if survivor.get("note") else "") + note
                result.append(survivor)
        else:
            # Descriptions clearly differ too — likely genuinely different items that coincidentally
            # share category+price. Don't collapse or discard anything; just flag each for a
            # human to double-check its code, since the coincidence itself is worth a second look.
            for it in group:
                it["needs_engineering_review"] = True
                note = (
                    "ℹ shares category and unit_price with another distinct-looking line on this "
                    "estimate (different description/code) — a coincidental price match across "
                    "visibly different items can also indicate a copy-paste code error on one of "
                    "them; verify this line's product_code is correct for its own description "
                    "before release."
                )
                it["note"] = (it.get("note") + " " if it.get("note") else "") + note
                result.append(it)
    return result


def _run_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    """Execute only explicitly supported tools and return JSON-safe results."""
    if name != "search_pricelist":
        return json.dumps({"error": f"Unknown tool: {name}"})

    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({
            "results": [],
            "error": "A non-empty item/configuration query is required.",
        })
    if len(query) > 500:
        query = query[:500]

    try:
        results = search_pricelist(query, top_k=5) or []
    except Exception as exc:
        # Do not pretend a failed lookup is a no-match or let the model infer
        # a price from an unavailable source.
        return json.dumps({
            "results": [],
            "error": f"Pricelist lookup failed: {type(exc).__name__}",
            "note": "Do not guess a product code or price; flag the item for review.",
        })

    if not results:
        return json.dumps({
            "results": [],
            "note": "No pricelist matches found. Do not invent a code or price.",
        })
    return json.dumps({"results": results}, default=str)



def _validate_quote_payload(quote: Any) -> Dict[str, Any]:
    """Validate the model's submitted structure before any business-rule mutation.

    This does not decide whether a product is technically suitable. It rejects
    malformed payloads and normalizes safe primitive fields so bad JSON shapes
    cannot silently become a rendered estimate.
    """
    if not isinstance(quote, dict):
        raise RuntimeError("submit_quote must contain a JSON object.")

    line_items = quote.get("line_items", [])
    if not isinstance(line_items, list):
        raise RuntimeError("submit_quote.line_items must be a JSON array.")

    normalized_items = []
    for index, item in enumerate(line_items):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"submit_quote.line_items[{index}] must be a JSON object."
            )

        item = dict(item)
        category = str(item.get("category") or "other").strip()
        allowed_categories = {
            "door_operator", "clutch", "panel_adaptor", "car_door_panel",
            "roller_guide", "governor", "car_safety",
            "door_protective_device", "door_tools", "other",
        }
        if category not in allowed_categories:
            item["needs_engineering_review"] = True
            item["note"] = (
                (str(item.get("note") or "") + " ")
                + f"⚠ Unknown category {category!r}; normalized to 'other'."
            ).strip()
            category = "other"
        item["category"] = category

        for field in ("qty", "unit_price"):
            value = item.get(field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = float("nan")
            if not (number == number and abs(number) != float("inf")):
                item["needs_engineering_review"] = True
                item["note"] = (
                    (str(item.get("note") or "") + " ")
                    + f"⚠ {field} is missing or non-numeric; verify before release."
                ).strip()
            else:
                item[field] = number

        override = item.get("subtotal_override")
        if override is not None:
            try:
                item["subtotal_override"] = float(override)
            except (TypeError, ValueError):
                item["subtotal_override"] = None
                item["needs_engineering_review"] = True
                item["note"] = (
                    (str(item.get("note") or "") + " ")
                    + "⚠ Invalid subtotal_override; cleared for manual review."
                ).strip()

        if item.get("unit_price") is not None and item.get("unit_price", 0) < 0:
            item["needs_engineering_review"] = True
            item["note"] = (
                (str(item.get("note") or "") + " ")
                + "⚠ Negative unit price is not accepted; verify against the source pricelist."
            ).strip()

        normalized_items.append(item)

    quote = dict(quote)
    quote["line_items"] = normalized_items

    # Normalize top-level text fields without fabricating missing customer data.
    for field in (
        "project_name", "customer_name", "contact_name", "contact_phone",
        "billing_address", "shipping_address", "bdm", "freight_note",
        "payment_terms", "remarks",
    ):
        value = quote.get(field, "")
        quote[field] = "" if value is None else str(value).strip()

    if not isinstance(quote.get("assumptions"), list):
        quote["assumptions"] = (
            [str(quote["assumptions"])]
            if quote.get("assumptions") not in (None, "")
            else []
        )

    try:
        freight = float(quote.get("freight_estimate", 0) or 0)
    except (TypeError, ValueError):
        freight = 0.0
        quote["assumptions"].append(
            "⚠ Freight estimate was non-numeric and reset to 0; confirm freight manually."
        )
    if not (freight == freight and abs(freight) != float("inf")) or freight < 0:
        freight = 0.0
        quote["assumptions"].append(
            "⚠ Freight estimate was invalid/negative and reset to 0; confirm freight manually."
        )
    quote["freight_estimate"] = freight
    return quote


def _finalize_quote(quote: Dict[str, Any], candidate_text: str) -> Dict[str, Any]:
    quote = _validate_quote_payload(quote)
    quote = _enforce_scope_table(quote, candidate_text)
    quote = _check_door_package_completeness(quote, candidate_text)
    quote = _enforce_governor_presence(quote, candidate_text)
    quote = _autocorrect_governor_split(quote, candidate_text)
    quote = _check_governor_count(quote, candidate_text)
    quote = _check_governor_variant_differentiation(quote, candidate_text)
    # --- FTL Stage 2 rulebook enforcement (received Sep 2026) — see rules_engine.py ---
    quote = _reconcile_against_product_master(quote)
    quote = _enforce_governor_tension_companions(quote)
    quote = _enforce_safety_gear_block(quote)
    quote = _enforce_wrg_gating(quote, candidate_text)
    quote = _normalize_detector_lines(quote)
    quote = _apply_universal_door_panel_conflict_notes(quote)
    # --- end Stage 2 rulebook enforcement ---
    quote = _check_door_tools_presence(quote)
    quote = _apply_temporary_flat_freight(quote)
    quote = _compose_remarks(quote, candidate_text)
    quote = _sanitize_quote(quote)
    return quote


def _run_quote_json_mode(
    client: Any, model_name: str, skill: Dict[str, Any], candidate_text: str
) -> Tuple[Dict[str, Any], int]:
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
                    "content": "Please submit your final quote now using action submit_quote in valid JSON.",
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
            clean_content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
            try:
                data = json.loads(clean_content)
            except json.JSONDecodeError:
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": "Your response was not valid JSON. Please return valid JSON only.",
                    }
                )
                continue

        action = data.get("action")
        is_quote = (
            action == "submit_quote"
            or "line_items" in data
            or (isinstance(data.get("quote"), dict) and "line_items" in data["quote"])
        )

        if is_quote:
            if isinstance(data.get("quote"), dict):
                quote = data["quote"]
            else:
                quote = data
            if not isinstance(quote, dict):
                quote = {}

            quote.setdefault("project_name", "")
            quote.setdefault("customer_name", "")
            quote.setdefault("contact_name", "")
            quote.setdefault("contact_phone", "")
            quote.setdefault("billing_address", "")
            quote.setdefault("shipping_address", "")
            quote.setdefault("bdm", "")
            quote.setdefault("line_items", [])
            quote.setdefault("freight_estimate", 0)
            quote.setdefault("freight_note", "")
            quote.setdefault("payment_terms", "")
            quote.setdefault("remarks", "")
            quote.setdefault("assumptions", [])

            if force_final and len(quote.get("line_items") or []) <= 2:
                raise RuntimeError(
                    "The model ran out of tool-call rounds before finishing its research and was "
                    f"forced to submit an incomplete quote ({len(quote.get('line_items') or [])} "
                    "line item(s)). Please try again."
                )

            quote = _finalize_quote(quote, candidate_text)
            return quote, total_tokens

        if action == "search_pricelist" or "query" in data or "search_pricelist" in data:
            query = data.get("query")
            if not query and isinstance(data.get("search_pricelist"), str):
                query = data["search_pricelist"]
            elif not query and isinstance(data.get("search_pricelist"), dict):
                query = data["search_pricelist"].get("query", "")
            if not query:
                query = ""

            result = _run_tool_call("search_pricelist", {"query": query})
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {"role": "user", "content": f"Tool search_pricelist result for '{query}':\n{result}"}
            )
            continue

        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": 'Please respond with either {"action": "search_pricelist", "query": "..."} or {"action": "submit_quote", "quote": {...}} in valid JSON.',
            }
        )

    raise RuntimeError(f"Model did not submit a quote within {MAX_LOOKUP_ROUNDS} rounds.")


def _run_quote_native_tools(
    client: Any, model_name: str, skill: Dict[str, Any], candidate_text: str
) -> Tuple[Dict[str, Any], int]:
    system_prompt = build_system_prompt(skill, is_json_mode=False)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": candidate_text},
    ]
    tools = [_SEARCH_PRICELIST_TOOL, _SUBMIT_QUOTE_TOOL]
    total_tokens = 0

    for round_num in range(MAX_LOOKUP_ROUNDS + 1):
        force_final = round_num == MAX_LOOKUP_ROUNDS
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice=(
                {"type": "function", "function": {"name": "submit_quote"}} if force_final else "auto"
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
                    "content": "Please respond only via a tool call — either search_pricelist or, if you're ready, submit_quote.",
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

        quote_call = next((tc for tc in tool_calls if tc.function.name == "submit_quote"), None)
        if quote_call is not None:
            try:
                quote = json.loads(quote_call.function.arguments)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Model returned invalid JSON for its quote: {e}") from e
            if force_final and len(quote.get("line_items") or []) <= 2:
                raise RuntimeError(
                    "The model ran out of tool-call rounds before finishing its research and was "
                    f"forced to submit an incomplete quote ({len(quote.get('line_items') or [])} "
                    "line item(s)). Please try again."
                )
            quote = _finalize_quote(quote, candidate_text)
            return quote, total_tokens

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_tool_call(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    raise RuntimeError(f"Model did not submit a quote within {MAX_LOOKUP_ROUNDS} tool-call rounds.")


def run_quote_estimation(
    skill: Dict[str, Any],
    candidate_text: str,
    llm_overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], int]:
    """Runs the bounded agentic loop and returns (quote_dict, total_tokens).

    Model and API key come from the same preset overrides other agents use.
    """
    from app.ftl.llm import open_client, prefers_json_mode, resolve_llm_config

    config = resolve_llm_config(llm_overrides)
    client, deploy_model = open_client(config)
    model_name = str(config.get("model") or deploy_model)

    if prefers_json_mode(model_name):
        return _run_quote_json_mode(client, deploy_model, skill, candidate_text)
    try:
        return _run_quote_native_tools(client, deploy_model, skill, candidate_text)
    except Exception as e:
        message = str(e).lower()
        tool_schema_error = (
            "tool" in message
            and any(term in message for term in (
                "unsupported", "not support", "invalid", "schema", "function"
            ))
        ) or ("400" in message and "tool" in message)
        if tool_schema_error:
            return _run_quote_json_mode(client, deploy_model, skill, candidate_text)
        raise
