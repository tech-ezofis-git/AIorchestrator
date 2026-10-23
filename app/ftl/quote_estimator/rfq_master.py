"""Fill Billing Address / Shipping Address from the RFQ Master form.

The estimator often only has a signature fragment or a jobsite street. After the
quote is built from the EML, we look up ``dbo.ezfb_7737f3f4_items`` (form name
"RFQ Master") by email, company name, or the fetched address. A matching row
overlays those two quote_result fields. No row — or an empty master address —
keeps the EML values.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.data_import.ident import quote_ident

logger = logging.getLogger("orchestrator.ftl_quote_estimator.rfq_master")

RFQ_MASTER_TABLE = "ezfb_7737f3f4_items"
RFQ_MASTER_FORM_NAME = "RFQ Master"
RFQ_MASTER_FORM_TOKEN = "7737f3f4"

_FTL_DOMAIN = "ftl-distribution"
_ROW_CAP = 400

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_FWD_EMAIL_RE = re.compile(
    r"is:\s*[^<\n]+?\s*<([^>\s]+@[^>\s]+)>",
    re.I,
)
_POSTAL_RE = re.compile(r"\b([A-Z]\d[A-Z])\s?(\d[A-Z]\d)\b", re.I)
_LEGAL_RE = re.compile(
    r"\b(limited|ltd\.?|inc\.?|incorporated|corp\.?|corporation|llc|co\.?)\b",
    re.I,
)
_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_COMPANY_LINE_RE = re.compile(
    r"(?:company(?:\s+name)?|customer(?:\s+name)?)\s*[:\-]\s*(.+)",
    re.I,
)
_STREET_ABBR = (
    ("avenue", "ave"),
    ("street", "st"),
    ("road", "rd"),
    ("drive", "dr"),
    ("boulevard", "blvd"),
    ("ontario", "on"),
)

EMAIL_ALIASES = (
    "Email",
    "E_Mail",
    "E-Mail",
    "Contact_Email",
    "ContactEmail",
    "Customer_Email",
)
COMPANY_ALIASES = (
    "Company_Name",
    "Company Name",
    "Company",
    "Customer",
    "Customer_Name",
    "Name",
)
BILLING_ALIASES = (
    "Billing_Address",
    "Billing Address",
    "Bill_To",
    "BillTo",
    "Bill_Address",
    "Billing",
)
SHIPPING_ALIASES = (
    "Shipping_Address",
    "Shipping Address",
    "Ship_To",
    "ShipTo",
    "Ship_Address",
    "Shipping",
)


def _norm_col(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _ci_get(data: dict[str, Any], *names: str) -> Any:
    if not data:
        return None
    by_norm = {_norm_col(str(k)): v for k, v in data.items()}
    for name in names:
        hit = by_norm.get(_norm_col(name))
        if hit not in (None, ""):
            return hit
    return None


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _row_as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(k): row[k] for k in keys()}
    return {}


def _is_ftl_email(email: str) -> bool:
    return _FTL_DOMAIN in (email or "").lower()


def customer_emails(*values: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _text(raw)
        if not text:
            continue
        for match in _EMAIL_RE.findall(text):
            email = match.strip().strip("<>").lower()
            if not email or _is_ftl_email(email) or email in seen:
                continue
            seen.add(email)
            found.append(email)
        fwd = _FWD_EMAIL_RE.search(text)
        if fwd:
            email = fwd.group(1).strip().lower()
            if email and not _is_ftl_email(email) and email not in seen:
                seen.add(email)
                found.append(email)
    return found


def email_domain(email: str) -> str:
    parts = (email or "").split("@", 1)
    return parts[1].lower().strip() if len(parts) == 2 else ""


def normalize_company(name: str) -> str:
    text = _LEGAL_RE.sub(" ", (name or "").lower())
    return _PUNCT_RE.sub(" ", text).strip()


def normalize_address(value: str) -> str:
    text = (value or "").lower()
    for src, dst in _STREET_ABBR:
        text = text.replace(src, dst)
    return _PUNCT_RE.sub(" ", text).strip()


def _postals(value: str) -> set[str]:
    return {f"{a.upper()}{b.upper()}" for a, b in _POSTAL_RE.findall(value or "")}


def _address_overlap(left: str, right: str) -> bool:
    a = normalize_address(left)
    b = normalize_address(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    left_codes = _postals(left)
    right_codes = _postals(right)
    if left_codes and right_codes and left_codes & right_codes:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    shared = a_tokens & b_tokens
    has_number = any(tok.isdigit() for tok in shared)
    return has_number and len(shared) >= 3


def flatten_master_row(row: dict[str, Any]) -> dict[str, Any]:
    """Lift JSON/object columns so Email / Billing_Address are readable at the top level."""
    out = dict(row or {})
    extra: dict[str, Any] = {}
    for value in list(out.values()):
        parsed: Any = value
        if isinstance(value, str):
            text = value.strip()
            if not text or text[0] not in "{[":
                continue
            try:
                parsed = json.loads(text)
            except Exception:
                continue
        if not isinstance(parsed, dict):
            continue
        for key, inner in parsed.items():
            if extra.get(key) in (None, ""):
                extra[key] = inner
    for key, inner in extra.items():
        if out.get(key) in (None, ""):
            out[key] = inner
    return out


def master_field(row: dict[str, Any], aliases: tuple[str, ...]) -> str:
    return _text(_ci_get(flatten_master_row(row), *aliases))


def _field(quote: dict[str, Any], *names: str) -> str:
    """Read a quote field from snake_case or Title Case public keys."""
    return _text(_ci_get(quote, *names))


def _lookup_blob(
    quote: dict[str, Any],
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
) -> str:
    parts = [
        candidate_text,
        _field(quote, "email", "Email"),
        _field(quote, "customer_name", "Company Name", "Company"),
        _field(quote, "contact_name", "Contact", "Contact Name"),
        _field(quote, "billing_address", "Billing Address"),
        _field(quote, "shipping_address", "Shipping Address"),
    ]
    meta = email_meta if isinstance(email_meta, dict) else {}
    if meta:
        parts.extend(
            [
                _text(meta.get("from")),
                _text(meta.get("to")),
                _text(meta.get("cc")),
                _text(meta.get("subject")),
                _text(meta.get("body_text")),
            ]
        )
        fwd = meta.get("forwarded_sender")
        if isinstance(fwd, dict):
            parts.extend([_text(fwd.get("name")), _text(fwd.get("email"))])
    return "\n".join(part for part in parts if part)


def companies_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _COMPANY_LINE_RE.finditer(text or ""):
        name = _text(match.group(1)).split("\n")[0][:80]
        key = normalize_company(name)
        if not name or len(key) < 3 or key in seen:
            continue
        seen.add(key)
        found.append(name)
    return found


def qualifier_lookup_text(qualifier: Optional[dict[str, Any]]) -> str:
    """Customer identity from Qualifier Result — used only to find the RFQ Master row."""
    if not isinstance(qualifier, dict):
        return ""
    parts = [
        _text(_ci_get(qualifier, "email", "Email")),
        _text(_ci_get(qualifier, "customer_name", "Company Name", "Company", "Customer Name")),
        _text(_ci_get(qualifier, "contact_name", "Contact", "Contact Name")),
        _text(_ci_get(qualifier, "billing_address", "Billing Address")),
        _text(_ci_get(qualifier, "shipping_address", "Shipping Address")),
    ]
    return "\n".join(part for part in parts if part)


def lookup_keys(
    quote: dict[str, Any],
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    blob = _lookup_blob(quote, candidate_text, email_meta)
    emails = customer_emails(blob)
    company = _field(quote, "customer_name", "Company Name", "Company")
    if not company:
        named = companies_from_text(blob)
        company = named[0] if named else ""
    return {
        "emails": emails,
        "domains": [email_domain(e) for e in emails if email_domain(e)],
        "company": company,
        "billing": _field(quote, "billing_address", "Billing Address"),
        "shipping": _field(quote, "shipping_address", "Shipping Address"),
        "text": blob,
    }


def score_master_row(row: dict[str, Any], keys: dict[str, Any]) -> int:
    row_email = master_field(row, EMAIL_ALIASES).lower()
    row_company = master_field(row, COMPANY_ALIASES)
    row_bill = master_field(row, BILLING_ALIASES)
    row_ship = master_field(row, SHIPPING_ALIASES)
    score = 0

    emails = [e.lower() for e in (keys.get("emails") or [])]
    if row_email and row_email in emails:
        score += 100
    elif row_email and email_domain(row_email) in (keys.get("domains") or []):
        score += 40

    quote_company = normalize_company(keys.get("company") or "")
    master_company = normalize_company(row_company)
    haystack = normalize_company(keys.get("text") or "")
    if quote_company and master_company:
        if quote_company == master_company:
            score += 30
        elif quote_company in master_company or master_company in quote_company:
            score += 20
    elif master_company and len(master_company) >= 6 and master_company in haystack:
        score += 25

    fetched = " ".join(
        part for part in (keys.get("billing"), keys.get("shipping")) if part
    )
    master_addr = " ".join(part for part in (row_bill, row_ship) if part)
    if fetched and master_addr and _address_overlap(fetched, master_addr):
        score += 20
    elif keys.get("billing") and row_bill and _address_overlap(keys["billing"], row_bill):
        score += 20
    elif keys.get("shipping") and row_ship and _address_overlap(keys["shipping"], row_ship):
        score += 20

    return score


def pick_master_row(
    rows: list[dict[str, Any]],
    quote: dict[str, Any],
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    keys = lookup_keys(quote, candidate_text, email_meta)
    if not (keys["emails"] or keys["company"] or keys["billing"] or keys["shipping"] or keys["text"]):
        return None
    best: Optional[dict[str, Any]] = None
    best_score = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        flat = flatten_master_row(row)
        score = score_master_row(flat, keys)
        if score > best_score:
            best, best_score = flat, score
    return best if best_score > 0 else None


def _with_address_source_tags(
    quote: dict[str, Any],
    *,
    billing_from_master: bool = False,
    shipping_from_master: bool = False,
) -> dict[str, Any]:
    """Always stamp whether each address was written from RFQ Master."""
    out = dict(quote)
    out["billing_address_from_master"] = bool(billing_from_master)
    out["shipping_address_from_master"] = bool(shipping_from_master)
    return out


def overlay_master_addresses(
    quote: dict[str, Any],
    rows: list[dict[str, Any]],
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write Billing/Shipping from a matched RFQ Master row; otherwise keep EML values."""
    out = dict(quote)
    row = pick_master_row(rows, out, candidate_text, email_meta)
    if not row:
        return _with_address_source_tags(out)
    billing = master_field(row, BILLING_ALIASES)
    shipping = master_field(row, SHIPPING_ALIASES)
    billing_from_master = bool(billing)
    shipping_from_master = bool(shipping)
    if billing_from_master:
        out["billing_address"] = billing
        if "Billing Address" in out:
            out["Billing Address"] = billing
    if shipping_from_master:
        out["shipping_address"] = shipping
        if "Shipping Address" in out:
            out["Shipping Address"] = shipping
    if billing_from_master or shipping_from_master:
        notes = list(out.get("assumptions") or [])
        note = "ℹ Billing / Shipping Address filled from RFQ Master."
        if note not in notes:
            notes.append(note)
        out["assumptions"] = notes
        out["rfq_master_match"] = RFQ_MASTER_FORM_NAME
    return _with_address_source_tags(
        out,
        billing_from_master=billing_from_master,
        shipping_from_master=shipping_from_master,
    )


