"""Fill a caller-supplied pdfme template (templateJson) with estimator formData."""
from __future__ import annotations

import io
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from reportlab.pdfgen import canvas

from app.ftl.key_format import title_key
from app.pdf_skills.template_renderer import (
    convert_page_measure,
    detect_page_unit,
    is_pdfme_template,
    measure_table_height_mm,
    parse_page_padding,
    render_schema_page,
    resolve_table_rows,
    safe_float,
)

FOOTER_ZONE_MM = 25.0
TABLE_GAP_MM = 3.0
_BLANKABLE_TYPES = {"text", "multivariabletext", "image", "table"}

Page = Tuple[List[Dict[str, Any]], Dict[str, Any]]


def prepare_form_data(value: Any) -> Any:
    """Keep caller keys and add public-name aliases (``customer_name`` -> ``Company Name``)."""
    if isinstance(value, list):
        return [prepare_form_data(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: Dict[str, Any] = {}
    for key, raw in value.items():
        out[key] = prepare_form_data(raw)
    for key, raw in value.items():
        alias = title_key(key)
        if alias not in out:
            out[alias] = out[key]
    return out


def _format_top_level(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: (f"{v:,.2f}" if isinstance(v, float) else v)
        for k, v in data.items()
    }


def _blank_value_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Missing formData must render blank, not the template's sample content."""
    out = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        field = deepcopy(field)
        ftype = str(field.get("type", "text")).lower()
        if field.get("dataKey") and not field.get("readOnly") and ftype in _BLANKABLE_TYPES:
            field["content"] = [] if ftype == "table" else ""
        out.append(field)
    return out


def _y(field: Dict[str, Any]) -> float:
    return safe_float((field.get("position") or {}).get("y"), 0.0)


def _paginate(fields: List[Dict[str, Any]], data: Dict[str, Any], page_w: float, page_h: float, unit: str) -> List[Page]:
    table = next((f for f in fields if str(f.get("type", "")).lower() == "table"), None)
    if table is None:
        return [(fields, data)]

    to_unit = 1.0 if unit == "mm" else 72.0 / 25.4
    table_y = _y(table)
    table_w_pts = convert_page_measure(safe_float(table.get("width"), page_w), unit)
    rows = resolve_table_rows(table, data)

    def height(chunk: List[List[str]]) -> float:
        return measure_table_height_mm(table, chunk, table_w_pts) * to_unit

    others = [f for f in fields if f is not table]
    above = [f for f in others if _y(f) <= table_y]
    below = [f for f in others if _y(f) > table_y]
    footer_top = page_h - FOOTER_ZONE_MM * to_unit
    footer = [f for f in below if _y(f) >= footer_top]
    summary = [f for f in below if _y(f) < footer_top]

    gap = TABLE_GAP_MM * to_unit
    last_limit = min((_y(f) for f in summary or footer), default=page_h - 10 * to_unit) - table_y - gap
    mid_limit = min((_y(f) for f in footer), default=page_h - 10 * to_unit) - table_y - gap

    if not rows or height(rows) <= last_limit:
        return [(fields, data)]

    chunks: List[List[List[str]]] = []
    remaining = rows
    while remaining:
        if height(remaining) <= last_limit:
            chunks.append(remaining)
            break
        take = 1
        while take < len(remaining) and height(remaining[: take + 1]) <= mid_limit:
            take += 1
        chunks.append(remaining[:take])
        remaining = remaining[take:]
        if not remaining:
            # The final chunk must still leave room for the summary block.
            chunks.append([])

    keys = {k for k in (table.get("dataKey"), table.get("name")) if k}
    pages: List[Page] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        page_fields = above + [table] + footer + (summary if idx == total else [])
        page_fields = page_fields + [_page_number_field(idx, total, page_w, page_h, to_unit)]
        page_data = dict(data)
        for key in keys:
            page_data[key] = chunk
        pages.append((page_fields, page_data))
    return pages


def _page_number_field(idx: int, total: int, page_w: float, page_h: float, to_unit: float) -> Dict[str, Any]:
    return {
        "name": "PageNumberNote",
        "type": "text",
        "readOnly": True,
        "content": f"Page {idx} of {total}",
        "position": {"x": page_w - 60 * to_unit, "y": page_h - 6 * to_unit},
        "width": 50 * to_unit,
        "height": 5 * to_unit,
        "fontSize": 7,
        "alignment": "right",
    }


def render_template_pdf(template: Dict[str, Any], form_data: Dict[str, Any], title: Optional[str] = None) -> Tuple[bytes, int]:
    """Return ``(pdf_bytes, page_count)`` for ``template`` filled with ``form_data``."""
    if not is_pdfme_template(template) or not template["schemas"]:
        raise ValueError("templateJson must be a pdfme template with a non-empty 'schemas' list.")
    if not isinstance(form_data, dict):
        raise ValueError("formData must be a JSON object.")

    schemas = [_blank_value_fields(page) for page in template["schemas"] if isinstance(page, list)]
    if not schemas:
        raise ValueError("templateJson 'schemas' must contain at least one page of fields.")

    base_pdf = template.get("basePdf") if isinstance(template.get("basePdf"), dict) else {}
    page_w = safe_float(base_pdf.get("width"), 210.0)
    page_h = safe_float(base_pdf.get("height"), 297.0)
    unit = detect_page_unit(page_w, page_h)
    page_w_pts = convert_page_measure(page_w, unit)
    page_h_pts = convert_page_measure(page_h, unit)
    padding = parse_page_padding(base_pdf.get("padding"), unit)

    data = _format_top_level(prepare_form_data(form_data))
    if len(schemas) == 1:
        pages = _paginate(schemas[0], data, page_w, page_h, unit)
    else:
        pages = [(page, data) for page in schemas]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w_pts, page_h_pts))
    if title:
        c.setTitle(title)
    for idx, (fields, page_data) in enumerate(pages):
        if idx:
            c.showPage()
        render_schema_page(c, fields, page_data, page_w_pts, page_h_pts, unit, padding)
    c.save()
    return buf.getvalue(), len(pages)
