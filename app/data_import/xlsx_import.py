"""Map Excel rows onto tenant form tables and upsert/insert."""
from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import Column, MetaData, Table, Text, text
from sqlalchemy.orm import sessionmaker

from app.data_import.ident import is_guid, pg_ident, quote_ident
from app.data_import.models import DataImportRequest

logger = logging.getLogger("orchestrator.data_import")


def _sql_literal(value: Any) -> str:
    return str(value).replace("'", "''")


def _execute_form_query(session, sql: str, params: dict) -> list:
    return session.execute(text(sql), params).fetchall()


def resolve_table(session_or_conn, table_name: str) -> dict[str, Any]:
    wanted = str(table_name).strip()
    rows = session_or_conn.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = lower(:t)
              AND table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_schema
            LIMIT 1
            """
        ),
        {"t": wanted},
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f'Table "{wanted}" not found in tenant database')
    schema_name, real_name = rows[0][0], rows[0][1]
    col_rows = session_or_conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
            """
        ),
        {"schema": schema_name, "table": real_name},
    ).fetchall()
    columns = [str(row[0]) for row in col_rows]
    return {
        "schema": schema_name,
        "table": real_name,
        "columns": columns,
        "qualified": f"{quote_ident(schema_name)}.{quote_ident(real_name)}",
    }


def control_db_column(column_name: Any, json_id: Any) -> str:
    """Prefer wFormControl.columnName for ezfb_*_items SQL; jsonId is fallback."""
    name = str(column_name or "").strip()
    if name:
        return name
    return str(json_id or "").strip()


def _target_column_sql(cols: dict[str, str]) -> str:
    json_c = quote_ident(cols["jsonid"]) if "jsonid" in cols else "NULL"
    col_key = next((key for key in ("columnname", "column_name") if key in cols), None)
    if not col_key:
        return f"{json_c}::text"
    col_c = quote_ident(cols[col_key])
    return f"coalesce(nullif(btrim({col_c}::text), ''), {json_c}::text)"


def _resolve_wformcontrol_table(session) -> dict[str, Any]:
    tables = session.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = 'wformcontrol'
              AND table_type = 'BASE TABLE'
            ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_schema
            """
        )
    ).fetchall()
    if not tables:
        raise HTTPException(status_code=404, detail="No wFormControl table in tenant database.")
    schema_name, table_name = tables[0][0], tables[0][1]
    col_rows = session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            """
        ),
        {"schema": schema_name, "table": table_name},
    ).fetchall()
    by_lower = {str(row[0]).lower(): str(row[0]) for row in col_rows}
    required = ("name", "wformid")
    missing = [col for col in required if col not in by_lower]
    if missing or ("jsonid" not in by_lower and "columnname" not in by_lower and "column_name" not in by_lower):
        raise HTTPException(status_code=404, detail="wFormControl is missing required columns.")
    return {
        "schema": schema_name,
        "table": table_name,
        "columns": by_lower,
        "qualified": f"{quote_ident(schema_name)}.{quote_ident(table_name)}",
    }


def fetch_form_column_mapping(session, form_id: str) -> dict[str, str]:
    name_to_col, _json_to_col, _date_cols, _table_cols = fetch_form_control_maps(session, form_id)
    return name_to_col