def _pick_column(columns: list[str], aliases: tuple[str, ...]) -> Optional[str]:
    by_norm = {_norm_col(c): c for c in columns}
    for name in aliases:
        hit = by_norm.get(_norm_col(name))
        if hit:
            return hit
    return None


async def _locate_table(db: Any) -> Optional[tuple[str, str]]:
    row = await db.fetchrow(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE lower(table_name) = lower($1)
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_schema
        LIMIT 1
        """,
        RFQ_MASTER_TABLE,
    )
    if row is None:
        return None
    data = _row_as_dict(row)
    schema = _text(_ci_get(data, "table_schema") or "dbo")
    table = _text(_ci_get(data, "table_name") or RFQ_MASTER_TABLE)
    return (schema or "dbo", table or RFQ_MASTER_TABLE)


async def _table_columns(db: Any, schema: str, table: str) -> list[str]:
    rows = await db.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
        """,
        schema,
        table,
    )
    out: list[str] = []
    for row in rows or []:
        data = _row_as_dict(row)
        name = _text(_ci_get(data, "column_name"))
        if name:
            out.append(name)
    return out


def _deleted_clause(columns: list[str]) -> str:
    col = _pick_column(columns, ("is_deleted", "isdeleted", "IsDeleted"))
    if not col:
        return ""
    return f" AND COALESCE({quote_ident(col)}, false) = false"


