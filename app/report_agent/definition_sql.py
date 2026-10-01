"""Build read-only SQL from a locked reportDefinition."""
from __future__ import annotations

from typing import Any, Optional


def _quote_ident(ident: str) -> str:
    clean = ident.strip(' "')
    if "." in clean:
        parts = [p.strip(' "') for p in clean.split(".") if p.strip(' "')]
        return ".".join(f'"{p}"' for p in parts)
    return f'"{clean}"'


def _format_literal(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def _resolve_field_sql(field: str, alias_map: dict[str, str]) -> str:
    text = (field or "").strip()
    if "." in text:
        alias, col = text.rsplit(".", 1)
        table_sql = alias_map.get(alias.lower())
        if table_sql:
            return f"{table_sql}.{_quote_ident(col)}"
        return f"{_quote_ident(alias)}.{_quote_ident(col)}"
    return _quote_ident(text)


def _filter_sql(filt: dict[str, Any], alias_map: dict[str, str]) -> Optional[str]:
    field = _resolve_field_sql(str(filt.get("field") or ""), alias_map)
    op = str(filt.get("op") or "eq").lower()
    val = filt.get("value")
    if op == "is_null":
        return f"{field} IS NULL"
    if op == "not_null":
        return f"{field} IS NOT NULL"
    if op == "eq":
        return f"{field} = {_format_literal(val)}"
    if op == "ne":
        return f"{field} <> {_format_literal(val)}"
    if op == "gt":
        return f"{field} > {_format_literal(val)}"
    if op == "gte":
        return f"{field} >= {_format_literal(val)}"
    if op == "lt":
        return f"{field} < {_format_literal(val)}"
    if op == "lte":
        return f"{field} <= {_format_literal(val)}"
    if op == "like":
        return f"{field} ILIKE {_format_literal(val)}"
    if op == "in":
        if isinstance(val, (list, tuple, set)):
            items = [_format_literal(v) for v in val]
            if not items:
                return None
            return f"{field} IN ({', '.join(items)})"
        return f"{field} IN ({_format_literal(val)})"
    return None


def generate_definition_sql(
    definition: dict[str, Any],
    *,
    page: int = 1,
    page_size: int = 25,
    extra_filters: Optional[dict[str, Any]] = None,
    sort_field: Optional[str] = None,
    sort_direction: str = "asc",
) -> tuple[str, str]:
    """Return (data_sql, count_sql)."""
    sources = definition.get("sources") or []
    if not sources:
        raise ValueError("Cannot generate SQL without sources.")

    alias_map: dict[str, str] = {}
    from_parts: list[str] = []
    for i, src in enumerate(sources):
        schema_name = src.get("schemaName") or src.get("schema_name")
        table = src.get("table")
        alias = str(src.get("alias") or table)
        if schema_name:
            table_sql = f"{_quote_ident(str(schema_name))}.{_quote_ident(str(table))}"
        else:
            table_sql = _quote_ident(str(table))
        alias_sql = _quote_ident(alias)
        alias_map[alias.lower()] = alias_sql
        if i == 0:
            from_parts.append(f"{table_sql} AS {alias_sql}")
        else:
            # joins handled separately; still register aliases
            pass

    join_clauses: list[str] = []
    used_aliases = {str(sources[0].get("alias") or sources[0].get("table")).lower()}
    for join in definition.get("joins") or []:
        left = str(join.get("left") or "")
        right = str(join.get("right") or "")
        jtype = "LEFT JOIN" if str(join.get("type") or "left").lower() == "left" else "INNER JOIN"
        right_alias = left_alias = ""
        if "." in right:
            right_alias = right.split(".", 1)[0].lower()
        if "." in left:
            left_alias = left.split(".", 1)[0].lower()
        # find source for the new alias
        new_alias = right_alias if right_alias not in used_aliases else left_alias
        src_match = next(
            (s for s in sources if str(s.get("alias") or s.get("table")).lower() == new_alias),
            None,
        )
        if not src_match:
            continue
        schema_name = src_match.get("schemaName") or src_match.get("schema_name")
        table = src_match.get("table")
        alias = str(src_match.get("alias") or table)
        if schema_name:
            table_sql = f"{_quote_ident(str(schema_name))}.{_quote_ident(str(table))}"
        else:
            table_sql = _quote_ident(str(table))
        alias_sql = _quote_ident(alias)
        alias_map[alias.lower()] = alias_sql
        used_aliases.add(alias.lower())
        on_sql = f"{_resolve_field_sql(left, alias_map)} = {_resolve_field_sql(right, alias_map)}"
        join_clauses.append(f"{jtype} {table_sql} AS {alias_sql} ON {on_sql}")

    select_parts: list[str] = []
    for col in definition.get("columns") or []:
        key = str(col.get("key") or "col")
        source = str(col.get("source") or "")
        agg = str(col.get("aggregate") or "none").lower()
        if agg == "count" and source in ("1", "*", ""):
            expr = "COUNT(*)"
        elif agg == "count":
            expr = f"COUNT({_resolve_field_sql(source, alias_map)})"
        elif agg == "sum":
            expr = f"SUM({_resolve_field_sql(source, alias_map)})"
        elif agg == "avg":
            expr = f"AVG({_resolve_field_sql(source, alias_map)})"
        elif agg == "min":
            expr = f"MIN({_resolve_field_sql(source, alias_map)})"
        elif agg == "max":
            expr = f"MAX({_resolve_field_sql(source, alias_map)})"
        else:
            expr = _resolve_field_sql(source, alias_map)
        select_parts.append(f"{expr} AS {_quote_ident(key)}")

    if not select_parts:
        raise ValueError("Cannot generate SQL without columns.")

    where_parts: list[str] = []
    for filt in definition.get("filters") or []:
        clause = _filter_sql(filt, alias_map)
        if clause:
            where_parts.append(clause)

    # UI filters: map availableFilters keys -> field, or treat key as field
    avail = {
        str(a.get("key") or "").lower(): a
        for a in (definition.get("availableFilters") or [])
        if isinstance(a, dict)
    }
    for key, value in (extra_filters or {}).items():
        if value is None or value == "":
            continue
        af = avail.get(str(key).lower())
        field = str(af.get("field") or key) if af else str(key)
        # try column key match
        col_match = next(
            (c for c in (definition.get("columns") or []) if str(c.get("key")).lower() == str(key).lower()),
            None,
        )
        if col_match and not af:
            field = str(col_match.get("source") or field)
        clause = _filter_sql({"field": field, "op": "eq", "value": value}, alias_map)
        if clause:
            where_parts.append(clause)

    group_sql = ""
    group_cols = definition.get("groupBy") or []
    if group_cols:
        group_sql = "GROUP BY " + ", ".join(_resolve_field_sql(str(g), alias_map) for g in group_cols)

    order_bits: list[str] = []
    if sort_field:
        direction = "DESC" if str(sort_direction).lower() == "desc" else "ASC"
        keys = {str(c.get("key")).lower(): str(c.get("key")) for c in (definition.get("columns") or [])}
        if sort_field.lower() in keys:
            order_bits.append(f"{_quote_ident(keys[sort_field.lower()])} {direction}")
        else:
            order_bits.append(f"{_resolve_field_sql(sort_field, alias_map)} {direction}")
    else:
        for ob in definition.get("orderBy") or []:
            direction = "DESC" if str(ob.get("direction") or "asc").lower() == "desc" else "ASC"
            field = str(ob.get("field") or "")
            keys = {str(c.get("key")).lower(): str(c.get("key")) for c in (definition.get("columns") or [])}
            if field.lower() in keys:
                order_bits.append(f"{_quote_ident(keys[field.lower()])} {direction}")
            else:
                order_bits.append(f"{_resolve_field_sql(field, alias_map)} {direction}")

    order_sql = ("ORDER BY " + ", ".join(order_bits)) if order_bits else ""

    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(int(page_size or 25), 500))
    offset = (safe_page - 1) * safe_size

    from_sql = from_parts[0]
    joins_sql = "\n".join(join_clauses)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    data_sql = "\n".join(
        p
        for p in [
            "SELECT",
            "    " + ",\n    ".join(select_parts),
            f"FROM {from_sql}",
            joins_sql,
            where_sql,
            group_sql,
            order_sql,
            f"LIMIT {safe_size} OFFSET {offset}",
        ]
        if p
    )

    # count query: wrap
    inner = "\n".join(
        p
        for p in [
            "SELECT 1",
            f"FROM {from_sql}",
            joins_sql,
            where_sql,
            group_sql,
        ]
        if p
    )
    if group_sql:
        count_sql = f"SELECT COUNT(*) AS total FROM ({inner}) AS _report_count"
    else:
        count_sql = "\n".join(
            p
            for p in [
                "SELECT COUNT(*) AS total",
                f"FROM {from_sql}",
                joins_sql,
                where_sql,
            ]
            if p
        )

    return data_sql, count_sql