def fetch_form_control_maps(
    session, form_id: str
) -> tuple[dict[str, str], dict[str, str], set[str], list[str]]:
    """Excel control name → table columnName; jsonId → columnName; date columnNames; TABLE columns."""
    token = str(form_id).strip()
    if not is_guid(token):
        return {}, {}, set(), []
    guid = str(UUID(token))
    try:
        meta = _resolve_wformcontrol_table(session)
        cols = meta["columns"]
        name_c = quote_ident(cols["name"])
        target_c = _target_column_sql(cols)
        form_c = quote_ident(cols["wformid"])
        json_c = quote_ident(cols["jsonid"]) if "jsonid" in cols else "NULL"
        type_col = cols.get("type") or cols.get("controltype")
        type_expr = (
            f"upper(coalesce({quote_ident(type_col)}::text, ''))"
            if type_col
            else "''"
        )
        deleted_filter = ""
        if "isdeleted" in cols:
            deleted_filter = f' AND coalesce({quote_ident(cols["isdeleted"])}::int, 0) = 0'
        sql = f"""
            SELECT
                lower(replace({name_c}::text, ' ', '')) AS norm_name,
                {target_c} AS target_col,
                {json_c}::text AS jsonid,
                {type_expr} AS control_type
            FROM {meta["qualified"]}
            WHERE lower({form_c}::text) = lower(:fid)
            {deleted_filter}
        """
        rows = _execute_form_query(session, sql, {"fid": guid})
        name_to_col: dict[str, str] = {}
        json_to_col: dict[str, str] = {}
        date_cols: set[str] = set()
        table_columns: list[str] = []
        for row in rows:
            norm_name = str(row[0] or "").strip()
            target_col = control_db_column(row[1], row[2])
            json_id = str(row[2] or "").strip()
            control_type = str(row[3] or "").strip().upper()
            if norm_name and target_col:
                name_to_col[norm_name] = target_col
            if json_id and target_col:
                json_to_col[json_id.lower()] = target_col
            if target_col and control_type in {"DATE", "DATETIME"}:
                date_cols.add(target_col)
                if json_id:
                    date_cols.add(json_id)
            if target_col and control_type == "TABLE" and target_col not in table_columns:
                table_columns.append(target_col)
        return name_to_col, json_to_col, date_cols, table_columns
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("form_column_map_failed", extra={"error_type": type(exc).__name__})
        try:
            session.rollback()
        except Exception:
            pass
        return {}, {}, set(), []


def fetch_form_date_jsonids(session, form_id: str) -> set[str]:
    _name_to_col, _json_to_col, date_cols, _table_cols = fetch_form_control_maps(session, form_id)
    return date_cols


def _update_import_status(engine, request: DataImportRequest, remarks: str) -> None:
    fid = str(request.id or "").strip()
    nid = str(request.notifyId or "").strip()
    if fid:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE "masterFileprocess"
                        SET status = 1, remarks = :remarks
                        WHERE id::text = :fid
                        """
                    ),
                    {"remarks": remarks, "fid": fid},
                )
        except Exception as exc:
            logger.warning("master_fileprocess_update_skipped", extra={"error_type": type(exc).__name__})
    if nid:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE "notification"
                        SET status = 'COMPLETED', remarks = :remarks
                        WHERE id::text = :nid
                        """
                    ),
                    {"remarks": remarks, "nid": nid},
                )
        except Exception as exc:
            logger.warning("notification_update_skipped", extra={"error_type": type(exc).__name__})


