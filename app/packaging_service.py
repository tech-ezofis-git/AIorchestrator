import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse, urlunparse
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DEFAULT_TENANT_ID = "1f10e791-b054-483a-8414-e77961f3e282"


def get_tenant_db_connection(tenant_id: str = DEFAULT_TENANT_ID):
    """
    Automatically derives the tenant database connection from DATABASE_URL.
    Ezofis tenant databases follow the naming: ezofis_Tenant_<first_8_chars_of_tenant_id>
    
    If TENANT_DATABASE_URL is set in .env, it uses that as an override.
    Otherwise, it derives it directly from DATABASE_URL.
    """
    # 1. Check for explicit override in .env
    override_url = os.getenv("TENANT_DATABASE_URL")
    if override_url:
        return psycopg2.connect(override_url)

    # 2. Derive dynamically from DATABASE_URL
    base_url = os.getenv("DATABASE_URL")
    if not base_url:
        raise RuntimeError("DATABASE_URL is not set in environment or .env file.")

    tenant_id = tenant_id or DEFAULT_TENANT_ID
    tenant_prefix = tenant_id.replace("-", "")[:8]
    parsed = urlparse(base_url)
    tenant_url = urlunparse(parsed._replace(path=f"/ezofis_Tenant_{tenant_prefix}"))

    return psycopg2.connect(tenant_url)


def lookup_master_item(material_code: str, tenant_id: str = DEFAULT_TENANT_ID) -> Optional[Dict[str, Any]]:
    """
    Lookup a packaging item in the Master Form catalog by Material_Code.
    Used by frontend auto-complete/validation when user enters a Product Code.

    :param material_code: e.g. 'BTL-001', 'CAP-001'
    :param tenant_id: Tenant UUID (default: 1f10e791-b054-483a-8414-e77961f3e282)
    :return: Dictionary of master item details, or None if not found.
    """
    if not material_code or not material_code.strip():
        return None

    query = """
        SELECT 
            item_id,
            "Material_Code",
            "Brand",
            "Description",
            "Material_Specifications",
            "Dimension",
            "Size",
            "Version_Number",
            "Status",
            created_at,
            modified_at
        FROM dbo.ezfb_f271d1d3_items
        WHERE "Material_Code" = %s AND is_deleted = false
        LIMIT 1;
    """
    with get_tenant_db_connection(tenant_id) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (material_code.strip(),))
            row = cur.fetchone()
            return dict(row) if row else None


