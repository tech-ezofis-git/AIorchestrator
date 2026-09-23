"""
Turns the agent's structured quote (as returned by agent.run_quote_estimation, matching the
submit_quote tool schema) into (a) deterministic totals and (b) self-contained HTML documents.

Supports two template types:
  1. "inflow" (Default, Customer-Facing): Authentic FTL inFlow Sales Estimate layout matching
     the official FTL Distribution PDF estimates (official logo, clean 5-column table, FTL Executive,
     signature dark navy total box, and standard warranty/delivery terms). Free of internal review flags.
  2. "internal" (Engineering Review): Comprehensive review layout showing item review notes,
     "⚠ needs engineering review" warning flags, Remarks box, and yellow Internal Review Notes.

Totals are computed here, never by the model — this file is the single source of truth for the
math. Verified against the real samples: HST = 13% of (subtotal + freight), and
Total = subtotal + freight + HST.
"""

from __future__ import annotations

import base64
import html
import math
import os
from datetime import date
from typing import Any, Dict, List

HST_RATE = 0.13

DEFAULT_PAYMENT_TERMS_TEXT = "50% Upon Acceptance of Order, 50% Upon Delivery (Net 30)"
PAYMENT_TERMS_TEXT = DEFAULT_PAYMENT_TERMS_TEXT

_BOILERPLATE_INTERNAL = (
    "Delivery: Lead times confirmed at order. "
    "Exclusions: Final specs/quantities to be confirmed prior to order and are Final. "
    "Warranty: Manufacturer warranties only. No additional implied warranties. Limited to "
    "replacement or refund of defective supplied materials. Suitability, installation, testing, "
    "adjustment, and regulatory compliance are the responsibility of the licensed elevator "
    "contractor."
)

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "ftl_logo.png")
_CACHED_LOGO_B64: str | None = None


def get_logo_base64() -> str:
    """Returns the base64-encoded FTL Distribution logo PNG."""
    global _CACHED_LOGO_B64
    if _CACHED_LOGO_B64 is not None:
        return _CACHED_LOGO_B64
    if os.path.exists(_LOGO_PATH):
        try:
            with open(_LOGO_PATH, "rb") as f:
                _CACHED_LOGO_B64 = base64.b64encode(f.read()).decode("ascii")
                return _CACHED_LOGO_B64
        except Exception:
            pass
    return ""


def _money(n: float) -> str:
    return f"${n:,.2f}"


def _resolve_subtotal(qty: float, unit_price: float, override: Any) -> float:
    """Resolves line item subtotal with defensive validation.

    If subtotal_override is provided and valid (including 0.0 for bundled/promotional items),
    returns round(float(override), 2). If override is None, empty string, non-numeric,
    negative, or non-finite, safely falls back to round(qty * unit_price, 2).
    """
    if override is not None and not isinstance(override, bool):
        if isinstance(override, (int, float)):
            if not math.isnan(override) and not math.isinf(override) and override >= 0:
                return round(float(override), 2)
        elif isinstance(override, str) and override.strip():
            try:
                val = float(override.strip().replace("$", "").replace(",", ""))
                if not math.isnan(val) and not math.isinf(val) and val >= 0:
                    return round(val, 2)
            except (ValueError, TypeError):
                pass
    return round(qty * unit_price, 2)


