"""duplicate_detect — same invoice number vs prior artifacts / cloud history."""
from __future__ import annotations

from typing import Any

from app.ap_skills.types import ApContext, ApSkillResult, field_text, invoice_from, name_similarity

SKILL_ID = "duplicate_detect"

# Only a prior *successful* match counts as a hard duplicate from local
# AP skill history. Re-ingesting the same PDF (new item_key) after a
# NOT_MATCHED / DUPLICATE / PARTIALLY_MATCHED run must not force Not Matched
# forever — those are retries, not a second payment.
_HARD_PRIOR_DECISIONS = frozenset({"MATCHED"})


def _invoice_of(payload: dict) -> dict:
    inner = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else payload
    return inner if isinstance(inner, dict) else {}


def _decision_of(payload: dict[str, Any]) -> str:
    raw = payload.get("decision")
    if raw is None and isinstance(payload.get("finalize_decision"), dict):
        raw = payload["finalize_decision"].get("decision")
    return str(raw or "").strip().upper()


async def run(ctx: ApContext) -> ApSkillResult:
    invoice = invoice_from(ctx)
    invoice_number = field_text(invoice, "invoice_number")
    vendor = field_text(invoice, "vendor", "supplier")

    store_history: list[dict[str, Any]] = []
    matched_item_keys: set[str] = set()
    if ctx.store is not None:
        store_history.extend(
            await ctx.store.list_skill_artifacts(tenant_id=ctx.tenant_id, skill_id="extract_invoice")
        )
        for row in await ctx.store.list_skill_artifacts(
            tenant_id=ctx.tenant_id, skill_id="finalize_decision"
        ):
            if not isinstance(row, dict):
                continue
            item_key = str(row.get("item_key") or "").strip()
            if item_key and _decision_of(row) in _HARD_PRIOR_DECISIONS:
                matched_item_keys.add(item_key)

    cloud_history = await ctx.ezofis.lookup_invoice_history(
        tenant_id=ctx.tenant_id, invoice_number=invoice_number or None
    )
    if not isinstance(cloud_history, list):
        cloud_history = []

    duplicate_of = None
    score = 0.0
    # Code-review finding #11: same-vendor-but-different-invoice-number
    # (a likely typo'd/reformatted duplicate) — a real, distinct, weaker
    # signal from an exact invoice-number match, tracked separately so
    # finalize_decision can downgrade a decision on it without treating it
    # as a certain duplicate the way an exact match is.
    possible_duplicate_of = None
    possible_duplicate_score = 0.0

    def _consider_possible(prior_no: str, prior_vendor: str) -> None:
        nonlocal possible_duplicate_of, possible_duplicate_score
        vendor_sim = name_similarity(vendor, prior_vendor)
        if not (invoice_number and prior_no and vendor_sim >= 0.85):
            return
        combined = round((vendor_sim + name_similarity(invoice_number, prior_no)) / 2, 4)
        if combined > possible_duplicate_score:
            possible_duplicate_of = prior_no
            possible_duplicate_score = combined

    # Local AP extracts: hard duplicate only if that prior item was MATCHED.
    for prior in store_history:
        if not isinstance(prior, dict):
            continue
        prior_key = str(prior.get("item_key") or "").strip()
        if prior_key and prior_key == ctx.item_key:
            continue
        prior_inv = _invoice_of(prior)
        prior_no = field_text(prior_inv, "invoice_number")
        prior_vendor = field_text(prior_inv, "vendor", "supplier")
        if invoice_number and prior_no and invoice_number.lower() == prior_no.lower():
            if prior_key and prior_key in matched_item_keys:
                duplicate_of = prior_no
                score = 1.0
                break
            # Same invoice on a prior non-MATCHED run = retry / re-ingest.
            # Do not soft-flag either (that would cap MATCHED → PARTIAL).
            continue
        _consider_possible(prior_no or "", prior_vendor)

    # Cloud / ERP history: exact invoice number is always a hard duplicate.
    if not duplicate_of:
        for prior in cloud_history:
            if not isinstance(prior, dict):
                continue
            prior_key = str(prior.get("item_key") or "").strip()
            if prior_key and prior_key == ctx.item_key:
                continue
            prior_inv = _invoice_of(prior)
            prior_no = field_text(prior_inv, "invoice_number")
            prior_vendor = field_text(prior_inv, "vendor", "supplier")
            if invoice_number and prior_no and invoice_number.lower() == prior_no.lower():
                duplicate_of = prior_no
                score = 1.0
                break
            _consider_possible(prior_no or "", prior_vendor)

    return ApSkillResult(
        skill_id=SKILL_ID,
        data={
            "is_duplicate_invoice": bool(duplicate_of),
            "duplicate_of": duplicate_of,
            "duplicate_score": score,
            "possible_duplicate_of": possible_duplicate_of,
            "possible_duplicate_score": possible_duplicate_score,
            "checked_history": len(store_history) + len(cloud_history),
        },
    )