def get_ticket_matched_master_items(ticket_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
    """
    Given a Workflow Ticket ID, unpacks all 11 component slots and returns
    each component along with its match status in the Master Form catalog.

    :param ticket_id: UUID string of the ticket (ezfb_aa038e45_items.item_id)
    :param tenant_id: Tenant UUID (default: 1f10e791-b054-483a-8414-e77961f3e282)
    :return: List of matched component dictionaries.
    """
    query = """
    SELECT 
        wf.item_id AS ticket_id,
        wf."Brand" AS header_brand,
        wf."System_Product_NameERP" AS erp_product_name,
        c.slot_number,
        c.component_name,
        c.product_code,
        c.specs,
        c.dimension,
        c.size,
        CASE 
            WHEN mf."Material_Code" IS NOT NULL THEN 'MATCHED_IN_MASTER'
            ELSE 'PENDING_CREATION'
        END AS sync_status,
        mf.item_id AS master_item_id,
        mf."Description" AS master_description,
        mf."Status" AS master_status
    FROM dbo.ezfb_aa038e45_items wf
    CROSS JOIN LATERAL (
        VALUES 
            (1, 'Bottle', wf."Product_Code", CONCAT_WS(', ', wf."Material", wf."Colour", wf."Finishing"), NULL::text, wf."Size"),
            (2, 'Cap', wf."Product_Code_2", CONCAT_WS(', ', wf."Material_2", wf."Colour_2", wf."Type_of_cap"), CONCAT('Dia: ', wf."Diameter"), NULL::text),
            (3, 'Pump', wf."Product_Code_3", CONCAT_WS(', ', wf."Colour_3", wf."Finishing_2"), CONCAT_WS(', ', wf."Tube_Length_from_gasket_mm", wf."DiameterNeck_mm"), NULL::text),
            (4, 'Tube', wf."Product_Code_4", CONCAT_WS(', ', wf."Material_3", wf."Type_of_cap_2", wf."Finishing_3"), NULL::text, CONCAT_WS(' x ', wf."SizeD", wf."SizeL")),
            (5, 'Unit Box', wf."Product_Code_5", CONCAT_WS(', ', wf."Material_4", wf."Colour_4", wf."Finishing_4"), CONCAT_WS(' x ', wf."DimensionL_mm", wf."DimensionW_mm", wf."DimensionH_mm"), NULL::text),
            (6, 'Inner Box', wf."Product_Code_6", CONCAT_WS(', ', wf."Material_5", wf."Colour_5", wf."Finishing_5"), CONCAT_WS(' x ', wf."DimensionL_mm_2", wf."DimensionW_mm_2", wf."Dimensionh_mm_2"), NULL::text),
            (7, 'Outer Carton', wf."Product_Code_7", CONCAT_WS(', ', wf."Material_6", wf."Colour_6", wf."Finishing_6"), CONCAT_WS(' x ', wf."Dimension_L_mm", wf."Dimension_W_mm", wf."Dimension_H_mm"), NULL::text),
            (8, 'Label 1', wf."Product_Code_8", CONCAT_WS(', ', wf."Material_7", wf."Colour_7", wf."Finishing_7"), CONCAT_WS(' x ', wf."Size_W_mm", wf."Size_H_mm"), NULL::text),
            (9, 'Label 2', wf."Product_Code_9", CONCAT_WS(', ', wf."Material_8", wf."Colour_8", wf."Finishing_8"), CONCAT_WS(' x ', wf."Size_W_mm_2", wf."Size_H_mm_2"), NULL::text),
            (10, 'Shrink Wrapper', wf."Product_Code_10", CONCAT_WS(', ', wf."Material_9", wf."Colour_9", wf."Finishing_9"), CONCAT_WS(' x ', wf."Dimension_L_mm_2", wf."Dimension_W_mm_2", wf."Dimension_H_mm_2"), NULL::text),
            (11, 'Others', wf."Product_Code_11", CONCAT_WS(', ', wf."Material_10", wf."Colour_10", wf."Finishing_10"), wf."Dimension", NULL::text)
    ) AS c(slot_number, component_name, product_code, specs, dimension, size)
    LEFT JOIN dbo.ezfb_f271d1d3_items mf 
      ON c.product_code = mf."Material_Code"
    WHERE wf.item_id = %s
      AND c.product_code IS NOT NULL AND c.product_code <> ''
    ORDER BY c.slot_number;
    """
    with get_tenant_db_connection(tenant_id) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (ticket_id,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def create_packaging_ticket(ticket_data: Dict[str, Any], tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    POST method: Creates a new requisition ticket in dbo.ezfb_aa038e45_items.
    Database trigger automatically syncs and creates master items in dbo.ezfb_f271d1d3_items.
    """
    ticket_id = ticket_data.get("item_id") or str(uuid.uuid4())
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    record = {
        "item_id": ticket_id,
        "Action_Type": ticket_data.get("Action_Type", "New Packaging Requisition"),
        "Company_Unit": ticket_data.get("Company_Unit", ""),
        "Product_Type": ticket_data.get("Product_Type", "Single"),
        "Brand": ticket_data.get("Brand", ""),
        "Variant": ticket_data.get("Variant", ""),
        "Type_of_Product": ticket_data.get("Type_of_Product", ""),
        "Size": ticket_data.get("Size", ""),
        "System_Product_NameERP": ticket_data.get("System_Product_NameERP", ""),
        "Customer_Company_Name": ticket_data.get("Customer_Company_Name", ""),

        # Slot 1
        "Status": ticket_data.get("Status", "New"),
        "Product_Code": ticket_data.get("Product_Code"),
        "Supplier_Pdt_Code": ticket_data.get("Supplier_Pdt_Code"),
        "Weight_g": ticket_data.get("Weight_g"),
        "Material": ticket_data.get("Material"),
        "Colour": ticket_data.get("Colour"),
        "Finishing": ticket_data.get("Finishing"),
        "Remark": ticket_data.get("Remark"),

        # Slot 2
        "Status_2": ticket_data.get("Status_2", "New"),
        "Product_Code_2": ticket_data.get("Product_Code_2"),
        "Supplier_Pdt_Code_2": ticket_data.get("Supplier_Pdt_Code_2"),
        "Diameter": ticket_data.get("Diameter"),
        "Material_2": ticket_data.get("Material_2"),
        "Colour_2": ticket_data.get("Colour_2"),
        "Type_of_cap": ticket_data.get("Type_of_cap"),
        "Remark_2": ticket_data.get("Remark_2"),

        # Slot 3
        "Status_3": ticket_data.get("Status_3", "New"),
        "Product_Code_3": ticket_data.get("Product_Code_3"),
        "Supplier_Pdt_Code_3": ticket_data.get("Supplier_Pdt_Code_3"),
        "Tube_Length_from_gasket_mm": ticket_data.get("Tube_Length_from_gasket_mm"),
        "DiameterNeck_mm": ticket_data.get("DiameterNeck_mm"),
        "Colour_3": ticket_data.get("Colour_3"),
        "Finishing_2": ticket_data.get("Finishing_2"),
        "Remark_3": ticket_data.get("Remark_3"),

        # Slot 4
        "Status_4": ticket_data.get("Status_4", "New"),
        "Product_Code_4": ticket_data.get("Product_Code_4"),
        "Supplier_Pdt_Code_4": ticket_data.get("Supplier_Pdt_Code_4"),
        "SizeD": ticket_data.get("SizeD"),
        "SizeL": ticket_data.get("SizeL"),
        "Material_3": ticket_data.get("Material_3"),
        "Type_of_cap_2": ticket_data.get("Type_of_cap_2"),
        "Finishing_3": ticket_data.get("Finishing_3"),
        "Remark_4": ticket_data.get("Remark_4"),

        # Slot 5
        "Status_5": ticket_data.get("Status_5", "New"),
        "Product_Code_5": ticket_data.get("Product_Code_5"),
        "DimensionL_mm": ticket_data.get("DimensionL_mm"),
        "DimensionW_mm": ticket_data.get("DimensionW_mm"),
        "DimensionH_mm": ticket_data.get("DimensionH_mm"),
        "Material_4": ticket_data.get("Material_4"),
        "Finishing_4": ticket_data.get("Finishing_4"),
        "Colour_4": ticket_data.get("Colour_4"),
        "Remark_5": ticket_data.get("Remark_5"),

        # Slot 6
        "Status_6": ticket_data.get("Status_6", "New"),
        "Product_Code_6": ticket_data.get("Product_Code_6"),
        "DimensionL_mm_2": ticket_data.get("DimensionL_mm_2"),
        "DimensionW_mm_2": ticket_data.get("DimensionW_mm_2"),
        "Dimensionh_mm_2": ticket_data.get("Dimensionh_mm_2"),
        "Material_5": ticket_data.get("Material_5"),
        "Finishing_5": ticket_data.get("Finishing_5"),
        "Colour_5": ticket_data.get("Colour_5"),
        "Remark_6": ticket_data.get("Remark_6"),

        # Slot 7
        "Status_7": ticket_data.get("Status_7", "New"),
        "Product_Code_7": ticket_data.get("Product_Code_7"),
        "Supplier_Pdt_Code_5": ticket_data.get("Supplier_Pdt_Code_5"),
        "Dimension_L_mm": ticket_data.get("Dimension_L_mm"),
        "Dimension_W_mm": ticket_data.get("Dimension_W_mm"),
        "Dimension_H_mm": ticket_data.get("Dimension_H_mm"),
        "Material_6": ticket_data.get("Material_6"),
        "Colour_6": ticket_data.get("Colour_6"),
        "Finishing_6": ticket_data.get("Finishing_6"),
        "Remark_7": ticket_data.get("Remark_7"),

        # Slot 8
        "Status_8": ticket_data.get("Status_8", "New"),
        "Product_Code_8": ticket_data.get("Product_Code_8"),
        "Type": ticket_data.get("Type"),
        "Size_W_mm": ticket_data.get("Size_W_mm"),
        "Size_H_mm": ticket_data.get("Size_H_mm"),
        "Material_7": ticket_data.get("Material_7"),
        "Finishing_7": ticket_data.get("Finishing_7"),
        "Colour_7": ticket_data.get("Colour_7"),
        "Remark_8": ticket_data.get("Remark_8"),

        # Slot 9
        "Status_9": ticket_data.get("Status_9", "New"),
        "Product_Code_9": ticket_data.get("Product_Code_9"),
        "Size_W_mm_2": ticket_data.get("Size_W_mm_2"),
        "Size_H_mm_2": ticket_data.get("Size_H_mm_2"),
        "Material_8": ticket_data.get("Material_8"),
        "Finishing_8": ticket_data.get("Finishing_8"),
        "Colour_8": ticket_data.get("Colour_8"),
        "Remark_9": ticket_data.get("Remark_9"),

        # Slot 10
        "Status_10": ticket_data.get("Status_10", "New"),
        "Product_Code_10": ticket_data.get("Product_Code_10"),
        "Type_2": ticket_data.get("Type_2"),
        "Others_specify": ticket_data.get("Others_specify"),
        "Dimension_L_mm_2": ticket_data.get("Dimension_L_mm_2"),
        "Dimension_W_mm_2": ticket_data.get("Dimension_W_mm_2"),
        "Dimension_H_mm_2": ticket_data.get("Dimension_H_mm_2"),
        "Thickness_mm": ticket_data.get("Thickness_mm"),
        "Material_9": ticket_data.get("Material_9"),
        "Finishing_9": ticket_data.get("Finishing_9"),
        "Colour_9": ticket_data.get("Colour_9"),
        "Remark_10": ticket_data.get("Remark_10"),

        # Slot 11
        "Status_11": ticket_data.get("Status_11", "New"),
        "Product_Code_11": ticket_data.get("Product_Code_11"),
        "Type_3": ticket_data.get("Type_3"),
        "Dimension": ticket_data.get("Dimension"),
        "Material_10": ticket_data.get("Material_10"),
        "Finishing_10": ticket_data.get("Finishing_10"),
        "Colour_10": ticket_data.get("Colour_10"),
        "Remark_11": ticket_data.get("Remark_11"),

        "created_at": now_iso,
        "created_by": ticket_data.get("created_by", "frontend-user"),
        "modified_by": "0",
        "is_deleted": False,
        "today_task": True,
        "is_marked": False
    }

    active_keys = [k for k, v in record.items() if v is not None]
    cols_sql = ', '.join([f'"{k}"' if k not in ("item_id", "created_at", "created_by", "modified_by", "is_deleted", "today_task", "is_marked") else k for k in active_keys])
    vals_placeholders = ', '.join(['%s'] * len(active_keys))
    vals = [record[k] for k in active_keys]

    insert_sql = f'INSERT INTO dbo.ezfb_aa038e45_items ({cols_sql}) VALUES ({vals_placeholders}) RETURNING item_id;'

    with get_tenant_db_connection(tenant_id) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(insert_sql, vals)
            created_id = cur.fetchone()[0]

    # Fetch synced components immediately to return to caller
    synced_components = get_ticket_matched_master_items(created_id, tenant_id)

    return {
        "success": True,
        "ticket_id": str(created_id),
        "message": "Requisition ticket created and automatically synced to Master Catalog",
        "components": synced_components
    }