def _drop_staging_table(engine, staging_table_name: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {pg_ident(staging_table_name)}"))
    except Exception as exc:
        logger.warning("staging_drop_failed", extra={"error_type": type(exc).__name__})


def execute_postgres_upsert_import(
    engine,
    table_qualified: str,
    staging_table_name: str,
    staging_table_columns: list[str],
    condition_col: str,
    request: DataImportRequest,
    today: str,
) -> str:
    qt = table_qualified
    qs = pg_ident(staging_table_name)
    qc = pg_ident(condition_col)
    userid = _sql_literal(request.userid)
    skip = {condition_col.lower(), "item_id", "itemid"}
    update_cols = [col for col in staging_table_columns if col.lower() not in skip]
    # Postgres rejects alias-qualified SET targets (SET t.col = ...).
    set_parts = [f"{pg_ident(col)} = s.{pg_ident(col)}" for col in update_cols]
    set_parts.append(f"modified_at = '{today}'")
    set_parts.append(f"modified_by = '{userid}'")
    insert_cols = [col for col in staging_table_columns if col.lower() not in ("item_id", "itemid")]
    insert_col_list = ", ".join(
        [pg_ident(col) for col in insert_cols]
        + ["created_by", "created_at", "modified_by", "modified_at", "is_deleted", "today_task", "is_marked"]
    )
    insert_sel_list = ", ".join(
        [f"s.{pg_ident(col)}" for col in insert_cols]
        + [f"'{userid}'", f"'{today}'", f"'{userid}'", f"'{today}'", "false", "false", "false"]
    )
    empty_insert_cols = [col for col in staging_table_columns if col.lower() not in skip]
    empty_col_list = ", ".join(
        [pg_ident(col) for col in empty_insert_cols]
        + ["created_by", "created_at", "modified_by", "modified_at", "is_deleted", "today_task", "is_marked"]
    )
    empty_sel_list = ", ".join(
        [pg_ident(col) for col in empty_insert_cols]
        + [f"'{userid}'", f"'{today}'", f"'{userid}'", f"'{today}'", "false", "false", "false"]
    )

    with engine.begin() as conn:
        updated_count = conn.execute(
            text(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT ON ({qc}) *
                    FROM {qs}
                    WHERE coalesce({qc}::text, '') <> ''
                    ORDER BY {qc}
                )
                UPDATE {qt} t
                SET {", ".join(set_parts)}
                FROM deduped s
                WHERE t.{qc}::text = s.{qc}::text AND t.is_deleted = false
                """
            )
        ).rowcount
        inserted_from_merge = conn.execute(
            text(
                f"""
                INSERT INTO {qt} ({insert_col_list})
                SELECT {insert_sel_list}
                FROM (
                    SELECT DISTINCT ON ({qc}) *
                    FROM {qs}
                    WHERE coalesce({qc}::text, '') <> ''
                    ORDER BY {qc}
                ) s
                WHERE NOT EXISTS (
                    SELECT 1 FROM {qt} t
                    WHERE t.{qc}::text = s.{qc}::text AND t.is_deleted = false
                )
                """
            )
        ).rowcount
        inserted_empty = conn.execute(
            text(
                f"""
                INSERT INTO {qt} ({empty_col_list})
                SELECT {empty_sel_list}
                FROM {qs}
                WHERE coalesce({qc}::text, '') = ''
                """
            )
        ).rowcount
        inserted_count = inserted_from_merge + inserted_empty
        remarks = f"Total rows inserted: {inserted_count}, Total rows updated: {updated_count}"
    _update_import_status(engine, request, remarks)
    return remarks


def execute_postgres_insert_only(
    engine,
    table_qualified: str,
    staging_table_name: str,
    staging_table_columns: list[str],
    request: DataImportRequest,
    today: str,
) -> str:
    qt = table_qualified
    qs = pg_ident(staging_table_name)
    userid = _sql_literal(request.userid)
    insert_cols = [col for col in staging_table_columns if col.lower() not in ("item_id", "itemid")]
    col_list = ", ".join(
        [pg_ident(col) for col in insert_cols]
        + ["created_by", "created_at", "modified_by", "modified_at", "is_deleted", "today_task", "is_marked"]
    )
    sel_list = ", ".join(
        [f"s.{pg_ident(col)}" for col in insert_cols]
        + [f"'{userid}'", f"'{today}'", f"'{userid}'", f"'{today}'", "false", "false", "false"]
    )
    with engine.begin() as conn:
        inserted_count = conn.execute(
            text(f"INSERT INTO {qt} ({col_list}) SELECT {sel_list} FROM {qs} s")
        ).rowcount
        remarks = f"Total rows inserted: {inserted_count}, Total rows updated: 0"
    _update_import_status(engine, request, remarks)
    return remarks


def _fix_date_columns(df: pd.DataFrame, date_ids: set[str]) -> None:
    if df is None or not isinstance(df, pd.DataFrame) or not date_ids:
        return
    targets = [col for col in date_ids if col in df.columns]
    if not targets:
        return
    excel_origin = pd.Timestamp("1899-12-30")
    for col in targets:
        series = df[col].replace(r"^\s*$", pd.NA, regex=True)
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=False)
        mask = parsed.isna()
        if mask.any():
            parsed.loc[mask] = pd.to_datetime(series[mask], errors="coerce", dayfirst=True)
        mask = parsed.isna()
        if mask.any():
            nums = pd.to_numeric(
                series[mask].astype(str).str.replace(",", "").str.strip(),
                errors="coerce",
            )
            serial_dt = excel_origin + pd.to_timedelta(nums, unit="D")
            parsed.loc[mask & nums.notna()] = serial_dt[nums.notna()]
        df[col] = parsed.dt.strftime("%Y-%m-%d")
        df[col] = df[col].where(parsed.notna(), None)


def _normalize_col(col: str) -> str:
    return str(col).lower().strip().replace(" ", "").replace("-", "").replace("/", "").replace(".", "")


def _to_ymd(val: Any) -> Optional[str]:
    if val is None:
        return None
    text = str(val).strip()
    if not text:
        return None
    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        dt = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        num = pd.to_numeric(text.replace(",", ""), errors="coerce")
        if pd.notna(num):
            dt = pd.Timestamp("1899-12-30") + pd.to_timedelta(num, unit="D")
    return None if pd.isna(dt) else dt.strftime("%Y-%m-%d")


def _choose_master_sheet(
    frames: list[tuple[str, pd.DataFrame]],
    normalized_mapping: dict[str, str],
    table_columns: list[str],
) -> int:
    db_targets = {_normalize_col(col) for col in table_columns}
    best_idx = 0
    best_score = -1
    for idx, (_sheet_name, frame) in enumerate(frames):
        cols = [_normalize_col(col) for col in frame.columns]
        mapped_hits = sum(1 for col in cols if col in normalized_mapping)
        db_hits = sum(1 for col in cols if col in db_targets)
        score = mapped_hits * 10 + db_hits
        if score > best_score:
            best_idx = idx
            best_score = score
    return best_idx


def _read_workbook(
    file_bytes: bytes,
    column_mapping: dict[str, str],
    date_jsonids: set[str],
    table_columns: list[str],
) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    sheets = xls.sheet_names
    if len(sheets) == 1:
        df = pd.read_excel(xls, dtype=str)
        return df.replace(r"^\s*$", None, regex=True)

    if len(sheets) != 2:
        df = pd.read_excel(xls, sheet_name=sheets[0], dtype=str)
        return df.replace(r"^\s*$", None, regex=True)

    frames = [(sheet, pd.read_excel(xls, sheet_name=sheet, dtype=str)) for sheet in sheets[:2]]
    for _, frame in frames:
        frame.replace(r"^\s*$", None, regex=True, inplace=True)
        frame.dropna(how="all", inplace=True)
        frame.reset_index(drop=True, inplace=True)
    normalized_mapping = {_normalize_col(key): value for key, value in column_mapping.items()}
    master_idx = _choose_master_sheet(frames, normalized_mapping, table_columns)
    detail_idx = 1 - master_idx
    _master_sheetname, df = frames[master_idx]
    df2_sheetname, df2 = frames[detail_idx]
    key_col = df.columns[0]
    if key_col in df.columns:
        df[key_col] = df[key_col].astype(str).where(pd.notna(df[key_col]), None)
    if key_col in df2.columns:
        df2[key_col] = df2[key_col].astype(str).where(pd.notna(df2[key_col]), None)
    if key_col not in df2.columns:
        df["__enriched"] = None
    else:
        reverse_mapping = normalized_mapping
        non_key_cols = [col for col in df2.columns if col != key_col]

        def _has_any_value(row) -> bool:
            return any(pd.notna(v) and str(v).strip() != "" for v in row)

        if non_key_cols:
            df2["__any_value"] = df2[non_key_cols].apply(_has_any_value, axis=1)
        else:
            df2["__any_value"] = False
        df2 = df2[
            df2[key_col].notnull()
            & (df2[key_col].astype(str).str.strip() != "")
            & df2["__any_value"]
        ].drop(columns="__any_value", errors="ignore").reset_index(drop=True)

        def _row2payload(row_dict: dict) -> dict:
            payload = {}
            for key, value in row_dict.items():
                if key == key_col:
                    continue
                if pd.isna(value) or str(value).strip() == "":
                    continue
                json_id = reverse_mapping.get(_normalize_col(key))
                if not json_id:
                    continue
                if json_id in date_jsonids:
                    value = _to_ymd(value)
                payload[json_id] = value
            return payload

        def _pack_detail_group(group_df: pd.DataFrame) -> list:
            items = []
            for row in group_df.to_dict(orient="records"):
                payload = _row2payload(row)
                if payload:
                    items.append(payload)
            return items

        if not df2.empty:
            packed = (
                df2.groupby(key_col, dropna=False)
                .apply(_pack_detail_group)
                .reset_index(name="__enriched")
            )
            df = df.merge(packed, how="left", left_on=key_col, right_on=key_col)
        else:
            df["__enriched"] = None

    def _to_json_or_none(val):
        if isinstance(val, list):
            return json.dumps(val) if val else None
        if isinstance(val, dict):
            return json.dumps(val) if val else None
        return None

    df["__enriched"] = df["__enriched"].where(pd.notna(df["__enriched"]), None)
    df[df2_sheetname] = df["__enriched"].apply(_to_json_or_none)
    df.drop(columns="__enriched", inplace=True)
    return df


def import_xlsx_bytes(engine, request: DataImportRequest, table_name: str, file_bytes: bytes) -> dict[str, str]:
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        column_mapping, json_to_column, date_columns, table_json_columns = fetch_form_control_maps(
            session, request.formId
        )
        if not column_mapping:
            raise HTTPException(status_code=404, detail="No form controls found for formId.")
    finally:
        session.close()

    with engine.connect() as conn:
        table_meta = resolve_table(conn, table_name)
    table_qualified = table_meta["qualified"]
    db_by_lower = {col.lower(): col for col in table_meta["columns"]}

    df = _read_workbook(file_bytes, column_mapping, date_columns, table_json_columns)
    normalized_mapping = {_normalize_col(key): value for key, value in column_mapping.items()}
    normalized_json_mapping = {_normalize_col(key): value for key, value in json_to_column.items()}
    normalized_db_mapping = {_normalize_col(col): col for col in table_meta["columns"]}
    df.columns = [_normalize_col(col) for col in df.columns]
    df.rename(columns=normalized_mapping, inplace=True)
    df.rename(columns=normalized_json_mapping, inplace=True)
    df.rename(columns=normalized_db_mapping, inplace=True)
    _fix_date_columns(df, date_columns)
    if "entryid" in df.columns and "item_id" not in df.columns:
        df.rename(columns={"entryid": "item_id"}, inplace=True)

    mapped_conditions: list[str] = []
    for col in request.conditionColumn or []:
        raw = str(col or "").strip()
        mapped_conditions.append(json_to_column.get(raw.lower(), raw))
    if mapped_conditions != list(request.conditionColumn or []):
        request = request.model_copy(update={"conditionColumn": mapped_conditions})

    rename_to_db = {}
    staging_table_columns: list[str] = []
    for col in df.columns:
        real = db_by_lower.get(str(col).lower())
        if real:
            staging_table_columns.append(real)
            if col != real:
                rename_to_db[col] = real
    if rename_to_db:
        df = df.rename(columns=rename_to_db)
    if not staging_table_columns:
        raise HTTPException(status_code=404, detail="No matching columns found")

    staging_table_name = f"StagingTable_{uuid.uuid4().hex}"
    metadata = MetaData()
    Table(
        staging_table_name,
        metadata,
        *(Column(col, Text, nullable=True) for col in staging_table_columns),
    )
    metadata.create_all(engine)
    df[staging_table_columns] = df[staging_table_columns].fillna("")
    try:
        df[staging_table_columns].to_sql(
            staging_table_name,
            con=engine,
            if_exists="replace",
            index=False,
            dtype={col: Text for col in staging_table_columns},
        )
    except Exception as exc:
        logger.warning("staging_write_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Failed to write to staging table") from exc

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    df_cols_lower = {str(col).lower(): col for col in df.columns}
    effective: list[str] = []
    for col in request.conditionColumn or []:
        match = df_cols_lower.get(str(col).lower())
        if match:
            effective.append(match)
    if not effective and "item_id" in df.columns and request.conditionColumn:
        effective.append("item_id")

    try:
        if effective:
            remarks = execute_postgres_upsert_import(
                engine,
                table_qualified,
                staging_table_name,
                staging_table_columns,
                effective[0],
                request,
                today,
            )
        else:
            remarks = execute_postgres_insert_only(
                engine,
                table_qualified,
                staging_table_name,
                staging_table_columns,
                request,
                today,
            )
    finally:
        _drop_staging_table(engine, staging_table_name)
    return {"message": remarks}
