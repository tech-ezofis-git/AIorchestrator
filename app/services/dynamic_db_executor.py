"""Transaction-Safe Dynamic DB Execution Engine.

Executes dynamic, parameterized INSERTs for source workflow tickets and
intelligent UPSERTs for master catalog records across any Ezofis schema.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import psycopg2
from psycopg2 import sql

logger = logging.getLogger("orchestrator.dynamic_db_executor")


def execute_form_sync_transaction(
    conn,
    source_schema: Dict[str, Any],
    target_schema: Dict[str, Any],
    source_record_data: Dict[str, Any],
    target_records_plan: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Executes the source insert and target upserts inside an ACID transaction.
    Rolls back completely if any step fails.
    """
    source_table_name = source_schema["table_name"]
    target_table_name = target_schema["table_name"]

    source_phys_cols = source_schema.get("physical_columns", {})
    target_phys_cols = target_schema.get("physical_columns", {})

    now_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    target_results: List[Dict[str, Any]] = []

    try:
        # If connection has pending transaction from schema introspection, commit clean state
        if conn.status != 0 and not conn.autocommit:
            conn.commit()

        with conn.cursor() as cur:
            # -------------------------------------------------------------
            # STEP 1: Insert Source Workflow Record
            # -------------------------------------------------------------
            filtered_source = {}
            for k, v in source_record_data.items():
                if k in source_phys_cols and v is not None:
                    filtered_source[k] = v

            # Primary key: item_id
            source_item_id = str(uuid.uuid4())
            if "item_id" in source_phys_cols:
                filtered_source["item_id"] = source_item_id

            if "created_at" in source_phys_cols and "created_at" not in filtered_source:
                filtered_source["created_at"] = now_iso
            if "modified_at" in source_phys_cols and "modified_at" not in filtered_source:
                filtered_source["modified_at"] = now_iso
            if "is_deleted" in source_phys_cols and "is_deleted" not in filtered_source:
                filtered_source["is_deleted"] = False

            if not filtered_source:
                raise ValueError(f"No valid columns found to insert into source table dbo.{source_table_name}")

            source_cols = list(filtered_source.keys())
            source_vals = [filtered_source[c] for c in source_cols]

            insert_source_query = sql.SQL(
                "INSERT INTO dbo.{} ({}) VALUES ({}) RETURNING item_id::text;"
            ).format(
                sql.Identifier(source_table_name),
                sql.SQL(", ").join(map(sql.Identifier, source_cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(source_cols))
            )

            cur.execute(insert_source_query, source_vals)
            res = cur.fetchone()
            created_source_id = res[0] if res else source_item_id
            logger.info("Inserted source workflow record %s into %s", created_source_id, source_table_name)

            # -------------------------------------------------------------
            # STEP 2: Upsert Target Master Records
            # -------------------------------------------------------------
            for plan_item in target_records_plan:
                match_criteria = plan_item.get("match_criteria", {})
                fields = plan_item.get("fields", {})

                # Filter fields against target table columns
                filtered_fields = {
                    k: v for k, v in fields.items()
                    if k in target_phys_cols and v is not None
                }

                # Filter match criteria against target table columns
                valid_criteria = {
                    k: v for k, v in match_criteria.items()
                    if k in target_phys_cols and v is not None
                }

                existing_target_id = None
                if valid_criteria:
                    # Check if matching record exists
                    where_clauses = []
                    where_vals = []
                    for col, val in valid_criteria.items():
                        where_clauses.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
                        where_vals.append(val)

                    if "is_deleted" in target_phys_cols:
                        where_clauses.append(sql.SQL("is_deleted = false"))

                    check_query = sql.SQL(
                        "SELECT item_id::text FROM dbo.{} WHERE {} LIMIT 1;"
                    ).format(
                        sql.Identifier(target_table_name),
                        sql.SQL(" AND ").join(where_clauses)
                    )
                    cur.execute(check_query, where_vals)
                    row = cur.fetchone()
                    if row:
                        existing_target_id = row[0]

                if existing_target_id:
                    # UPDATE existing master record
                    update_cols = []
                    update_vals = []
                    for col, val in filtered_fields.items():
                        if col in ("item_id", "created_at", "created_by"):
                            continue
                        update_cols.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
                        update_vals.append(val)

                    if "modified_at" in target_phys_cols:
                        update_cols.append(sql.SQL("modified_at = %s"))
                        update_vals.append(now_iso)

                    if update_cols:
                        update_query = sql.SQL(
                            "UPDATE dbo.{} SET {} WHERE item_id::text = %s;"
                        ).format(
                            sql.Identifier(target_table_name),
                            sql.SQL(", ").join(update_cols)
                        )
                        update_vals.append(existing_target_id)
                        cur.execute(update_query, update_vals)

                    target_results.append({
                        "action": "updated",
                        "target_id": existing_target_id,
                        "match_criteria": valid_criteria,
                        "fields": filtered_fields
                    })
                    logger.info("Updated existing master record %s in %s", existing_target_id, target_table_name)
                else:
                    # INSERT new master record
                    new_target_id = str(uuid.uuid4())
                    if "item_id" in target_phys_cols:
                        filtered_fields["item_id"] = new_target_id
                    if "created_at" in target_phys_cols and "created_at" not in filtered_fields:
                        filtered_fields["created_at"] = now_iso
                    if "modified_at" in target_phys_cols and "modified_at" not in filtered_fields:
                        filtered_fields["modified_at"] = now_iso
                    if "is_deleted" in target_phys_cols and "is_deleted" not in filtered_fields:
                        filtered_fields["is_deleted"] = False

                    target_cols = list(filtered_fields.keys())
                    target_vals = [filtered_fields[c] for c in target_cols]

                    insert_query = sql.SQL(
                        "INSERT INTO dbo.{} ({}) VALUES ({}) RETURNING item_id::text;"
                    ).format(
                        sql.Identifier(target_table_name),
                        sql.SQL(", ").join(map(sql.Identifier, target_cols)),
                        sql.SQL(", ").join(sql.Placeholder() * len(target_cols))
                    )
                    cur.execute(insert_query, target_vals)
                    r = cur.fetchone()
                    inserted_id = r[0] if r else new_target_id

                    target_results.append({
                        "action": "inserted",
                        "target_id": inserted_id,
                        "match_criteria": valid_criteria,
                        "fields": filtered_fields
                    })
                    logger.info("Inserted new master record %s into %s", inserted_id, target_table_name)

        # Commit transaction
        conn.commit()

        return {
            "success": True,
            "source_record_id": created_source_id,
            "target_records_count": len(target_results),
            "target_records": target_results,
            "message": f"Successfully synced ticket {created_source_id} and {len(target_results)} master record(s)."
        }

    except Exception as exc:
        conn.rollback()
        logger.error("Transaction rolled back due to error: %s", exc, exc_info=True)
        raise exc
