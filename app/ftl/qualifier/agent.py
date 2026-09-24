"""
The Qualifier's agentic decision loop: skill instructions + candidate text (from extract.py) ->
bounded tool-calling rounds against search_pricelist -> a forced final tool call that produces the
structured decision. Same shape as the SALES CRM reference project's qualifier_agent.py, but
DB-free: search_pricelist reads pricelist_store.py's local JSON index instead of a SQL table.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI, AzureOpenAI

try:
    from app.ftl.qualifier.pricelist_store import search_pricelist
except ImportError:
    from pricelist_store import search_pricelist

MAX_LOOKUP_ROUNDS = 4
QUALIFIER_CHAT_MODEL = (
    os.getenv("QUALIFIER_CHAT_MODEL") or os.getenv("LLM_MODEL") or "qwen3.5-9b"
).strip()


def get_client() -> Union[OpenAI, AzureOpenAI]:
    model_name = (
        os.getenv("QUALIFIER_CHAT_MODEL") or os.getenv("LLM_MODEL") or "qwen3.5-9b"
    ).strip().lower()

    qwen_ep = (
        os.getenv("QWEN_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or "http://ezinferencebox.southindia.azurecontainer.io:8080/v1"
    )
    qwen_key = (
        os.getenv("QWEN_API_KEY")
        or os.getenv("QWEN_MAC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or "a6d7e1c198975ca5d73af08f4a82df52d9a55b044e78e348dfea9d1535cced0c"
    )

    if model_name.startswith("qwen") or "qwen" in model_name:
        return OpenAI(
            base_url=qwen_ep,
            api_key=qwen_key,
        )

    azure_ep = (
        os.getenv("AZURE_OPENAI_ENDPOINT")
        or (
            os.getenv("AZURE_EAST_US_API_BASE", "https://api-4omin-ez.openai.azure.com")
            if "4o" in model_name
            else (os.getenv("AZURE_SOUTH_INDIA_API_BASE") or "https://ezazopenai.openai.azure.com/")
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
    openai_key = os.getenv("OPENAI_API_KEY")

    if openai_key and openai_key.startswith("sk-"):
        return OpenAI(api_key=openai_key)
    elif azure_ep and (azure_key or (openai_key and not openai_key.startswith("sk-"))):
        api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        return AzureOpenAI(
            azure_endpoint=azure_ep,
            api_key=azure_key or openai_key,
            api_version=api_ver,
        )
    elif openai_key:
        return OpenAI(api_key=openai_key)
    elif azure_key:
        api_ver = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        return AzureOpenAI(
            azure_endpoint=azure_ep,
            api_key=azure_key,
            api_version=api_ver,
        )
    else:
        return OpenAI()

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
                "reasoning": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["qualify", "project_type", "matched_items", "excluded_items", "flags", "reasoning", "confidence"],
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
            '  "reasoning": "...",\n'
            '  "confidence": 0.0 to 1.0\n'
            '}}\n\n'
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
            decision.setdefault("qualify", "needs_review")
            decision.setdefault("project_type", "unknown")
            decision.setdefault("matched_items", [])
            decision.setdefault("excluded_items", [])
            decision.setdefault("flags", [])
            decision.setdefault("deadline", None)
            decision.setdefault("project_name", "")
            decision.setdefault("reasoning", "")
            decision.setdefault("confidence", 0.8)
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
    (catalog / tenant selection, or the process default such as gpt-5-nano).
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

