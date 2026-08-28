"""Map items-table columns to proposed KPIs/charts, then aggregate rows."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


AMOUNT_ALIASES = ("invoiceamount", "amount", "total", "invoicetotal", "poamount")
DUE_ALIASES = ("duedate", "paymentdue", "datedue")
SUPPLIER_ALIASES = ("supplier", "vendor", "vendorname", "suppliername")
MATCH_ALIASES = ("matchedstatus", "matchstatus", "matchstate")
INVOICE_DATE_ALIASES = ("invoicedate", "docdate", "documentdate", "podate")
CURRENCY_ALIASES = ("currency", "curr")
STATUS_ALIASES = ("status", "aistatus")
PAID_ALIASES = ("paiddate", "paymentdate", "paidon")


@dataclass(frozen=True)
class WidgetDef:
    id: str
    kind: str  # kpi | chart
    label: str
    title: Optional[str]
    chart_type: Optional[str]
    required: tuple[str, ...]
    default_enabled: bool = True


WIDGETS: tuple[WidgetDef, ...] = (
    WidgetDef("total_ap", "kpi", "TOTAL AP", None, None, ("amount",)),
    WidgetDef("overdue", "kpi", "OVERDUE", None, None, ("amount", "due")),
    WidgetDef("open_invoices", "kpi", "OPEN INVOICES", None, None, ()),
    WidgetDef("dpo", "kpi", "DPO", None, None, ("amount", "paid"), default_enabled=False),
    WidgetDef(
        "supplier_risk",
        "chart",
        "Supplier Risk Radar",
        "Supplier Risk Radar",
        "donut",
        ("amount", "supplier"),
    ),
    WidgetDef(
        "match_status",
        "chart",
        "Match status",
        "Match status",
        "donut",
        ("match",),
    ),
    WidgetDef(
        "profit_vs_ap",
        "chart",
        "Profit vs AP spending",
        "Profit vs AP spending",
        "combo_bar_line",
        ("amount", "invoice_date"),
    ),
)


def bind_columns(column_names: list[str]) -> dict[str, str]:
    """Logical role -> actual column name present on the table."""
    by_norm = {_norm(name): name for name in column_names if name}

    def pick(*aliases: str) -> Optional[str]:
        for alias in aliases:
            if alias in by_norm:
                return by_norm[alias]
        return None

    bound: dict[str, str] = {}
    amount = pick(*AMOUNT_ALIASES)
    due = pick(*DUE_ALIASES)
    supplier = pick(*SUPPLIER_ALIASES)
    match = pick(*MATCH_ALIASES)
    invoice_date = pick(*INVOICE_DATE_ALIASES)
    currency = pick(*CURRENCY_ALIASES)
    status = pick(*STATUS_ALIASES)
    paid = pick(*PAID_ALIASES)
    if amount:
        bound["amount"] = amount
    if due:
        bound["due"] = due
    if supplier:
        bound["supplier"] = supplier
    if match:
        bound["match"] = match
    if invoice_date:
        bound["invoice_date"] = invoice_date
    if currency:
        bound["currency"] = currency
    if status:
        bound["status"] = status
    if paid:
        bound["paid"] = paid
    return bound


def propose_widgets(column_names: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    bound = bind_columns(column_names)
    kpis: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    for widget in WIDGETS:
        if any(role not in bound for role in widget.required):
            continue
        item: dict[str, Any] = {
            "id": widget.id,
            "label": widget.label,
            "enabled": widget.default_enabled,
            "columns": {role: bound[role] for role in widget.required},
        }
        if widget.kind == "kpi":
            kpis.append(item)
        else:
            item["type"] = widget.chart_type
            item["title"] = widget.title
            charts.append(item)
    return kpis, charts, bound


def _row_get(row: Any, column: Optional[str]) -> Any:
    if not column or row is None:
        return None
    if isinstance(row, dict):
        if column in row:
            return row[column]
        wanted = column.lower()
        for key, value in row.items():
            if str(key).lower() == wanted:
                return value
        return None
    try:
        return row[column]
    except Exception:
        try:
            mapping = dict(row)
        except Exception:
            return None
        return _row_get(mapping, column)


def parse_amount(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {".", "-", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%b %d %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


def parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = re.sub(r"\s+", " ", str(value).strip())
    iso = text[:10]
    try:
        return date.fromisoformat(iso)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _enabled_ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id") or "") for item in items if item.get("enabled") is not False}


def hydrate_data(
    *,
    rows: list[Any],
    bound: dict[str, str],
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    today: Optional[date] = None,
) -> dict[str, Any]:
    today = today or date.today()
    amount_col = bound.get("amount")
    due_col = bound.get("due")
    supplier_col = bound.get("supplier")
    match_col = bound.get("match")
    date_col = bound.get("invoice_date")
    currency_col = bound.get("currency")

    amounts: list[float] = []
    overdue_total = 0.0
    currencies: list[str] = []
    by_supplier: dict[str, float] = {}
    by_match: dict[str, int] = {}
    by_month: dict[str, float] = {}

    for row in rows:
        amount = parse_amount(_row_get(row, amount_col)) if amount_col else None
        if amount is not None:
            amounts.append(amount)
        due = parse_date(_row_get(row, due_col)) if due_col else None
        if amount is not None and due is not None and due < today:
            overdue_total += amount
        if currency_col:
            currency = _row_get(row, currency_col)
            if currency:
                currencies.append(str(currency))
        if supplier_col and amount is not None:
            name = str(_row_get(row, supplier_col) or "Unknown")
            by_supplier[name] = by_supplier.get(name, 0.0) + amount
        if match_col:
            label = str(_row_get(row, match_col) or "Unknown")
            by_match[label] = by_match.get(label, 0) + 1
        invoice_date = parse_date(_row_get(row, date_col)) if date_col else None
        if invoice_date is not None and amount is not None:
            key = invoice_date.strftime("%b %Y")
            by_month[key] = by_month.get(key, 0.0) + amount

    unit = currencies[0] if currencies else "USD"
    enabled_kpis = _enabled_ids(kpis)
    enabled_charts = _enabled_ids(charts)

    kpi_data: dict[str, Any] = {}
    if "total_ap" in enabled_kpis:
        kpi_data["total_ap"] = {"value": round(sum(amounts), 2), "unit": unit}
    if "overdue" in enabled_kpis:
        kpi_data["overdue"] = {"value": round(overdue_total, 2), "unit": unit, "alert": overdue_total > 0}
    if "open_invoices" in enabled_kpis:
        kpi_data["open_invoices"] = {"value": len(rows)}
    if "dpo" in enabled_kpis:
        kpi_data["dpo"] = {"value": 0, "unit": "days"}

    chart_data: dict[str, Any] = {}
    if "supplier_risk" in enabled_charts:
        series = [
            {"name": name, "value": round(value, 2)}
            for name, value in sorted(by_supplier.items(), key=lambda item: item[1], reverse=True)
        ]
        chart_data["supplier_risk"] = {"type": "donut", "series": series}
    if "match_status" in enabled_charts:
        series = [
            {"name": name, "value": count}
            for name, count in sorted(by_match.items(), key=lambda item: item[1], reverse=True)
        ]
        chart_data["match_status"] = {"type": "donut", "series": series}
    if "profit_vs_ap" in enabled_charts:
        def _month_sort(label: str) -> tuple:
            try:
                return (datetime.strptime(label, "%b %Y"), label)
            except ValueError:
                return (datetime.min, label)

        categories = sorted(by_month.keys(), key=_month_sort)
        chart_data["profit_vs_ap"] = {
            "type": "combo_bar_line",
            "categories": categories,
            "bars": [round(by_month[key], 2) for key in categories],
            "line": [100 for _ in categories],
        }

    return {"kpis": kpi_data, "charts": chart_data}


def row_id(row: Any) -> str:
    value = _row_get(row, "id") or _row_get(row, "item_key")
    return str(value or "").strip()


def amounts_missing(rows: list[Any], bound: dict[str, str]) -> bool:
    amount_col = bound.get("amount")
    if not amount_col:
        return True
    return not any(parse_amount(_row_get(row, amount_col)) is not None for row in rows)


def _search_ocr(text: str, *patterns: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" :-\t")
    return ""


def _vendor_from_ocr(text: str) -> str:
    named = _search_ocr(text, r"vendor\s*[:\-]\s*([A-Za-z0-9 .,&'\-]{3,80})")
    if named:
        return named
    for line in text.splitlines():
        candidate = line.strip(" -:|")
        if len(candidate) < 6 or len(candidate) > 80:
            continue
        lower = candidate.lower()
        if lower in {"original", "invoice", "bill to", "ship to"} or lower.startswith("page "):
            continue
        if re.search(r"\b(ltd|limited|inc|llc|corp|group|supply|components|solutions)\b", candidate, re.I):
            return candidate
    return ""


def fields_from_extract(result_json: Any) -> dict[str, Any]:
    payload = result_json if isinstance(result_json, dict) else {}
    invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    ocr = str(payload.get("ocr_text") or "")
    amount = parse_amount(invoice.get("total") if invoice.get("total") is not None else invoice.get("amount"))
    if amount is None:
        amount = parse_amount(
            _search_ocr(
                ocr,
                r"invoice\s*total\s*[:\-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
                r"total\s*due\s*[:\-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
                r"\btotal\s*[:\-]?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            )
        )
    vendor = str(invoice.get("vendor") or invoice.get("supplier") or "").strip() or _vendor_from_ocr(ocr)
    invoice_date = parse_date(
        invoice.get("invoice_date")
        or invoice.get("invoiceDate")
        or _search_ocr(
            ocr,
            r"invoice\s*date\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
            r"invoice\s*date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        )
    )
    due = parse_date(invoice.get("due_date") or invoice.get("dueDate"))
    if due is None:
        due_chunk = ""
        match = re.search(r"due\s*date(.{0,160})", ocr, flags=re.IGNORECASE | re.DOTALL)
        if match:
            due_chunk = match.group(1)
        dates = re.findall(r"(\d{1,2}/\d{1,2}/\d{2,4})", due_chunk)
        if dates:
            due = parse_date(dates[-1])
            if invoice_date is None and len(dates) > 1:
                invoice_date = parse_date(dates[0])
    if due is None and invoice_date is not None and re.search(r"net\s+(one\s+month|30)\b", ocr, re.I):
        due = invoice_date + timedelta(days=30)
    currency = str(invoice.get("currency") or "").strip() or _search_ocr(ocr, r"\b(CAD|USD|EUR|GBP)\b")
    return {
        "amount": amount,
        "supplier": vendor or None,
        "due": due,
        "invoice_date": invoice_date,
        "currency": currency or None,
    }


def overlay_extract_artifacts(
    rows: list[Any],
    bound: dict[str, str],
    artifacts_by_item: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Fill empty items-table fields from latest extract_invoice / po_match artifacts."""
    if not artifacts_by_item:
        return [dict(row) if not isinstance(row, dict) else dict(row) for row in rows], False
    used = False
    out: list[dict[str, Any]] = []
    for row in rows:
        mapping = dict(row) if not isinstance(row, dict) else dict(row)
        item_id = row_id(mapping)
        bundle = artifacts_by_item.get(item_id) or {}
        extracted = fields_from_extract(bundle.get("extract_invoice"))
        po_match = bundle.get("po_match") if isinstance(bundle.get("po_match"), dict) else {}
        decision = po_match.get("decision")
        patches = {
            bound.get("amount"): extracted.get("amount"),
            bound.get("supplier"): extracted.get("supplier"),
            bound.get("due"): extracted.get("due"),
            bound.get("invoice_date"): extracted.get("invoice_date"),
            bound.get("currency"): extracted.get("currency"),
            bound.get("match"): decision,
        }
        for column, value in patches.items():
            if not column or value in (None, ""):
                continue
            current = mapping.get(column)
            if current not in (None, ""):
                continue
            mapping[column] = value
            used = True
        out.append(mapping)
    return out, used