def compute_totals(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of the quote with each line item's resolved `subtotal` filled in, plus the
    deterministic subtotal/freight/hst/total figures for the whole estimate."""
    line_items: List[Dict[str, Any]] = []
    running_subtotal = 0.0

    for raw in (quote.get("line_items") if isinstance(quote, dict) else []) or []:
        if not isinstance(raw, dict):
            continue
        try:
            qty = float(raw.get("qty") or 0)
            if math.isnan(qty) or math.isinf(qty):
                qty = 0.0
        except (ValueError, TypeError):
            qty = 0.0

        try:
            unit_price = float(raw.get("unit_price") or 0)
            if math.isnan(unit_price) or math.isinf(unit_price):
                unit_price = 0.0
        except (ValueError, TypeError):
            unit_price = 0.0

        override = raw.get("subtotal_override")
        subtotal = _resolve_subtotal(qty, unit_price, override)
        running_subtotal += subtotal
        line_items.append(
            {
                "product_code": raw.get("product_code") or "",
                "description": raw.get("description") or "",
                "category": raw.get("category") or "",
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "note": raw.get("note") or "",
                "needs_engineering_review": bool(raw.get("needs_engineering_review")),
            }
        )

    try:
        freight = float(quote.get("freight_estimate") or 0) if isinstance(quote, dict) else 0.0
        if math.isnan(freight) or math.isinf(freight) or freight < 0:
            freight = 0.0
    except (ValueError, TypeError):
        freight = 0.0

    running_subtotal = round(running_subtotal, 2)
    hst = round((running_subtotal + freight) * HST_RATE, 2)
    total = round(running_subtotal + freight + hst, 2)

    return {
        "project_name": (quote.get("project_name") or "") if isinstance(quote, dict) else "",
        "customer_name": (quote.get("customer_name") or "") if isinstance(quote, dict) else "",
        "contact_name": (quote.get("contact_name") or "") if isinstance(quote, dict) else "",
        "contact_phone": (quote.get("contact_phone") or "") if isinstance(quote, dict) else "",
        "billing_address": (quote.get("billing_address") or "") if isinstance(quote, dict) else "",
        "shipping_address": (quote.get("shipping_address") or "") if isinstance(quote, dict) else "",
        "bdm": (quote.get("bdm") or "") if isinstance(quote, dict) else "",
        "line_items": line_items,
        "freight_estimate": freight,
        "freight_note": (quote.get("freight_note") or "") if isinstance(quote, dict) else "",
        "payment_terms": (quote.get("payment_terms") or "") if isinstance(quote, dict) else "",
        "remarks": (quote.get("remarks") or "") if isinstance(quote, dict) else "",
        "assumptions": (quote.get("assumptions") or []) if isinstance(quote, dict) else [],
        "subtotal": running_subtotal,
        "hst": hst,
        "total": total,
    }


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


# ---------------------------------------------------------------------------
# Template 1: Official inFlow Sales Estimate (Customer-Facing)
# ---------------------------------------------------------------------------


def render_inflow_quote_html(quote: Dict[str, Any], estimate_number: str, date_str: str | None = None) -> str:
    """Renders the customer-facing Sales Estimate HTML styled to exact-match the official
    FTL inFlow template (PDF 1 / EST-261041). Free of internal review flags and debug notes."""
    totals = compute_totals(quote)
    d = date.today()
    date_str = date_str or f"{d.strftime('%b')} {d.day}, {d.year}"

    logo_b64 = get_logo_base64()
    logo_img_html = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="FTL Distribution" style="max-height:80px;width:auto;display:block;" />'
        if logo_b64
        else '<div style="font-size:22px;font-weight:800;color:#0b2545;">FTL DISTRIBUTION</div>'
    )

    rows_html = []
    for item in totals["line_items"]:
        code = _esc(item["product_code"])
        desc = _esc(item["description"])
        qty_str = f"{item['qty']:g}"
        price_str = _esc(_money(item["unit_price"]))
        sub_str = _esc(_money(item["subtotal"]))

        rows_html.append(
            f"""
            <tr style="border-bottom:1px solid #eaeaea;">
              <td style="padding:10px 8px 10px 0;vertical-align:top;width:32%;">
                <div style="font-weight:700;color:#111;font-size:13px;">{code}</div>
              </td>
              <td style="padding:10px 8px;vertical-align:top;color:#333;font-size:13px;width:34%;">
                {desc}
              </td>
              <td style="padding:10px 8px;vertical-align:top;text-align:center;color:#222;font-size:13px;width:10%;">
                {qty_str}
              </td>
              <td style="padding:10px 8px;vertical-align:top;text-align:right;color:#222;font-size:13px;width:12%;">
                {price_str}
              </td>
              <td style="padding:10px 0 10px 8px;vertical-align:top;text-align:right;color:#222;font-size:13px;width:12%;">
                {sub_str}
              </td>
            </tr>"""
        )

    freight_row_html = ""
    if totals["freight_estimate"] > 0:
        freight_row_html = f"""
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px;color:#ffffff !important;">
          <span style="color:#ffffff !important;">Freight</span>
          <span style="color:#ffffff !important;font-weight:600;">{_esc(_money(totals['freight_estimate']))}</span>
        </div>"""

    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#222222;max-width:850px;margin:0 auto;background:#ffffff;padding:36px;border:1px solid #e0e0e0;box-shadow:0 2px 8px rgba(0,0,0,0.04);box-sizing:border-box;">
  
  <!-- Header: Logo | Company Address | Sales Estimate Title & Date -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;">
    <div style="display:flex;align-items:flex-start;gap:20px;">
      <div>{logo_img_html}</div>
      <div style="font-size:12px;color:#222222 !important;line-height:1.45;padding-top:2px;">
        <div style="font-weight:700;font-size:13px;color:#0b2545 !important;">FTL Distribution Inc.</div>
        <div style="color:#222222 !important;">8-12555 Coleraine Drive</div>
        <div style="color:#222222 !important;">Bolton, Ontario</div>
        <div style="color:#222222 !important;">L7E 0P6</div>
        <div style="color:#222222 !important;">866-702-1184</div>
        <div style="color:#0b2545 !important;">ftl-distribution.ca</div>
      </div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:26px;color:#111111 !important;font-weight:400;letter-spacing:-0.5px;margin-bottom:8px;">Sales Estimate</div>
      <div style="font-size:13px;color:#222222 !important;margin-bottom:4px;"><strong style="color:#000000 !important;">Estimate No.</strong>&nbsp;&nbsp;&nbsp;&nbsp;{_esc(estimate_number)}</div>
      <div style="font-size:13px;color:#222222 !important;"><strong style="color:#000000 !important;">Date</strong>&nbsp;&nbsp;&nbsp;&nbsp;{_esc(date_str)}</div>
    </div>
  </div>

  <!-- 4-Column Address and Contact Block -->
  <div style="display:grid;grid-template-columns:1.1fr 1.1fr 0.9fr 0.9fr;gap:16px;margin-bottom:28px;font-size:12.5px;color:#333333;line-height:1.45;">
    <div>
      <div style="font-weight:700;color:#000000 !important;margin-bottom:4px;font-size:13px;">Billing Address</div>
      <div style="color:#222222 !important;">{_esc(totals["customer_name"])}</div>
      <div style="white-space:pre-line;color:#222222 !important;">{_esc(totals["billing_address"])}</div>
    </div>
    <div>
      <div style="font-weight:700;color:#000000 !important;margin-bottom:4px;font-size:13px;">Shipping Address</div>
      <div style="color:#222222 !important;">{_esc(totals["customer_name"])}</div>
      <div style="white-space:pre-line;color:#222222 !important;">{_esc(totals["shipping_address"])}</div>
    </div>
    <div>
      <div style="font-weight:700;color:#000000 !important;margin-bottom:4px;font-size:13px;">Contact</div>
      <div style="color:#222222 !important;">{_esc(totals["contact_name"])}</div>
      {f'<div style="color:#222222 !important;">{_esc(totals["contact_phone"])}</div>' if totals["contact_phone"] else ''}
    </div>
    <div>
      <div style="font-weight:700;color:#000000 !important;margin-bottom:4px;font-size:13px;">FTL Executive</div>
      <div style="color:#222222 !important;">{_esc(totals["bdm"])}</div>
    </div>
  </div>

  <!-- Line Items Table -->
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:28px;">
    <thead>
      <tr style="border-bottom:1px solid #111111;">
        <th style="padding:8px 8px 8px 0;text-align:left;font-weight:700;color:#000000 !important;font-size:13px;">Product</th>
        <th style="padding:8px 8px;text-align:left;font-weight:700;color:#000000 !important;font-size:13px;">Description</th>
        <th style="padding:8px 8px;text-align:center;font-weight:700;color:#000000 !important;font-size:13px;">Quantity</th>
        <th style="padding:8px 8px;text-align:right;font-weight:700;color:#000000 !important;font-size:13px;">Unit Price</th>
        <th style="padding:8px 0 8px 8px;text-align:right;font-weight:700;color:#000000 !important;font-size:13px;">Subtotal</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html) if rows_html else '<tr><td colspan="5" style="padding:20px;text-align:center;color:#888;">No line items.</td></tr>'}
    </tbody>
  </table>

  <!-- Bottom Section: Terms & Boilerplate (Left) | Navy Totals Box (Right) -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:32px;margin-top:12px;">
    <div style="flex:1.4;font-size:12px;color:#222222 !important;line-height:1.5;">
      <div><strong style="color:#000000 !important;">Delivery:</strong> Lead times confirmed at order.</div>
      <div style="margin-top:3px;"><strong style="color:#000000 !important;">Exclusions:</strong> Final specs/quantities to be confirmed prior to order and are Final.</div>
      <div style="margin-top:3px;"><strong style="color:#000000 !important;">Warranty:</strong> Manufacturer warranties only. No additional implied warranties. Limited to replacement or refund of defective supplied materials.</div>
      <div style="margin-top:16px;font-weight:700;font-size:12.5px;color:#000000 !important;">{_esc(estimate_number)}</div>
    </div>
    
    <!-- Signature inFlow Navy Totals Box with Explicit White Text -->
    <div style="width:230px;min-width:210px;">
      <div style="background-color:#13253b !important;color:#ffffff !important;border-radius:3px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.15);">
        <div style="padding:12px 14px 8px;background-color:#13253b !important;">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px;color:#ffffff !important;">
            <span style="color:#ffffff !important;">Subtotal</span>
            <span style="color:#ffffff !important;font-weight:600;">{_esc(_money(totals["subtotal"]))}</span>
          </div>
          {freight_row_html}
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:13px;color:#ffffff !important;">
            <span style="color:#ffffff !important;">HST</span>
            <span style="color:#ffffff !important;font-weight:600;">{_esc(_money(totals["hst"]))}</span>
          </div>
        </div>
        <div style="background-color:#091626 !important;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;font-size:15px;border-top:1px solid rgba(255,255,255,0.12);color:#ffffff !important;">
          <span style="color:#ffffff !important;font-weight:700;">Total</span>
          <span style="color:#ffffff !important;font-weight:700;font-size:16px;letter-spacing:0.3px;">{_esc(_money(totals["total"]))}</span>
        </div>
      </div>
    </div>
  </div>

</div>
"""


# ---------------------------------------------------------------------------
# Template 2: Internal Engineering / Review Estimate
# ---------------------------------------------------------------------------


def render_internal_quote_html(quote: Dict[str, Any], estimate_number: str, date_str: str | None = None) -> str:
    """Renders the detailed Internal Review estimate HTML fragment containing engineering
    review tags, item notes, Remarks box, and yellow assumptions section."""
    totals = compute_totals(quote)
    d = date.today()
    date_str = date_str or f"{d.strftime('%b')} {d.day}, {d.year}"

    rows_html = []
    for i, item in enumerate(totals["line_items"], start=1):
        flag = (
            ' <span style="color:#b30000;font-weight:600;">■ needs engineering review</span>'
            if item["needs_engineering_review"]
            else ""
        )
        note = f'<div style="color:#555;font-size:12px;margin-top:2px;">{_esc(item["note"])}{flag}</div>' if (item["note"] or flag) else flag
        rows_html.append(
            f"""
            <tr>
              <td style="padding:8px 6px;border-bottom:1px solid #e2e2e2;text-align:center;vertical-align:top;color:#555;">{i}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #e2e2e2;vertical-align:top;">
                <div style="font-weight:700;">{_esc(item["product_code"])}</div>
                <div style="color:#333;">{_esc(item["description"])}</div>
                {note}
              </td>
              <td style="padding:8px 6px;border-bottom:1px solid #e2e2e2;text-align:center;vertical-align:top;">{item["qty"]:g}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #e2e2e2;text-align:right;vertical-align:top;">{_esc(_money(item["unit_price"]))}</td>
              <td style="padding:8px 6px;border-bottom:1px solid #e2e2e2;text-align:right;vertical-align:top;font-weight:600;">{_esc(_money(item["subtotal"]))}</td>
            </tr>"""
        )

    assumptions_html = ""
    if totals["assumptions"]:
        items = "".join(f"<li>{_esc(a)}</li>" for a in totals["assumptions"])
        assumptions_html = f"""
        <div style="margin-top:18px;padding:12px 16px;background:#fff8e1;border:1px solid #f0d98c;border-radius:6px;">
          <div style="font-weight:700;color:#7a5c00;margin-bottom:6px;">Internal review notes (not part of the customer-facing estimate)</div>
          <ul style="margin:0;padding-left:18px;color:#5c4600;line-height:1.5;">{items}</ul>
        </div>"""

    freight_note_html = (
        f'<div style="font-size:11px;color:#777;margin-top:2px;">{_esc(totals["freight_note"])}</div>'
        if totals["freight_note"]
        else ""
    )

    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;color:#222;max-width:900px;margin:0 auto;background:#fff;padding:28px;border:1px solid #ddd;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1f3a5f;padding-bottom:14px;margin-bottom:18px;">
    <div>
      <div style="font-size:20px;font-weight:800;color:#1f3a5f;">FTL Distribution Inc.</div>
      <div style="font-size:12px;color:#666;">info@ftl-distribution.ca</div>
      <div style="font-size:11px;color:#999;margin-top:2px;">8-12555 Coleraine Drive, Bolton, Ontario L7E 0P6</div>
      <div style="font-size:11px;color:#1f3a5f;font-style:italic;margin-top:4px;">Your Lift, Our Parts – Elevate With FTL</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:18px;font-weight:800;color:#1f3a5f;">Sales Estimate</div>
      <div style="font-size:13px;margin-top:4px;">Estimate No. <strong>{_esc(estimate_number)}</strong></div>
      <div style="font-size:13px;">Date {_esc(date_str)}</div>
    </div>
  </div>

  <div style="display:flex;gap:24px;margin-bottom:18px;font-size:12.5px;flex-wrap:wrap;">
    <div style="flex:1;min-width:180px;">
      <div style="font-weight:700;color:#1f3a5f;margin-bottom:4px;">Billing Address</div>
      <div>{_esc(totals["customer_name"])}</div>
      <div style="white-space:pre-line;">{_esc(totals["billing_address"])}</div>
    </div>
    <div style="flex:1;min-width:180px;">
      <div style="font-weight:700;color:#1f3a5f;margin-bottom:4px;">Shipping Address</div>
      <div>{_esc(totals["customer_name"])}</div>
      <div style="white-space:pre-line;">{_esc(totals["shipping_address"])}</div>
    </div>
    <div style="flex:1;min-width:160px;">
      <div style="font-weight:700;color:#1f3a5f;margin-bottom:4px;">Contact</div>
      <div>{_esc(totals["contact_name"])}</div>
      <div>{_esc(totals["contact_phone"])}</div>
      <div style="font-weight:700;color:#1f3a5f;margin-top:8px;margin-bottom:4px;">FTL BDM</div>
      <div>{_esc(totals["bdm"])}</div>
    </div>
    {(
        '<div style="flex:1;min-width:200px;">'
        '<div style="font-weight:700;color:#1f3a5f;margin-bottom:4px;">Payment Terms</div>'
        f'<div>{_esc(totals["payment_terms"])}</div>'
        '</div>'
    ) if totals["payment_terms"] else ""}
  </div>

  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px;">
    <thead>
      <tr style="background-color:#1f3a5f !important;color:#ffffff !important;">
        <th style="padding:8px 6px;text-align:center;width:32px;background-color:#1f3a5f !important;color:#ffffff !important;">#</th>
        <th style="padding:8px 6px;text-align:left;background-color:#1f3a5f !important;color:#ffffff !important;">Product</th>
        <th style="padding:8px 6px;text-align:center;width:60px;background-color:#1f3a5f !important;color:#ffffff !important;">Qty</th>
        <th style="padding:8px 6px;text-align:right;width:100px;background-color:#1f3a5f !important;color:#ffffff !important;">Price</th>
        <th style="padding:8px 6px;text-align:right;width:110px;background-color:#1f3a5f !important;color:#ffffff !important;">Subtotal</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html) if rows_html else '<tr><td colspan="5" style="padding:14px;text-align:center;color:#999;">No line items.</td></tr>'}
    </tbody>
  </table>

  <div style="display:flex;justify-content:space-between;gap:24px;margin-top:18px;flex-wrap:wrap;">
    <div style="flex:1.4;min-width:260px;font-size:11px;color:#666;line-height:1.5;">
      {_esc(_BOILERPLATE_INTERNAL)}
    </div>
    <div style="flex:1;min-width:220px;">
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <tr><td style="padding:4px 0;color:#555;">Subtotal</td><td style="padding:4px 0;text-align:right;">{_esc(_money(totals["subtotal"]))}</td></tr>
        <tr><td style="padding:4px 0;color:#555;">Freight{freight_note_html}</td><td style="padding:4px 0;text-align:right;vertical-align:top;">{_esc(_money(totals["freight_estimate"]))}</td></tr>
        <tr><td style="padding:4px 0;color:#555;">HST (13%)</td><td style="padding:4px 0;text-align:right;">{_esc(_money(totals["hst"]))}</td></tr>
        <tr style="border-top:2px solid #1f3a5f;"><td style="padding:8px 0 0;font-weight:800;color:#1f3a5f;">Total</td><td style="padding:8px 0 0;text-align:right;font-weight:800;color:#1f3a5f;font-size:15px;">{_esc(_money(totals["total"]))}</td></tr>
      </table>
    </div>
  </div>

  <div style="margin-top:18px;padding:12px 16px;background:#f4f6f9;border:1px solid #dde3ec;border-radius:6px;">
    <div style="font-weight:700;color:#1f3a5f;margin-bottom:6px;">Remarks</div>
    <div style="white-space:pre-line;font-size:12.5px;color:#333;">{_esc(totals["project_name"])}{chr(10) if totals["project_name"] and totals["remarks"] else ""}{_esc(totals["remarks"])}</div>
  </div>

  {assumptions_html}
</div>
"""


def render_quote_html(
    quote: Dict[str, Any], estimate_number: str, date_str: str | None = None, template_type: str = "inflow"
) -> str:
    """Renders quote HTML fragment with the chosen template ('inflow' or 'internal'/'internal_review'). Defaults to 'inflow'."""
    norm = str(template_type or "inflow").strip().lower()
    if norm in ("internal", "internal_review"):
        return render_internal_quote_html(quote, estimate_number, date_str)
    return render_inflow_quote_html(quote, estimate_number, date_str)