async def fetch_rfq_master_rows(
    db: Any,
    quote: dict[str, Any],
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    loc = await _locate_table(db)
    if loc is None:
        logger.info("ftl_rfq_master_table_missing", extra={"table": RFQ_MASTER_TABLE})
        return []
    schema, table = loc
    columns = await _table_columns(db, schema, table)
    if not columns:
        return []
    email_col = _pick_column(columns, EMAIL_ALIASES)
    company_col = _pick_column(columns, COMPANY_ALIASES)
    bill_col = _pick_column(columns, BILLING_ALIASES)
    ship_col = _pick_column(columns, SHIPPING_ALIASES)
    if not any((email_col, company_col, bill_col, ship_col)):
        logger.info(
            "ftl_rfq_master_columns_missing",
            extra={"table": table, "columns": columns[:40]},
        )
        return []

    keys = lookup_keys(quote, candidate_text, email_meta)
    where: list[str] = ["TRUE"]
    params: list[Any] = []
    clauses: list[str] = []

    if email_col and keys["emails"]:
        params.append(keys["emails"][0])
        clauses.append(
            f"lower(btrim(CAST({quote_ident(email_col)} AS text))) = lower(${len(params)})"
        )
        domain = email_domain(keys["emails"][0])
        if domain:
            params.append(f"%@{domain}")
            clauses.append(
                f"lower(btrim(CAST({quote_ident(email_col)} AS text))) LIKE lower(${len(params)})"
            )
    if company_col and keys["company"]:
        params.append(f"%{keys['company']}%")
        clauses.append(
            f"CAST({quote_ident(company_col)} AS text) ILIKE ${len(params)}"
        )
    if bill_col and keys["billing"]:
        needle = keys["billing"].split(",")[0].strip()
        if needle:
            params.append(f"%{needle}%")
            clauses.append(
                f"CAST({quote_ident(bill_col)} AS text) ILIKE ${len(params)}"
            )
    if ship_col and keys["shipping"]:
        needle = keys["shipping"].split(",")[0].strip()
        if needle:
            params.append(f"%{needle}%")
            clauses.append(
                f"CAST({quote_ident(ship_col)} AS text) ILIKE ${len(params)}"
            )

    if clauses:
        where = ["(" + " OR ".join(clauses) + ")"]
    deleted = _deleted_clause(columns)
    sql = (
        f"SELECT * FROM {quote_ident(schema)}.{quote_ident(table)} "
        f"WHERE {' AND '.join(where)}{deleted} "
        f"LIMIT {_ROW_CAP}"
    )
    try:
        rows = await db.fetch(sql, *params)
    except Exception:
        rows = await db.fetch(
            f"SELECT * FROM {quote_ident(schema)}.{quote_ident(table)} "
            f"WHERE TRUE{deleted} LIMIT {_ROW_CAP}"
        )
    return [flatten_master_row(_row_as_dict(row)) for row in (rows or [])]


async def _acquire_db(
    *,
    tenant_id: Optional[str],
    tenant_pools: Any,
    catalog_store: Any,
) -> Any:
    if tenant_pools is None or not str(tenant_id or "").strip():
        return None
    acquire_search = getattr(tenant_pools, "acquire_for_global_search", None)
    if callable(acquire_search):
        return await acquire_search(tenant_id, catalog_store)
    acquire = getattr(tenant_pools, "acquire", None)
    if callable(acquire):
        return await acquire(tenant_id)
    return None


async def apply_rfq_master_addresses(
    quote: dict[str, Any],
    *,
    candidate_text: str = "",
    email_meta: Optional[dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
    tenant_pools: Any = None,
    catalog_store: Any = None,
    rows: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Overlay RFQ Master bill/ship onto quote_result. Keep EML values when unmatched."""
    if not isinstance(quote, dict):
        return quote
    try:
        master_rows = rows
        if master_rows is None:
            db = await _acquire_db(
                tenant_id=tenant_id,
                tenant_pools=tenant_pools,
                catalog_store=catalog_store,
            )
            if db is None:
                return overlay_master_addresses(quote, [], candidate_text, email_meta)
            master_rows = await fetch_rfq_master_rows(
                db, quote, candidate_text, email_meta
            )
        return overlay_master_addresses(quote, master_rows, candidate_text, email_meta)
    except Exception as exc:
        logger.warning(
            "ftl_rfq_master_lookup_failed",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        return overlay_master_addresses(quote, [], candidate_text, email_meta)
