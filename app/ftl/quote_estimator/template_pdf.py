"""Fill a caller-supplied pdfme template (templateJson) with estimator formData."""
from __future__ import annotations

import io
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from reportlab.pdfgen import canvas

from app.ftl.key_format import internal_key, title_key
from app.pdf_skills.template_renderer import (
    convert_page_measure,
    detect_page_unit,
    is_pdfme_template,
    measure_table_height_mm,
    parse_page_padding,
    render_schema_page,
    resolve_metadata_value,
    resolve_table_rows,
    safe_float,
)

FOOTER_ZONE_MM = 25.0
TABLE_GAP_MM = 3.0
# Images are not blanked: an embedded logo must still render when formData has no value for it.
_BLANKABLE_TYPES = {"text", "multivariabletext", "table"}

Page = Tuple[List[Dict[str, Any]], Dict[str, Any]]


def prepare_form_data(value: Any) -> Any:
    """Keep caller keys and add public and internal aliases
    (``customer_name`` <-> ``Company Name``, ``Order Number`` -> ``estimate_number``)."""
    if isinstance(value, list):
        return [prepare_form_data(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: Dict[str, Any] = {}
    for key, raw in value.items():
        out[key] = prepare_form_data(raw)
    for key in value:
        for alias in (title_key(key), internal_key(key)):
            if alias not in out:
                out[alias] = out[key]
    if out.get("Company Name") and "Customer" not in out:
        out["Customer"] = out["Company Name"]
    if out.get("Phone Number") and "Phone" not in out:
        out["Phone"] = out["Phone Number"]
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


def _is_empty_value(value: Any) -> bool:
    return value in (None, "", [], {}) or (isinstance(value, str) and not value.strip())


def _should_hide(field: Dict[str, Any], data: Dict[str, Any]) -> bool:
    flag = field.get("hideIfEmpty")
    if not flag:
        return False
    if flag is True:
        return _is_empty_value(resolve_metadata_value(field, data, ""))
    key = str(flag)
    if key in data:
        return _is_empty_value(data.get(key))
    wanted = key.strip().lower()
    for name, value in data.items():
        if str(name).strip().lower() == wanted:
            return _is_empty_value(value)
    return True


def _drop_hidden(fields: List[Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [field for field in fields if not _should_hide(field, data)]


def _field_height(field: Dict[str, Any], data: Dict[str, Any]) -> float:
    height = safe_float(field.get("height"), 0.0)
    if str(field.get("type", "")).lower() == "container":
        return max(height, 20.0)
    if field.get("dynamicHeight"):
        text = str(resolve_metadata_value(field, data, field.get("content") or "") or "")
        lines = max(1, text.count("\n") + 1)
        return max(height, 4.2 * lines + 8.0)
    return height or 5.0


def _place_flow_fields(
    flow_fields: List[Dict[str, Any]],
    bottoms: Dict[str, float],
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Set y from flowAfter + flowGap so totals/notes sit under a variable-height table."""
    placed: List[Dict[str, Any]] = []
    remaining = [deepcopy(field) for field in flow_fields]
    for _ in range(len(remaining) + 2):
        if not remaining:
            break
        leftover: List[Dict[str, Any]] = []
        progressed = False
        for field in remaining:
            anchor = str(field.get("flowAfter") or "")
            if anchor not in bottoms:
                leftover.append(field)
                continue
            pos = dict(field.get("position") or {})
            pos["y"] = bottoms[anchor] + safe_float(field.get("flowGap"), 0.0)
            field["position"] = pos
            bottoms[str(field.get("name") or "")] = pos["y"] + _field_height(field, data)
            placed.append(field)
            progressed = True
        remaining = leftover
        if not progressed:
            placed.extend(remaining)
            break
    return placed


def _paginate(fields: List[Dict[str, Any]], data: Dict[str, Any], page_w: float, page_h: float, unit: str) -> List[Page]:
    table = next((f for f in fields if str(f.get("type", "")).lower() == "table"), None)
    if table is None:
        return [(_place_flow_fields([f for f in fields if f.get("flowAfter")], {}, data) or fields, data)]

    to_unit = 1.0 if unit == "mm" else 72.0 / 25.4
    table_y = _y(table)
    table_w_pts = convert_page_measure(safe_float(table.get("width"), page_w), unit)
    rows = resolve_table_rows(table, data)

    def height(chunk: List[List[str]]) -> float:
        return measure_table_height_mm(table, chunk, table_w_pts) * to_unit

    others = [f for f in fields if f is not table]
    flow = [f for f in others if f.get("flowAfter")]
    fixed = [f for f in others if not f.get("flowAfter")]
    above = [f for f in fixed if _y(f) <= table_y]
    below = [f for f in fixed if _y(f) > table_y]
    footer_top = page_h - FOOTER_ZONE_MM * to_unit
    footer = [f for f in below if _y(f) >= footer_top]
    summary = [f for f in below if _y(f) < footer_top]

    gap = TABLE_GAP_MM * to_unit
    flow_reserve = sum(safe_float(f.get("flowGap"), 0.0) + _field_height(f, data) for f in flow)
    if flow:
        last_limit = page_h - FOOTER_ZONE_MM * to_unit - flow_reserve - table_y - gap
    else:
        last_limit = min((_y(f) for f in summary or footer), default=page_h - 10 * to_unit) - table_y - gap
    mid_limit = min((_y(f) for f in footer), default=page_h - 10 * to_unit) - table_y - gap

    if not rows or height(rows) <= last_limit:
        chunks = [rows]
        if flow and table_y + height(rows) + flow_reserve > page_h - FOOTER_ZONE_MM * to_unit:
            chunks.append([])
    else:
        chunks = []
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
                table_bottom = table_y + height(chunks[-1])
                needs_summary_page = (not flow) or (
                    table_bottom + flow_reserve > page_h - FOOTER_ZONE_MM * to_unit
                )
                if needs_summary_page:
                    chunks.append([])

    keys = {k for k in (table.get("dataKey"), table.get("name")) if k}
    pages: List[Page] = []
    total = len(chunks)
    table_name = str(table.get("name") or "Items Table")
    for idx, chunk in enumerate(chunks, start=1):
        page_data = dict(data)
        for key in keys:
            page_data[key] = chunk
        last = idx == total
        extras = (summary if last and not flow else [])
        flow_placed: List[Dict[str, Any]] = []
        show_table = bool(chunk) or not last or total == 1
        if last and flow:
            bottoms = {table_name: table_y + (height(chunk) if chunk else 0)}
            if not chunk:
                below_header = max((_y(f) + _field_height(f, page_data) for f in above), default=table_y)
                bottoms[table_name] = below_header + gap
            flow_placed = _place_flow_fields(flow, bottoms, page_data)
        page_fields = above + ([table] if show_table else []) + footer + extras + flow_placed
        if total > 1:
            page_fields = page_fields + [_page_number_field(idx, total, page_w, page_h, to_unit)]
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


def _is_zero_or_blank(value: Any) -> bool:
    text = str(value if value is not None else "").replace(",", "").replace("$", "").strip()
    if not text:
        return True
    try:
        return float(text) == 0
    except ValueError:
        return False


def _expand_containers(fields: List[Dict[str, Any]], data: Dict[str, Any], to_unit: float) -> List[Dict[str, Any]]:
    """Replace each ``container`` field with the rectangle and text fields the renderer draws."""
    out: List[Dict[str, Any]] = []
    for field in fields:
        if str(field.get("type", "")).lower() == "container":
            out.extend(_container_fields(field, data, to_unit))
        else:
            out.append(field)
    return out


def _container_fields(box: Dict[str, Any], data: Dict[str, Any], to_unit: float) -> List[Dict[str, Any]]:
    """A container is a background box whose ``key_value`` children are stacked as equal-height rows,
    label on the left and the formData value on the right."""
    pos = box.get("position") or {}
    x, y = safe_float(pos.get("x"), 0.0), safe_float(pos.get("y"), 0.0)
    w, h = safe_float(box.get("width"), 0.0), safe_float(box.get("height"), 0.0)
    prefix = f"KV{box.get('name') or 'Container'}"

    def rect(name: str, top: float, height: float, src: Dict[str, Any]) -> Dict[str, Any]:
        field = {"name": name, "type": "rectangle", "position": {"x": x, "y": top}, "width": w, "height": height}
        for key in ("backgroundColor", "borderColor", "borderWidth"):
            if src.get(key):
                field[key] = src[key]
        return field

    shapes: List[Dict[str, Any]] = []
    if box.get("backgroundColor") or box.get("borderColor"):
        shapes.append(rect(f"{prefix}Background", y, h, box))

    rows = []
    for child in box.get("children") or []:
        if not isinstance(child, dict) or str(child.get("type", "")).lower() != "key_value":
            continue
        value = resolve_metadata_value(child, data, "")
        if child.get("hideIfZero") and _is_zero_or_blank(value):
            continue
        rows.append((child, value))
    if not rows:
        return shapes

    pad = 3.0 * to_unit
    half = max(0.0, w - 2 * pad) / 2
    row_h = h / len(rows)
    texts: List[Dict[str, Any]] = []
    for idx, (child, value) in enumerate(rows):
        top = y + idx * row_h
        name = f"{prefix}{child.get('name') or idx}"
        row_src = dict(child)
        font_color = child.get("textColor") or child.get("fontColor") or "#000000"
        if (
            not box.get("backgroundColor")
            and not child.get("backgroundColor")
            and str(child.get("label") or "").strip().lower().startswith("total")
        ):
            row_src["backgroundColor"] = "#102C47"
            font_color = "#FFFFFF"
        if row_src.get("backgroundColor"):
            shapes.append(rect(f"{name}Background", top, row_h, row_src))
        style = {
            "type": "text",
            "readOnly": True,
            "preserveColors": True,
            "fontSize": child.get("fontSize", 9),
            "fontName": child.get("fontName", "Helvetica"),
            "fontColor": font_color,
            "verticalAlignment": "middle",
            "width": half,
            "height": row_h,
        }
        texts.append({**style, "name": f"{name}Label", "content": str(child.get("label") or ""),
                      "alignment": "left", "position": {"x": x + pad, "y": top}})
        texts.append({**style, "name": f"{name}Value", "content": "" if value is None else str(value),
                      "alignment": "right", "position": {"x": x + pad + half, "y": top}})
    return shapes + texts


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
    to_unit = 1.0 if unit == "mm" else 72.0 / 25.4
    schemas = [_drop_hidden(page, data) for page in schemas]
    if len(schemas) == 1:
        pages = _paginate(schemas[0], data, page_w, page_h, unit)
    else:
        pages = [(page, data) for page in schemas]
    pages = [(_expand_containers(fields, page_data, to_unit), page_data) for fields, page_data in pages]

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
