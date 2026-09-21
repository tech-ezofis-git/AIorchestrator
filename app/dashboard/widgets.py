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
    WidgetDef("average_invoice", "kpi", "AVERAGE INVOICE", None, None, ("amount",)),
    WidgetDef("overdue_count", "kpi", "OVERDUE COUNT", None, None, ("due",)),
    WidgetDef("overdue_pct", "kpi", "OVERDUE %", None, None, ("amount", "due")),
    WidgetDef("current_ap", "kpi", "CURRENT AP", None, None, ("amount", "due")),
    WidgetDef("due_in_30", "kpi", "DUE IN 30 DAYS", None, None, ("amount", "due")),
    WidgetDef("supplier_count", "kpi", "SUPPLIERS", None, None, ("supplier",)),
    WidgetDef("unmatched_count", "kpi", "UNMATCHED", None, None, ("match",)),
    WidgetDef("dpo", "kpi", "DPO", None, None, ("amount", "paid"), default_enabled=False),
    WidgetDef(
        "supplier_risk",
        "chart",
        "Supplier Risk Radar",
        "Supplier Risk Radar",
        "radar",
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
        "area",
        ("amount", "invoice_date"),
    ),
    WidgetDef(
        "ap_aging",
        "chart",
        "AP aging",
        "AP aging",
        "heatmap",
        ("amount", "due"),
    ),
    WidgetDef(
        "top_suppliers",
        "chart",
        "Top suppliers",
        "Top suppliers",
        "lollipop",
        ("amount", "supplier"),
    ),
    WidgetDef(
        "overdue_by_supplier",
        "chart",
        "Overdue by supplier",
        "Overdue by supplier",
        "lollipop",
        ("amount", "due", "supplier"),
    ),
    WidgetDef(
        "invoices_by_status",
        "chart",
        "Invoices by status",
        "Invoices by status",
        "pie",
        ("status",),
    ),
    WidgetDef(
        "invoice_count_by_month",
        "chart",
        "Invoice count by month",
        "Invoice count by month",
        "line",
        ("invoice_date",),
    ),
    WidgetDef(
        "currency_mix",
        "chart",
        "Currency mix",
        "Currency mix",
        "donut",
        ("currency",),
    ),
    WidgetDef(
        "matched_vs_unmatched",
        "chart",
        "Matched vs unmatched",
        "Matched vs unmatched",
        "gauge",
        ("match",),
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


_FAKE_AGING_KPI = re.compile(
    r"1\s*[-to]+\s*30|31\s*[-to]+\s*60|61\s*[-to]+\s*90|90\+|over\s*90|days overdue",
    re.I,
)
_FILE_SIZE_NORMS = {"filesize", "size", "contentlength"}


def _item_blob(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("id", "label", "title")).lower()


def _ensure_cols(item: dict[str, Any]) -> dict[str, str]:
    cols = item.get("columns") if isinstance(item.get("columns"), dict) else {}
    item["columns"] = {str(key): str(value) for key, value in cols.items() if value}
    return item["columns"]


def repair_live_spec(
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    columns: list[str],
) -> None:
    """Rewrite model widgets so values come from real items-table columns, not fake buckets."""
    bound = bind_columns(columns)
    amount = bound.get("amount")
    due = bound.get("due")
    match = bound.get("match")
    paid = bound.get("paid")
    supplier = bound.get("supplier")

    def attach_money(item: dict[str, Any]) -> None:
        cols = _ensure_cols(item)
        value = cols.get("value") or cols.get("amount")
        if amount and (not value or _norm(value) in _FILE_SIZE_NORMS):
            cols["value"] = amount
        if match and not cols.get("match"):
            cols["match"] = match
        if paid and not cols.get("paid"):
            cols["paid"] = paid
        if due and str(item.get("agg") or "").startswith("overdue") and not cols.get("date"):
            cols["date"] = due
        item["columns"] = cols

    kept: list[dict[str, Any]] = []
    had_fake_aging = False
    for kpi in kpis:
        if _FAKE_AGING_KPI.search(_item_blob(kpi)):
            had_fake_aging = True
            continue
        kept.append(kpi)
    kpis[:] = kept

    for chart in charts:
        if "aging" in _item_blob(chart):
            chart["grain"] = "aging"
    if had_fake_aging and due and amount:
        if not any(str(item.get("grain") or "") == "aging" for item in charts):
            cols: dict[str, str] = {"group": due, "value": amount}
            if match:
                cols["match"] = match
            charts.append(
                {
                    "id": "payable_aging",
                    "label": "Payable Aging",
                    "title": "Payable Aging",
                    "description": "Invoice amounts by days past due from DueDate.",
                    "type": "column",
                    "enabled": True,
                    "agg": "sum",
                    "grain": "aging",
                    "columns": cols,
                    "position": "right",
                    "span": 1,
                }
            )

    for kpi in kpis:
        attach_money(kpi)
        text = _item_blob(kpi)
        agg = str(kpi.get("agg") or "count").lower()
        if "outstanding" in text and agg == "count":
            kpi["agg"] = "outstanding_count"
        if re.search(r"\bcurrent\b", text) and agg in {"overdue_sum", "outstanding_sum", "sum", "count"}:
            if amount:
                kpi["agg"] = "current_sum"
        if "overdue" in text and agg == "count":
            kpi["agg"] = "overdue_count"

    for chart in charts:
        attach_money(chart)
        text = _item_blob(chart)
        if "radar" in text or ("supplier" in text and "risk" in text):
            chart["type"] = "radar"
            cols = _ensure_cols(chart)
            if supplier:
                cols["group"] = supplier
            if amount:
                cols["value"] = amount
            if match:
                cols["match"] = match
                chart["agg"] = "outstanding_sum"
            elif amount:
                chart["agg"] = "sum"
            chart["grain"] = "none"
            chart["columns"] = cols
            if not str(chart.get("title") or "").strip():
                chart["title"] = "Supplier Risk Radar"
        if "payment" in text:
            chart["grain"] = "payment"
            cols = _ensure_cols(chart)
            if amount:
                cols["value"] = amount
            if match:
                cols["match"] = match
            if str(chart.get("agg") or "count") == "count":
                chart["agg"] = "sum"
            chart["columns"] = cols
        if str(chart.get("grain") or "") == "aging" and due:
            cols = _ensure_cols(chart)
            cols["group"] = due
            if amount:
                cols["value"] = amount
            chart["columns"] = cols


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


def _filled_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


_STATUS_GROUP_ALIASES = (
    "aistatus",
    "status",
    "invoicestatus",
    "workflowstatus",
    "matchedstatus",
    "matchstatus",
)
_PAYMENT_GROUP_ALIASES = (
    "matchedstatus",
    "matchstatus",
    "paymentstatus",
    "aistatus",
    "status",
)
_VENDOR_GROUP_ALIASES = ("supplier", "vendor", "vendorname", "suppliername")


def _row_column_names(rows: list[Any]) -> list[str]:
    for row in rows:
        if isinstance(row, dict) and row:
            return [str(key) for key in row.keys()]
        try:
            mapping = dict(row)
        except Exception:
            continue
        if mapping:
            return [str(key) for key in mapping.keys()]
    return []


def _columns_for_aliases(rows: list[Any], aliases: tuple[str, ...]) -> list[str]:
    by_norm = {_norm(name): name for name in _row_column_names(rows)}
    return [by_norm[alias] for alias in aliases if alias in by_norm]


def _populated_ratio(rows: list[Any], column: Optional[str]) -> float:
    if not rows or not column:
        return 0.0
    filled = sum(1 for row in rows if _filled_label(_row_get(row, column)))
    return filled / len(rows)


def _group_role(item: dict[str, Any]) -> Optional[str]:
    blob = " ".join(str(item.get(key) or "") for key in ("id", "title", "label")).lower()
    if any(word in blob for word in ("vendor", "supplier")):
        return "vendor"
    if any(word in blob for word in ("payment", "match", "matched")):
        return "payment"
    if "status" in blob:
        return "status"
    return None


def resolve_group_column(
    rows: list[Any],
    preferred: Optional[str],
    item: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Use a sibling column when the bound group field is empty on most rows."""
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    role = _group_role(item or {})
    aliases = {
        "vendor": _VENDOR_GROUP_ALIASES,
        "payment": _PAYMENT_GROUP_ALIASES,
        "status": _STATUS_GROUP_ALIASES,
    }.get(role or "", ())
    for column in _columns_for_aliases(rows, aliases):
        if column not in candidates:
            candidates.append(column)
    if not candidates:
        return preferred
    if preferred and _populated_ratio(rows, preferred) >= 0.3:
        return preferred
    best = max(candidates, key=lambda column: _populated_ratio(rows, column))
    if _populated_ratio(rows, best) > 0:
        return best
    return preferred


def rebind_sparse_group_columns(charts: list[dict[str, Any]], rows: list[Any]) -> None:
    """Point chart group columns at fields that actually have values in `rows`."""
    if not charts or not rows:
        return
    for item in charts:
        cols = item.get("columns") if isinstance(item.get("columns"), dict) else {}
        preferred = cols.get("group") or cols.get("category")
        resolved = resolve_group_column(rows, preferred, item)
        if resolved:
            cols = dict(cols)
            cols["group"] = resolved
            item["columns"] = cols


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


def _is_unmatched(label: str) -> bool:
    text = (label or "").strip().lower()
    if not text or text in {"unknown", "n/a", "na", "none"}:
        return True
    if "not match" in text or text.startswith("unmatch") or text in {"mismatch", "open"}:
        return True
    return False


def _is_paid_status(label: Optional[str]) -> bool:
    text = (label or "").strip().lower()
    if not text:
        return False
    if any(token in text for token in ("not match", "unmatch", "partial", "pending", "open", "unpaid", "outstanding")):
        return False
    return any(token in text for token in ("matched", "approved", "paid", "posted", "settled", "closed"))


def _row_is_paid(row: Any, match_col: Optional[str], paid_col: Optional[str]) -> bool:
    if paid_col and parse_date(_row_get(row, paid_col)):
        return True
    if not match_col:
        return False
    return _is_paid_status(_filled_label(_row_get(row, match_col)))


def _aging_bucket(due: date, today: date) -> str:
    delta = (today - due).days
    if delta <= 0:
        return "Current"
    if delta <= 30:
        return "1-30"
    if delta <= 60:
        return "31-60"
    if delta <= 90:
        return "61-90"
    return "90+"


def _month_sort_key(label: str) -> tuple:
    try:
        return (datetime.strptime(label, "%b %Y"), label)
    except ValueError:
        return (datetime.min, label)


def _num(value: Any) -> float | int:
    if isinstance(value, float):
        return round(value, 2)
    return value


def _bar_chart(
    categories: list[str],
    values: list[float],
    *,
    kind: str = "bar",
    palette: str = "mix",
) -> dict[str, Any]:
    rounded = [round(value, 2) for value in values]
    return {
        "type": kind,
        "palette": palette,
        "categories": categories,
        "bars": rounded,
        "values": rounded,
    }


def _donut_chart(
    pairs: list[tuple[str, Any]],
    *,
    kind: str = "donut",
    palette: str = "mix",
) -> dict[str, Any]:
    return {
        "type": kind,
        "palette": palette,
        "series": [{"name": name, "value": _num(value)} for name, value in pairs],
    }


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
    status_col = bound.get("status")

    amounts: list[float] = []
    overdue_total = 0.0
    current_total = 0.0
    due_30_total = 0.0
    overdue_count = 0
    unmatched_count = 0
    suppliers: set[str] = set()
    currencies: list[str] = []
    by_supplier: dict[str, float] = {}
    overdue_by_supplier: dict[str, float] = {}
    by_match: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_currency: dict[str, float] = {}
    by_month: dict[str, float] = {}
    count_by_month: dict[str, int] = {}
    by_aging: dict[str, float] = {key: 0.0 for key in ("Current", "1-30", "31-60", "61-90", "90+")}
    matched_split = {"Matched": 0, "Unmatched": 0}
    horizon = today + timedelta(days=30)

    for row in rows:
        amount = parse_amount(_row_get(row, amount_col)) if amount_col else None
        if amount is not None:
            amounts.append(amount)
        due = parse_date(_row_get(row, due_col)) if due_col else None
        supplier = _filled_label(_row_get(row, supplier_col)) if supplier_col else None
        if supplier:
            suppliers.add(supplier)
        if amount is not None and due is not None and due < today:
            overdue_total += amount
            overdue_count += 1
            if supplier:
                overdue_by_supplier[supplier] = overdue_by_supplier.get(supplier, 0.0) + amount
        if amount is not None and (due is None or due >= today):
            current_total += amount
        if amount is not None and due is not None and today <= due <= horizon:
            due_30_total += amount
        if due is not None and amount is not None:
            by_aging[_aging_bucket(due, today)] += amount
        if currency_col:
            currency = _row_get(row, currency_col)
            if currency:
                currencies.append(str(currency))
                by_currency[str(currency)] = by_currency.get(str(currency), 0.0) + (amount or 0.0)
        if supplier and amount is not None:
            by_supplier[supplier] = by_supplier.get(supplier, 0.0) + amount
        if match_col:
            label = _filled_label(_row_get(row, match_col))
            if label:
                by_match[label] = by_match.get(label, 0) + 1
                if _is_unmatched(label):
                    unmatched_count += 1
                    matched_split["Unmatched"] += 1
                else:
                    matched_split["Matched"] += 1
        if status_col:
            status = _filled_label(_row_get(row, status_col))
            if not status and match_col:
                status = _filled_label(_row_get(row, match_col))
            if status:
                by_status[status] = by_status.get(status, 0) + 1
        invoice_date = parse_date(_row_get(row, date_col)) if date_col else None
        if invoice_date is not None:
            key = invoice_date.strftime("%b %Y")
            count_by_month[key] = count_by_month.get(key, 0) + 1
            if amount is not None:
                by_month[key] = by_month.get(key, 0.0) + amount

    unit = currencies[0] if currencies else "USD"
    total_ap = sum(amounts)
    enabled_kpis = _enabled_ids(kpis)
    enabled_charts = _enabled_ids(charts)

    kpi_data: dict[str, Any] = {}
    if "total_ap" in enabled_kpis:
        kpi_data["total_ap"] = {"value": round(total_ap, 2), "unit": unit}
    if "overdue" in enabled_kpis:
        kpi_data["overdue"] = {"value": round(overdue_total, 2), "unit": unit, "alert": overdue_total > 0}
    if "open_invoices" in enabled_kpis:
        kpi_data["open_invoices"] = {"value": len(rows)}
    if "average_invoice" in enabled_kpis:
        avg = (total_ap / len(amounts)) if amounts else 0.0
        kpi_data["average_invoice"] = {"value": round(avg, 2), "unit": unit}
    if "overdue_count" in enabled_kpis:
        kpi_data["overdue_count"] = {"value": overdue_count, "alert": overdue_count > 0}
    if "overdue_pct" in enabled_kpis:
        pct = (overdue_total / total_ap * 100) if total_ap else 0.0
        kpi_data["overdue_pct"] = {"value": round(pct, 1), "unit": "%", "alert": pct > 0}
    if "current_ap" in enabled_kpis:
        kpi_data["current_ap"] = {"value": round(current_total, 2), "unit": unit}
    if "due_in_30" in enabled_kpis:
        kpi_data["due_in_30"] = {"value": round(due_30_total, 2), "unit": unit}
    if "supplier_count" in enabled_kpis:
        kpi_data["supplier_count"] = {"value": len(suppliers)}
    if "unmatched_count" in enabled_kpis:
        kpi_data["unmatched_count"] = {"value": unmatched_count, "alert": unmatched_count > 0}
    if "dpo" in enabled_kpis:
        kpi_data["dpo"] = {"value": 0, "unit": "days"}

    chart_data: dict[str, Any] = {}
    supplier_ranked = sorted(by_supplier.items(), key=lambda item: item[1], reverse=True)
    if "supplier_risk" in enabled_charts:
        chart_data["supplier_risk"] = _donut_chart(
            [(name, value) for name, value in supplier_ranked[:6]],
            kind="radar",
            palette="mix",
        )
    if "match_status" in enabled_charts:
        chart_data["match_status"] = _donut_chart(
            sorted(by_match.items(), key=lambda item: item[1], reverse=True),
            kind="donut",
            palette="status",
        )
    if "profit_vs_ap" in enabled_charts:
        categories = sorted(by_month.keys(), key=_month_sort_key)
        values = [round(by_month[key], 2) for key in categories]
        avg = round(sum(values) / len(values), 2) if values else 0.0
        chart_data["profit_vs_ap"] = {
            "type": "area",
            "palette": "ocean",
            "categories": categories,
            "bars": values,
            "values": values,
            "line": [avg for _ in categories],
        }
    if "ap_aging" in enabled_charts:
        aging_keys = ["Current", "1-30", "31-60", "61-90", "90+"]
        chart_data["ap_aging"] = _bar_chart(
            aging_keys, [by_aging[key] for key in aging_keys], kind="heatmap", palette="heat"
        )
    if "top_suppliers" in enabled_charts:
        top = supplier_ranked[:8]
        chart_data["top_suppliers"] = _bar_chart(
            [name for name, _ in top], [value for _, value in top], kind="lollipop", palette="ocean"
        )
    if "overdue_by_supplier" in enabled_charts:
        overdue_ranked = sorted(overdue_by_supplier.items(), key=lambda item: item[1], reverse=True)[:8]
        chart_data["overdue_by_supplier"] = _bar_chart(
            [name for name, _ in overdue_ranked],
            [value for _, value in overdue_ranked],
            kind="lollipop",
            palette="danger",
        )
    if "invoices_by_status" in enabled_charts:
        chart_data["invoices_by_status"] = _donut_chart(
            sorted(by_status.items(), key=lambda item: item[1], reverse=True),
            kind="pie",
            palette="forest",
        )
    if "invoice_count_by_month" in enabled_charts:
        categories = sorted(count_by_month.keys(), key=_month_sort_key)
        chart_data["invoice_count_by_month"] = _bar_chart(
            categories,
            [float(count_by_month[key]) for key in categories],
            kind="line",
            palette="sunset",
        )
    if "currency_mix" in enabled_charts:
        chart_data["currency_mix"] = _donut_chart(
            sorted(by_currency.items(), key=lambda item: item[1], reverse=True),
            kind="donut",
            palette="mix",
        )
    if "matched_vs_unmatched" in enabled_charts:
        chart_data["matched_vs_unmatched"] = _donut_chart(
            list(matched_split.items()),
            kind="gauge",
            palette="split",
        )

    return {"kpis": kpi_data, "charts": chart_data}


def spec_has_agg(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> bool:
    return any(str(item.get("agg") or "").strip() for item in list(kpis or []) + list(charts or []))


_DONUT_TYPES = {"donut", "pie", "radar", "gauge"}
_BAR_TYPES = {"column", "bar", "lollipop", "line", "area", "heatmap", "hbar"}


def hydrate_from_spec(
    *,
    rows: list[Any],
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Aggregate rows using per-widget agg/column specs (LLM or generic fallback)."""
    today = today or date.today()
    kpi_data: dict[str, Any] = {}
    for item in kpis:
        if item.get("enabled") is False:
            continue
        widget_id = str(item.get("id") or "").strip()
        if not widget_id:
            continue
        kpi_data[widget_id] = _hydrate_kpi_spec(rows, item, today)
    chart_data: dict[str, Any] = {}
    for item in charts:
        if item.get("enabled") is False:
            continue
        widget_id = str(item.get("id") or "").strip()
        if not widget_id:
            continue
        chart_data[widget_id] = _hydrate_chart_spec(rows, item, today)
    return {"kpis": kpi_data, "charts": chart_data}


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _trend_date_column(rows: list[Any], kpis: list[dict[str, Any]]) -> Optional[str]:
    for item in kpis:
        cols = _spec_cols(item)
        for key in ("date", "due", "date_column"):
            if cols.get(key):
                return cols[key]
    sample = next((row for row in rows if isinstance(row, dict)), None)
    if not sample:
        return None
    hints = ("invoicedate", "createdatutc", "createdat", "docdate", "duedate", "modifiedatutc")
    for key in sample:
        norm = _norm(str(key))
        if any(hint in norm for hint in hints):
            return str(key)
    return None


def attach_kpi_trends(
    *,
    rows: list[Any],
    kpis: list[dict[str, Any]],
    kpi_data: dict[str, Any],
    today: Optional[date] = None,
) -> None:
    """Add vs-last-month % onto hydrated KPI values (data HTML only)."""
    today = today or date.today()
    date_col = _trend_date_column(rows, kpis)
    if not date_col or not kpi_data:
        return
    dated = [row for row in rows if parse_date(_row_get(row, date_col)) is not None]
    if len(dated) < 10:
        return
    this_start = _month_start(today)
    prev_start = date(this_start.year - 1, 12, 1) if this_start.month == 1 else date(this_start.year, this_start.month - 1, 1)
    next_start = _add_month(this_start)
    this_rows = []
    prev_rows = []
    for row in rows:
        parsed = parse_date(_row_get(row, date_col))
        if parsed is None:
            continue
        if this_start <= parsed < next_start:
            this_rows.append(row)
        elif prev_start <= parsed < this_start:
            prev_rows.append(row)
    if not prev_rows:
        return
    for item in kpis:
        if item.get("enabled") is False:
            continue
        widget_id = str(item.get("id") or "")
        slot = kpi_data.get(widget_id)
        if not isinstance(slot, dict):
            continue
        current = _hydrate_kpi_spec(this_rows, item, today)
        previous = _hydrate_kpi_spec(prev_rows, item, today)
        try:
            cur_v = float(current.get("value") or 0)
            prev_v = float(previous.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if prev_v == 0:
            pct = 100.0 if cur_v else 0.0
        else:
            pct = round((cur_v - prev_v) / abs(prev_v) * 100.0, 1)
        slot["trend_pct"] = pct
        sign = "+" if pct > 0 else ""
        slot["subtext"] = f"{sign}{pct:g}% vs last month"


def _spec_cols(item: dict[str, Any]) -> dict[str, str]:
    cols = item.get("columns") if isinstance(item.get("columns"), dict) else {}
    return {str(key): str(value) for key, value in cols.items() if value}


def _amount_at(row: Any, column: Optional[str]) -> Optional[float]:
    if not column:
        return None
    return parse_amount(_row_get(row, column))


def _role_column(
    rows: list[Any],
    cols: dict[str, str],
    key: str,
    aliases: tuple[str, ...],
) -> Optional[str]:
    if cols.get(key):
        return cols[key]
    found = _columns_for_aliases(rows, aliases)
    return found[0] if found else None


def _hydrate_kpi_spec(rows: list[Any], item: dict[str, Any], today: date) -> dict[str, Any]:
    cols = _spec_cols(item)
    agg = str(item.get("agg") or "count").lower()
    value_col = cols.get("value") or cols.get("amount") or cols.get("column")
    date_col = cols.get("date") or cols.get("due") or cols.get("date_column") or _role_column(
        rows, cols, "due", DUE_ALIASES
    )
    match_col = _role_column(rows, cols, "match", MATCH_ALIASES)
    paid_col = _role_column(rows, cols, "paid", PAID_ALIASES)
    if agg == "sum":
        total = sum(v for v in (_amount_at(row, value_col) for row in rows) if v is not None)
        return {"value": round(total, 2)}
    if agg == "avg":
        vals = [v for v in (_amount_at(row, value_col) for row in rows) if v is not None]
        avg = (sum(vals) / len(vals)) if vals else 0.0
        return {"value": round(avg, 2)}
    if agg == "distinct":
        names = {str(_row_get(row, value_col) or "").strip() for row in rows}
        names.discard("")
        return {"value": len(names)}
    if agg in {"paid_sum", "outstanding_sum"}:
        total = 0.0
        want_paid = agg == "paid_sum"
        for row in rows:
            amount = _amount_at(row, value_col)
            if amount is None:
                continue
            if _row_is_paid(row, match_col, paid_col) == want_paid:
                total += amount
        return {"value": round(total, 2), "alert": total > 0 and not want_paid}
    if agg == "current_sum":
        total = 0.0
        for row in rows:
            amount = _amount_at(row, value_col)
            if amount is None or _row_is_paid(row, match_col, paid_col):
                continue
            due = parse_date(_row_get(row, date_col)) if date_col else None
            if due is not None and due < today:
                continue
            total += amount
        return {"value": round(total, 2)}
    if agg == "outstanding_count":
        count = sum(1 for row in rows if not _row_is_paid(row, match_col, paid_col))
        return {"value": count, "alert": count > 0}
    if agg in {"overdue_sum", "overdue"}:
        total = 0.0
        for row in rows:
            due = parse_date(_row_get(row, date_col)) if date_col else None
            amount = _amount_at(row, value_col)
            if due is None or amount is None or due >= today:
                continue
            if _row_is_paid(row, match_col, paid_col):
                continue
            total += amount
        return {"value": round(total, 2), "alert": total > 0}
    if agg == "overdue_count":
        count = 0
        for row in rows:
            due = parse_date(_row_get(row, date_col)) if date_col else None
            if due is None or due >= today:
                continue
            if _row_is_paid(row, match_col, paid_col):
                continue
            count += 1
        return {"value": count, "alert": count > 0}
    return {"value": len(rows)}


def _hydrate_chart_spec(rows: list[Any], item: dict[str, Any], today: date) -> dict[str, Any]:
    cols = _spec_cols(item)
    group_col = resolve_group_column(rows, cols.get("group") or cols.get("category"), item)
    value_col = cols.get("value") or cols.get("amount")
    agg = str(item.get("agg") or ("sum" if value_col else "count")).lower()
    grain = str(item.get("grain") or "none").lower()
    chart_type = str(item.get("type") or "donut").lower()
    palette = "mix"
    if chart_type in {"heatmap"} or grain == "aging":
        palette = "heat"
    if chart_type in {"lollipop", "area", "line"}:
        palette = "ocean"

    if grain == "aging":
        buckets = {key: 0.0 for key in ("Current", "1-30", "31-60", "61-90", "90+")}
        date_col = group_col or cols.get("date") or cols.get("due") or _role_column(rows, cols, "due", DUE_ALIASES)
        match_col = _role_column(rows, cols, "match", MATCH_ALIASES)
        paid_col = _role_column(rows, cols, "paid", PAID_ALIASES)
        for row in rows:
            if _row_is_paid(row, match_col, paid_col):
                continue
            due = parse_date(_row_get(row, date_col)) if date_col else None
            amount = _amount_at(row, value_col) if value_col else 1.0
            if due is None or amount is None:
                continue
            buckets[_aging_bucket(due, today)] += amount
        keys = ["Current", "1-30", "31-60", "61-90", "90+"]
        kind = chart_type if chart_type in _BAR_TYPES else "column"
        return _bar_chart(keys, [buckets[key] for key in keys], kind=kind, palette=palette)

    if grain == "payment":
        buckets = {"Paid": 0.0, "Outstanding": 0.0}
        match_col = _role_column(rows, cols, "match", MATCH_ALIASES) or group_col
        paid_col = _role_column(rows, cols, "paid", PAID_ALIASES)
        for row in rows:
            amount = _amount_at(row, value_col) if value_col else 1.0
            if amount is None:
                continue
            label = "Paid" if _row_is_paid(row, match_col, paid_col) else "Outstanding"
            buckets[label] += amount
        pairs = [(name, value) for name, value in buckets.items() if value]
        if chart_type in _DONUT_TYPES:
            return _donut_chart(pairs, kind=chart_type, palette=palette)
        kind = chart_type if chart_type in _BAR_TYPES else "column"
        return _bar_chart([name for name, _ in pairs], [value for _, value in pairs], kind=kind, palette=palette)

    grouped: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    match_col = _role_column(rows, cols, "match", MATCH_ALIASES)
    paid_col = _role_column(rows, cols, "paid", PAID_ALIASES)
    outstanding_only = agg in {"outstanding_sum", "outstanding"}
    paid_only = agg in {"paid_sum", "paid"}
    for row in rows:
        if outstanding_only and _row_is_paid(row, match_col, paid_col):
            continue
        if paid_only and not _row_is_paid(row, match_col, paid_col):
            continue
        if grain == "month":
            parsed = parse_date(_row_get(row, group_col)) if group_col else None
            if parsed is None:
                continue
            label = parsed.strftime("%b %Y")
        else:
            label = _filled_label(_row_get(row, group_col)) if group_col else "All"
            if not label:
                continue
        if agg == "count":
            counts[label] = counts.get(label, 0) + 1
        else:
            amount = _amount_at(row, value_col)
            if amount is None:
                continue
            grouped.setdefault(label, []).append(amount)

    pairs: list[tuple[str, float]] = []
    if agg == "count":
        pairs = [(name, float(value)) for name, value in counts.items()]
    elif agg == "avg":
        pairs = [(name, sum(vals) / len(vals)) for name, vals in grouped.items() if vals]
    else:
        pairs = [(name, sum(vals)) for name, vals in grouped.items()]

    if grain == "month":
        pairs.sort(key=lambda item: _month_sort_key(item[0]))
    else:
        pairs.sort(key=lambda item: item[1], reverse=True)
        pairs = pairs[:8]

    if chart_type in _DONUT_TYPES:
        return _donut_chart(pairs, kind=chart_type, palette=palette)
    kind = chart_type if chart_type in _BAR_TYPES else "column"
    return _bar_chart(
        [name for name, _ in pairs],
        [value for _, value in pairs],
        kind=kind,
        palette=palette,
    )


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

