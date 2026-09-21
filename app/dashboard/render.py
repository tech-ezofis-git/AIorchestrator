"""Build a self-contained HTML fragment for the UI team from a hydrated dashboard."""
from __future__ import annotations

import html
import math
import re
from typing import Any

from app.dashboard.tokens import ACCENT_PRIMARY, AXIS_MUTED, AXIS_TEXT_LIGHT, CHART_PALETTE, ERROR_MAIN, GRID_LIGHT, PRIMARY

_CSS = """
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@500;600;700&display=swap");
.ez-dash{
  --ez-violet:var(--accent-primary, var(--primary-9, #7c5cff));
  --ez-cyan:var(--primary, var(--secondary-9, var(--accent-primary, #00bcd4)));
  --ez-bg:var(--surface, #ffffff);
  --ez-surface:var(--surface-raised, var(--surface, #ffffff));
  --ez-muted-bg:var(--surface-muted, var(--surface-hover, var(--gray-2, #eef0f3)));
  --ez-ink:var(--text-primary, var(--gray-13, #0f172a));
  --ez-sub:var(--text-secondary, var(--gray-11, #334155));
  --ez-muted:var(--text-muted, var(--gray-9, #64748b));
  --ez-line:var(--border-default, #d1d5db);
  --ez-line-strong:var(--border-strong, #9ca3af);
  --ez-ok:var(--success-main, var(--green-9, #30a46c));
  --ez-warn:var(--warning-main, var(--orange-9, #f76b15));
  --ez-err:var(--error-main, var(--red-9, #ef4444));
  --ez-info:var(--info-main, var(--blue-9, #0090ff));
  --ez-soft:var(--accent-soft, var(--primary-3, #f3f0ff));
  --ez-shadow:0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.04);
  --ez-shadow-md:0 4px 14px -2px rgb(15 23 42 / 0.1);
  --ez-heading:Poppins,sans-serif;--ez-body:Inter,sans-serif;
  font-family:var(--ez-body);font-weight:450;color:var(--ez-ink);background:var(--ez-bg);
  border:0;border-radius:0;padding:16px;min-width:0
}
[data-mantine-color-scheme="dark"] .ez-dash,.ez-dash[data-mantine-color-scheme="dark"]{
  --ez-violet:var(--accent-primary, var(--primary-9, #a855f7));
  --ez-cyan:var(--primary, var(--secondary-9, var(--accent-primary, #00bcd4)));
  --ez-bg:#000000;
  --ez-surface:var(--surface-primary, var(--surface, #121113));
  --ez-muted-bg:#1a191b;
  --ez-ink:var(--text-primary, #eeeef0);
  --ez-sub:var(--text-secondary, #b5b2bc);
  --ez-muted:var(--text-muted, #6f6d78);
  --ez-line:#232225;
  --ez-line-strong:#2b292d;
  --ez-ok:var(--success-main, #3dd68c);
  --ez-warn:var(--warning-main, var(--orange-9, #f76b15));
  --ez-err:var(--error-main, var(--red-9, #e5484d));
  --ez-info:var(--info-main, var(--blue-9, #0090ff));
  --ez-soft:var(--accent-soft, #2e1945);
  --ez-shadow:0 1px 2px 0 rgb(0 0 0 / 0.25);
  --ez-shadow-md:0 4px 12px -2px rgb(0 0 0 / 0.35)
}
.ez-dash *{box-sizing:border-box}
.ez-acc-list{display:flex;flex-direction:column;gap:14px;margin:0}
.ez-acc{display:flex;flex-direction:column;gap:14px;background:transparent;border:0;border-radius:0;box-shadow:none}
.ez-acc-btn{position:relative;overflow:hidden;width:100%;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:24px;padding:16px;border:1px solid var(--ez-line);border-radius:8px;background:#fff;background:var(--ez-surface);color:var(--ez-ink);box-shadow:0 1px 2px 0 rgba(0,0,0,0.03);cursor:pointer;text-align:left;font:inherit;outline:none;user-select:none;transition:all .3s ease}
.ez-acc-btn:hover{border-color:rgba(124,92,255,0.4);background:rgba(124,92,255,0.02);box-shadow:0 1px 3px 0 rgba(0,0,0,0.06)}
.ez-acc-btn:active{transform:scale(0.99)}
.ez-acc.is-open .ez-acc-btn{border-color:var(--ez-line)}
.ez-acc.is-open .ez-acc-btn:hover{border-color:rgba(124,92,255,0.4);background:rgba(124,92,255,0.02);box-shadow:0 1px 3px 0 rgba(0,0,0,0.06)}
.ez-acc-copy{position:relative;z-index:10;min-width:0}
.ez-acc-title{font-family:var(--ez-heading);font-size:14px;font-weight:600;color:var(--ez-ink);margin:0;line-height:1.3}
.ez-acc-sub{font-family:var(--ez-body);font-size:11px;color:var(--ez-muted);margin:4px 0 0;line-height:1.4;white-space:normal}
.ez-acc-meta{position:relative;z-index:10;display:flex;flex-wrap:wrap;align-items:center;gap:24px}
.ez-acc-chip{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ez-violet);background:var(--ez-soft);border-radius:999px;padding:4px 8px;white-space:nowrap}
.ez-acc-metric{display:flex;flex-direction:column;gap:2px;text-align:right}
.ez-acc-metric-label{font-family:var(--ez-heading);font-size:10px;font-weight:500;letter-spacing:.05em;text-transform:uppercase;color:var(--ez-sub);line-height:1.2;white-space:nowrap}
.ez-acc-metric-value{font-size:15px;font-weight:600;color:var(--ez-violet);line-height:1.2;white-space:nowrap}
.ez-acc-metric-value.is-cyan{color:var(--ez-cyan)}
.ez-acc-metric-value.is-alert{color:var(--ez-err)}
.ez-acc-chevron-wrap{z-index:20;margin-left:8px;border-radius:8px;padding:6px;color:var(--ez-sub);display:flex;align-items:center;justify-content:center;transition:color .15s ease}
.ez-acc-chevron{width:20px;height:20px;color:var(--ez-violet);transition:transform .25s ease;flex-shrink:0}
.ez-acc.is-open .ez-acc-chevron{transform:rotate(180deg)}
.ez-acc-panel{display:none;padding:0;margin:0;background:transparent;border:0;flex-direction:column;gap:14px}
.ez-acc.is-open .ez-acc-panel{display:flex}
.ez-acc-panel>.ez-kpis,.ez-acc-panel>.ez-charts{margin-top:0}
.ez-acc-panel>.ez-insights{margin-top:0;padding-top:0;border-top:0}
.ez-kpis{display:grid;grid-template-columns:repeat(var(--ez-kpi-count,1),minmax(0,1fr));gap:16px}
@media(max-width:1024px){.ez-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:640px){.ez-kpis{grid-template-columns:1fr}}
.ez-kpi{cursor:pointer;border-radius:8px;border:1px solid var(--ez-line);border-top:3px solid var(--ez-violet);background:#fff;background:var(--ez-surface);padding:16px;box-shadow:0 1px 2px 0 rgba(0,0,0,0.03);transition:all .2s ease;display:flex;flex-direction:column;justify-content:space-between;min-height:92px}
.ez-kpi:hover{transform:translateY(-2px);box-shadow:0 4px 6px -1px rgba(0,0,0,0.06)}
.ez-kpi.is-active,.ez-kpi.is-selected{border-color:var(--ez-violet)!important;border-top:3px solid var(--ez-violet)!important;box-shadow:0 0 0 2px rgba(124,92,255,0.16),0 2px 6px rgba(124,92,255,0.06)!important}
.ez-kpi-label{font-family:var(--ez-heading);font-size:8px;font-weight:600;text-transform:uppercase;color:var(--ez-sub);letter-spacing:.04em;line-height:1.2}
.ez-kpi-value{font-family:var(--ez-heading);font-size:18px;font-weight:600;color:var(--ez-ink);margin:6px 0 0;line-height:1.2}
.ez-kpi-sub{margin-top:8px;display:flex;align-items:center;gap:6px;font-size:11px;font-weight:600;flex-wrap:nowrap}
.ez-kpi-badge{border-radius:4px;padding:2px 6px;line-height:1.25;display:inline-block;font-size:11px;font-weight:600}
.ez-kpi-badge.is-danger,.ez-kpi-badge.is-down{background:#fee2e2;color:#e5484d}
.ez-kpi-badge.is-success,.ez-kpi-badge.is-up{background:#d1fae5;color:#10b981}
.ez-kpi-sub-text{font-family:var(--ez-body);font-weight:400;color:var(--ez-muted);font-size:11px}
.ez-insights{min-width:0;overflow:hidden;white-space:normal}
.ez-insights-top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 16px;min-width:0}
.ez-insights-top>div{min-width:0;flex:1}
.ez-insights-title{font-family:var(--ez-heading);font-size:14px;font-weight:600;color:var(--ez-ink);margin:0;line-height:1.3}
.ez-insights-sub{font-family:var(--ez-body);font-size:11px;color:var(--ez-muted);margin:2px 0 0;white-space:normal;line-height:1.4;overflow-wrap:anywhere}
.ez-insights ul{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:0;min-width:0}
.ez-insights li{display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px dashed var(--ez-line);font-size:13px;color:var(--ez-sub);line-height:1.5;min-width:0;white-space:normal}
.ez-insights li:first-child{padding-top:0}
.ez-insights li:last-child{border-bottom:0;padding-bottom:0}
.ez-insights li > div{min-width:0;flex:1 1 auto;white-space:normal;overflow-wrap:anywhere;word-break:break-word}
.ez-spark-svg{width:16px;height:16px;margin-top:2px;flex-shrink:0;color:#00a2c7;animation:ez-pulse 2s cubic-bezier(0.4,0,0.6,1) infinite}
@keyframes ez-pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes ez-blink{0%,100%{opacity:1}50%{opacity:.4}}
.ez-live{font-family:var(--ez-body);font-size:10px;font-weight:600;letter-spacing:.04em;background:transparent;color:#c2410c;border:1px solid #fed7aa;border-radius:9999px;padding:2px 8px;flex-shrink:0;display:inline-flex;align-items:center;gap:6px}
.ez-live-dot{width:6px;height:6px;border-radius:50%;background:#ea580c;animation:ez-pulse 1.5s infinite;flex-shrink:0}
[data-mantine-color-scheme="dark"] .ez-live,.ez-dash[data-mantine-color-scheme="dark"] .ez-live{background:transparent;color:#fb923c;border-color:#7c2d12}
[data-mantine-color-scheme="dark"] .ez-live-dot,.ez-dash[data-mantine-color-scheme="dark"] .ez-live-dot{background:#f97316}
.ez-charts{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}
.ez-chart{background:#fff;background:var(--ez-surface);border:1px solid var(--ez-line);border-radius:8px;padding:20px;overflow:visible;position:relative;box-shadow:0 1px 2px 0 rgba(0,0,0,0.03);display:flex;flex-direction:column;justify-content:flex-start}
.ez-chart.span-2,.ez-chart.pos-full,.ez-chart.pos-bottom,.ez-chart.pos-top{grid-column:1/-1}
.ez-chart.pos-left{grid-column:span 8 / span 8}
.ez-chart.pos-right{grid-column:span 4 / span 4}
.ez-chart-title{font-family:var(--ez-heading);font-weight:600;font-size:14px;margin:0;color:var(--ez-ink);line-height:1.3}
.ez-chart-sub{font-family:var(--ez-body);font-size:11px;color:var(--ez-muted);margin:2px 0 16px;line-height:1.4}
.ez-tip{position:absolute;z-index:6;background:var(--chart-tooltip-bg, var(--gray-13, #1f2937));color:var(--chart-tooltip-text, var(--text-on-accent, #fff));font-size:11px;font-weight:600;padding:6px 8px;border-radius:8px;pointer-events:none;white-space:nowrap;box-shadow:var(--ez-shadow-md)}
.ez-tip[hidden]{display:none!important}
.ez-hit{cursor:pointer}
.ez-chart:hover .ez-hit{opacity:.42}
.ez-chart .ez-hit:hover{opacity:1;filter:brightness(1.06)}
.ez-donut{width:148px;height:148px;margin:4px auto 0;display:block}
.ez-donut-hole{pointer-events:none}
.ez-legend{list-style:none;padding:0;margin:12px 0 0;font-family:var(--ez-body);font-size:11px;color:var(--ez-sub);display:flex;flex-wrap:wrap;justify-content:space-around;gap:6px 12px;max-height:160px;overflow:auto}
.ez-legend li{display:flex;gap:6px;align-items:center;margin:2px 0;cursor:pointer}
.ez-legend li:hover{color:var(--ez-ink)}
.ez-swatch{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.ez-svg{width:100%;height:176px;max-height:176px;display:block}
.ez-lollipop{width:100%;max-height:200px;display:block}
.ez-radar{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.ez-radar svg{width:148px;height:148px;max-width:148px;max-height:148px;flex:0 0 148px;display:block}
.ez-empty{color:var(--ez-muted);font-size:12px}
@media(max-width:1024px){
  .ez-charts{grid-template-columns:1fr}
  .ez-chart,.ez-chart.pos-left,.ez-chart.pos-right{grid-column:1/-1}
}
@media(max-width:700px){
  .ez-acc-btn{grid-template-columns:minmax(0,1fr) 18px}
  .ez-acc-meta{grid-column:1/-1;justify-content:flex-start;flex-wrap:wrap}
  .ez-acc-metric{align-items:flex-start}
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt(item: dict[str, Any] | None, row: dict[str, Any] | None = None) -> str:
    if not item or item.get("value") is None:
        return "None"
    value = item.get("value")
    try:
        number = float(value)
        text = f"{number:,.2f}".rstrip("0").rstrip(".") if not number.is_integer() else f"{int(number):,}"
    except (TypeError, ValueError):
        text = str(value)
    unit = str((item.get("unit") if item else None) or (row.get("unit") if row else "") or "").strip()
    kpi_id = str((row.get("id") if row else "") or "").lower()
    label = str((row.get("label") if row else "") or "").lower()
    if unit == "%":
        return f"{text}%"
    is_non_currency = any(w in kpi_id or w in label for w in ["count", "invoices", "supplier_count", "records", "dpo", "days", "rate", "pct", "percent"])
    if unit in {"$", "USD"} or (not is_non_currency and any(w in kpi_id or w in label for w in ["ap", "total_ap", "payable", "amount", "spend", "cost", "margin", "overdue"])):
        return f"${text}"
    if unit in {"d", "days"} or "dpo" in kpi_id or "dpo" in label:
        return f"{text}d"
    return f"{text} {unit}" if unit else text


def _compact(n: Any) -> str:
    value = float(n or 0)
    abs_n = abs(value)
    if abs_n >= 1e6:
        return f"{value / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    if abs_n >= 1e3:
        return f"{value / 1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))


_TIP_MOVE = (
    "var h=event.target.closest('[data-tip]');"
    "var box=this.closest('.ez-chart');"
    "var t=box&&box.querySelector('.ez-tip');"
    "if(!t)return;"
    "if(!h){t.hidden=true;return;}"
    "t.hidden=false;"
    "t.textContent=h.getAttribute('data-tip');"
    "var r=box.getBoundingClientRect();"
    "t.style.left=(event.clientX-r.left+12)+'px';"
    "t.style.top=(event.clientY-r.top-28)+'px'"
)
_TIP_LEAVE = "var t=this.closest('.ez-chart')&&this.closest('.ez-chart').querySelector('.ez-tip');if(t)t.hidden=true"


def _tip_label(name: Any, value: Any) -> str:
    return f"{name} · {_compact(value)}"


def _data_tip(name: Any, value: Any) -> str:
    return f'data-tip="{_esc(_tip_label(name, value))}"'


def _plot_wrap(*chunks: str) -> str:
    inner = "\n".join(chunks)
    return (
        f'<div class="ez-chart-plot" onmousemove="{_TIP_MOVE}" onmouseleave="{_TIP_LEAVE}">\n'
        f"{inner}\n"
        "</div>"
    )


def _pie_path(cx: float, cy: float, radius: float, start_deg: float, end_deg: float) -> str:
    sweep = end_deg - start_deg
    if sweep >= 359.9:
        return (
            f"M {cx:.1f} {cy - radius:.1f} "
            f"A {radius:.1f} {radius:.1f} 0 1 1 {cx:.1f} {cy + radius:.1f} "
            f"A {radius:.1f} {radius:.1f} 0 1 1 {cx:.1f} {cy - radius:.1f}"
        )
    a0 = math.radians(start_deg - 90)
    a1 = math.radians(end_deg - 90)
    x0 = cx + radius * math.cos(a0)
    y0 = cy + radius * math.sin(a0)
    x1 = cx + radius * math.cos(a1)
    y1 = cy + radius * math.sin(a1)
    large = 1 if sweep > 180 else 0
    return f"M {cx:.1f} {cy:.1f} L {x0:.1f} {y0:.1f} A {radius:.1f} {radius:.1f} 0 {large} 1 {x1:.1f} {y1:.1f} Z"


def _nice_max(n: float) -> float:
    value = max(float(n or 0), 1.0)
    exp = 10 ** math.floor(math.log10(value))
    f = value / exp
    nice = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return nice * exp


def _theme_color(color: Any | None, *, fallback: str = ACCENT_PRIMARY) -> str:
    """Map known accent hexes to host theme CSS vars; keep other colors as-is."""
    if color is None or str(color).strip() == "":
        return fallback
    raw = str(color).strip()
    themed = {
        "#7c5cff": ACCENT_PRIMARY,
        "#a855f7": ACCENT_PRIMARY,
        "#7c3aed": ACCENT_PRIMARY,
        "#6a4cf0": ACCENT_PRIMARY,
        "#00bcd4": PRIMARY,
        "#00bbd3": PRIMARY,
        "#22d3ee": PRIMARY,
        "#e5484d": "var(--error-main, var(--red-9, #e5484d))",
        "#dc2626": "var(--error-main, var(--red-9, #e5484d))",
        "#30a46c": "var(--success-main, var(--green-9, #30a46c))",
        "#f76b15": "var(--warning-main, var(--orange-9, #f76b15))",
        "#0090ff": "var(--info-main, var(--blue-9, #0090ff))",
    }
    return themed.get(raw.lower(), raw)


def _colors(row: dict[str, Any], chart: dict[str, Any]) -> list[str]:
    raw_accent = row.get("color") or chart.get("color")
    accent = _theme_color(raw_accent, fallback="") if raw_accent else ""
    palette = list(CHART_PALETTE)
    if accent:
        palette = [accent] + [c for c in palette if c.lower() != accent.lower()]
    return palette


def _lines(*chunks: str) -> list[str]:
    out: list[str] = []
    for chunk in chunks:
        out.extend(str(chunk).splitlines() or [""])
    return out


def _indent(level: int, chunks: list[str]) -> list[str]:
    pad = "  " * level
    return [pad + line if line else "" for line in chunks]


def _trend_class(item: dict[str, Any]) -> str:
    pct = item.get("trend_pct")
    try:
        number = float(pct)
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return " is-up"
    if number < 0:
        return " is-down"
    return ""


def _chart_block(
    row: dict[str, Any],
    chart: dict[str, Any],
    *,
    pos: str | None = None,
    show_heading: bool = True,
) -> list[str]:
    use_pos = str(row.get("position") or "full") if pos is None else pos
    span = row.get("span")
    cls = "ez-chart"
    if pos is None and (span == 2 or use_pos in {"full", "bottom", "top"}):
        cls += " span-2"
    if use_pos in {"left", "right", "full", "bottom", "top"}:
        cls += f" pos-{_esc(use_pos)}"
    lines = [f'<div class="{cls}">']
    if show_heading:
        lines.append(
            f'  <h3 class="ez-chart-title">{_esc(row.get("title") or row.get("label") or row.get("id"))}</h3>'
        )
        desc = str(row.get("description") or "").strip()
        if desc:
            lines.append(f'  <div class="ez-chart-sub">{_esc(desc)}</div>')
    lines.extend(_indent(1, _lines(_chart_body(row, chart or {}))))
    lines.append('  <div class="ez-tip" hidden></div>')
    lines.append("</div>")
    return lines


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    if not items:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _spread(items: list[Any], n: int) -> list[list[Any]]:
    if n <= 0:
        return []
    buckets: list[list[Any]] = [[] for _ in range(n)]
    if not items:
        return buckets
    base, rem = divmod(len(items), n)
    idx = 0
    for i in range(n):
        take = base + (1 if i < rem else 0)
        buckets[i] = items[idx : idx + take]
        idx += take
    return buckets


def _fmt_stat_value(value: Any) -> str:
    if value is None:
        return "None"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_n = abs(number)
    if abs_n >= 1000:
        return _compact(number)
    text = f"{number:,.2f}".rstrip("0").rstrip(".") if not number.is_integer() else f"{int(number):,}"
    return text


def _chart_header_stats(row: dict[str, Any], chart: dict[str, Any]) -> list[dict[str, str]]:
    """Top datapoints from this chart for the accordion header (not dashboard KPIs)."""
    stats: list[dict[str, str]] = []
    series = [item for item in (chart.get("series") or []) if isinstance(item, dict)]
    if series:
        ranked = sorted(series, key=lambda item: float(item.get("value") or 0), reverse=True)
        for index, item in enumerate(ranked[:3]):
            name = str(item.get("name") or "Item").strip() or "Item"
            stats.append(
                {
                    "label": name.upper()[:20],
                    "value": _fmt_stat_value(item.get("value")),
                    "tone": "cyan" if index == 0 else "violet",
                }
            )
        return stats

    categories = list(chart.get("categories") or [])
    values = list(chart.get("values") or chart.get("bars") or [])
    pairs: list[tuple[str, float]] = []
    for index, cat in enumerate(categories):
        raw = values[index] if index < len(values) else 0
        try:
            number = float(raw or 0)
        except (TypeError, ValueError):
            number = 0.0
        pairs.append((str(cat or f"Item {index + 1}"), number))
    pairs.sort(key=lambda item: item[1], reverse=True)
    for index, (name, number) in enumerate(pairs[:3]):
        stats.append(
            {
                "label": name.upper()[:20],
                "value": _fmt_stat_value(number),
                "tone": "cyan" if index == 0 else "violet",
            }
        )
    if stats:
        return stats

    if values:
        nums = []
        for raw in values:
            try:
                nums.append(float(raw or 0))
            except (TypeError, ValueError):
                continue
        if nums:
            stats.append({"label": "TOTAL", "value": _fmt_stat_value(sum(nums)), "tone": "cyan"})
            stats.append({"label": "MAX", "value": _fmt_stat_value(max(nums)), "tone": "violet"})
            stats.append({"label": "POINTS", "value": str(len(nums)), "tone": "violet"})
    return stats[:3]


def _section_title(charts: list[dict[str, Any]], index: int) -> str:
    titles = [str(c.get("title") or "").strip() for c in charts if str(c.get("title") or "").strip()]
    if len(titles) >= 2:
        joined = f"{titles[0]} & {titles[1]}"
        return joined if len(joined) <= 56 else titles[0]
    if titles:
        return titles[0]
    return f"Charts {index + 1}"


def _section_subtitle(charts: list[dict[str, Any]]) -> str:
    for row in charts:
        text = str(row.get("description") or "").strip()
        if text:
            return text if len(text) <= 110 else text[:107] + "…"
    return "Click to open this chart."


def _chart_type_label(charts: list[dict[str, Any]]) -> str:
    raw = str((charts[0].get("type") if charts else "") or "chart").strip().lower()
    labels = {
        "donut": "Donut",
        "pie": "Pie",
        "column": "Column",
        "bar": "Bar",
        "line": "Line",
        "area": "Area",
        "lollipop": "Lollipop",
        "radar": "Radar",
        "gauge": "Gauge",
        "hbar": "Bar",
    }
    return labels.get(raw, raw.title() or "Chart")


def _build_sections(chart_meta: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """One horizontal accordion card per chart. KPIs stay in a separate card row."""
    if not chart_meta:
        return None
    sections: list[dict[str, Any]] = []
    for index, chart in enumerate(chart_meta):
        charts = [chart]
        sections.append(
            {
                "title": _section_title(charts, index),
                "subtitle": _section_subtitle(charts),
                "chip": _chart_type_label(charts),
                "charts": charts,
            }
        )
    return sections


_ACC_TOGGLE = (
    "var p=this.closest('.ez-acc');"
    "if(!p)return;"
    "var o=p.classList.toggle('is-open');"
    "this.setAttribute('aria-expanded',o?'true':'false')"
)


def _acc_chevron() -> str:
    return (
        '<div class="ez-acc-chevron-wrap">'
        '<svg class="ez-acc-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="m18 15-6-6-6 6"/>'
        '</svg>'
        '</div>'
    )


def _acc_card(
    *,
    title: str,
    subtitle: str,
    meta_lines: list[str],
    body_lines: list[str],
    open: bool = False,
) -> list[str]:
    """One horizontal accordion row. Overview starts open; charts stay closed."""
    open_cls = " is-open" if open else ""
    expanded = "true" if open else "false"
    lines = [
        f'  <div class="ez-acc{open_cls}">',
        f'    <div class="ez-acc-btn" role="button" tabindex="0" aria-expanded="{expanded}" '
        f'onclick="{_ACC_TOGGLE}">',
        '      <div class="ez-acc-copy">',
        f'        <h2 class="ez-acc-title">{_esc(title)}</h2>',
        f'        <div class="ez-acc-sub">{_esc(subtitle)}</div>',
        "      </div>",
        '      <div class="ez-acc-meta">',
    ]
    lines.extend(meta_lines)
    lines.append(f"        {_acc_chevron()}")
    lines.append("      </div>")
    lines.append("    </div>")
    lines.append('    <div class="ez-acc-panel">')
    if body_lines:
        lines.extend(_indent(3, body_lines))
    else:
        lines.append('      <div class="ez-empty">Nothing to show yet.</div>')
    lines.append("    </div>")
    lines.append("  </div>")
    return lines


def _meta_metrics(stats: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for stat in stats:
        tone = str(stat.get("tone") or "")
        cls = " is-cyan" if tone == "cyan" else " is-alert" if tone == "alert" else ""
        lines.append('        <div class="ez-acc-metric">')
        lines.append(f'          <div class="ez-acc-metric-label">{_esc(stat["label"])}</div>')
        lines.append(f'          <div class="ez-acc-metric-value{cls}">{_esc(stat["value"])}</div>')
        lines.append("        </div>")
    return lines


def _meta_chip(label: str) -> list[str]:
    return [f'        <span class="ez-acc-chip">{_esc(label)}</span>']


def _accordion_rows(
    sections: list[dict[str, Any]],
    chart_data: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for section in sections:
        chart_rows = section.get("charts") or []
        first = chart_rows[0] if chart_rows else {}
        chart_payload = chart_data.get(str(first.get("id"))) if isinstance(chart_data.get(str(first.get("id"))), dict) else {}
        stats = _chart_header_stats(first, chart_payload or {})
        chart_html = _chart_rows(chart_rows, chart_data, in_accordion=True)
        meta = _meta_metrics(stats) if stats else _meta_chip(str(section.get("chip") or "Chart"))
        lines.extend(
            _acc_card(
                title=str(section["title"]),
                subtitle=str(section["subtitle"]),
                meta_lines=meta,
                body_lines=chart_html or ['<div class="ez-empty">No chart data yet.</div>'],
            )
        )
    return lines


def _fmt_kpi_stat(item: dict[str, Any] | None, row: dict[str, Any] | None = None) -> str:
    if not item or item.get("value") is None:
        return "—"
    value = item.get("value")
    unit = str((item.get("unit") if item else None) or (row.get("unit") if row else "") or "").strip()
    kpi_id = str((row.get("id") if row else "") or "").lower()
    label = str((row.get("label") if row else "") or "").lower()

    is_currency = unit in {"$", "USD"} or any(w in kpi_id or w in label for w in ["ap", "amount", "payable", "overdue", "spend", "cost", "total_ap", "margin"])
    is_days = unit in {"d", "days"} or "dpo" in kpi_id or "dpo" in label or "days" in kpi_id

    try:
        num = float(value)
        abs_num = abs(num)
        if abs_num >= 1e6:
            val_str = f"{num / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
        elif abs_num >= 1e3:
            val_str = f"{num / 1e3:.1f}K"
        elif num.is_integer():
            val_str = str(int(num))
        else:
            val_str = f"{num:.1f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        val_str = str(value)

    if is_currency and not val_str.startswith("$"):
        return f"${val_str}"
    if is_days and not val_str.endswith("d") and not val_str.endswith("days"):
        return f"{val_str}d"
    if unit and unit not in {"$", "USD", "d", "days"} and not val_str.endswith(unit):
        return f"{val_str}{unit}" if unit == "%" else f"{val_str} {unit}"
    return val_str


def _kpi_header_stats(kpi_meta: list[dict[str, Any]], kpi_data: dict[str, Any]) -> list[dict[str, str]]:
    stats: list[dict[str, str]] = []
    for row in kpi_meta[:4]:
        kpi_id = str(row.get("id") or "")
        label = str(row.get("label") or kpi_id).upper()
        item = kpi_data.get(kpi_id) if isinstance(kpi_data.get(kpi_id), dict) else {}
        val = _fmt_kpi_stat(item, row)
        is_alert = bool(item.get("alert")) or "overdue" in kpi_id.lower() or "overdue" in label.lower()
        tone = "alert" if is_alert else ("cyan" if "match" in kpi_id.lower() else "")
        stats.append({"label": label, "value": val, "tone": tone})
    return stats


def _overview_title(dashboard: dict[str, Any]) -> str:
    repo = str(dashboard.get("repository_name") or dashboard.get("repo_name") or "").strip()
    if repo:
        if "command center" in repo.lower():
            return repo
        if any(w in repo.lower() for w in ["ap", "account", "payable", "purchase", "invoice"]):
            return "AP Command Center"
        short = repo if len(repo) <= 24 else repo[:21] + "…"
        return f"{short} Command Center" if "overview" not in short.lower() else short
    return "AP Command Center"


def _overview_card(
    *,
    title: str,
    message: str,
    kpi_meta: list[dict[str, Any]],
    kpi_data: dict[str, Any],
    insights: list[str],
    primary_chart_meta: dict[str, Any] | None = None,
    primary_chart_data: dict[str, Any] | None = None,
) -> list[str]:
    """Shared accordion: KPIs + insights + primary chart. Open by default; user can collapse."""
    kpi_html = _kpi_rows(kpi_meta, kpi_data)
    insight_lines = _normalize_insights(insights)
    if not kpi_html and not insight_lines and not primary_chart_meta:
        return []

    body: list[str] = []
    if kpi_html:
        body.extend(kpi_html)

    if insight_lines and primary_chart_meta:
        chart_payload = primary_chart_data or {}
        body.append('<div class="ez-charts">')
        body.append('  <div class="ez-chart pos-left">')
        body.extend(_indent(2, _insight_body(insight_lines, show_heading=True)))
        body.append('  </div>')
        body.extend(_indent(1, _chart_block(primary_chart_meta, chart_payload, pos="right", show_heading=True)))
        body.append('</div>')
    elif insight_lines:
        body.extend(_insight_body(insight_lines, show_heading=True))
    elif primary_chart_meta:
        body.append('<div class="ez-charts">')
        body.extend(_indent(1, _chart_block(primary_chart_meta, primary_chart_data or {}, pos="full", show_heading=True)))
        body.append('</div>')

    subtitle = _overview_sub(message)
    stats = _kpi_header_stats(kpi_meta, kpi_data) if kpi_meta else []
    meta = _meta_metrics(stats) if stats else (_meta_chip("LIVE") if insight_lines else _meta_chip(f"{len(kpi_meta)} KPIs"))
    return _acc_card(
        title=title,
        subtitle=subtitle,
        meta_lines=meta,
        body_lines=body,
        open=True,
    )


def _kpis_after_charts(kpis: list[dict[str, Any]], charts: list[dict[str, Any]]) -> bool:
    if any(str(row.get("position") or "").lower() == "bottom" for row in kpis):
        return True
    if charts and any(str(row.get("position") or "").lower() == "top" for row in charts):
        if not any(str(row.get("position") or "top").lower() == "top" for row in kpis):
            return True
    return False


def _render_kpi_sub(sub: str, item: dict[str, Any]) -> str:
    sub_str = str(sub).strip()
    if not sub_str:
        return ""
    parts = sub_str.split(" ", 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if re.match(r"^[+-]?\d+(?:\.\d+)?%$", first):
        is_alert = item.get("alert") is True
        if first.startswith("+") and (is_alert or item.get("trend_pct", 0) > 50):
            badge_html = f'<span class="ez-kpi-badge is-danger">{_esc(first)}</span>'
        elif first.startswith("+"):
            badge_html = f'<span class="ez-kpi-badge is-up">{_esc(first)}</span>'
        else:
            badge_html = f'<span class="ez-kpi-sub-val">{_esc(first)}</span>'
        rest_html = f' <span class="ez-kpi-sub-text">{_esc(rest)}</span>' if rest else ""
        return f'<div class="ez-kpi-sub">{badge_html}{rest_html}</div>'
    return f'<div class="ez-kpi-sub">{_esc(sub_str)}</div>'


def _kpi_rows(kpi_meta: list[dict[str, Any]], kpi_data: dict[str, Any]) -> list[str]:
    if not kpi_meta:
        return []
    count = len(kpi_meta)
    lines = [f'<div class="ez-kpis" style="--ez-kpi-count:{count}">']
    for row in kpi_meta:
        item = kpi_data.get(str(row.get("id"))) if isinstance(kpi_data.get(str(row.get("id"))), dict) else {}
        color = _theme_color(row.get("color") or (ERROR_MAIN if item.get("alert") else None))
        lines.append(f'  <div class="ez-kpi" style="border-top-color:{_esc(color)}">')
        lines.append(f'    <div class="ez-kpi-label">{_esc(row.get("label") or row.get("id"))}</div>')
        lines.append(f'    <div class="ez-kpi-value">{_esc(_fmt(item, row))}</div>')
        sub = item.get("subtext") or row.get("subtext")
        if not sub and "trend_pct" in item:
            pct = item.get("trend_pct")
            try:
                fpct = float(pct)
                sign = "+" if fpct > 0 else ""
                sub = f"{sign}{fpct:g}% vs last month"
            except (TypeError, ValueError):
                pass
        if sub:
            lines.append(f'    {_render_kpi_sub(sub, item)}')
        lines.append("  </div>")
    lines.append("</div>")
    return lines


def _chart_rows(
    chart_meta: list[dict[str, Any]],
    chart_data: dict[str, Any],
    *,
    in_accordion: bool = False,
) -> list[str]:
    if not chart_meta:
        return []
    lines = ['<div class="ez-charts">']
    count = len(chart_meta)
    for index, row in enumerate(chart_meta):
        chart = chart_data.get(str(row.get("id"))) if isinstance(chart_data.get(str(row.get("id"))), dict) else {}
        pos = None
        if in_accordion:
            if count == 1:
                pos = "full"
            else:
                pos = "left" if index % 2 == 0 else "right"
        lines.extend(_indent(1, _chart_block(row, chart or {}, pos=pos, show_heading=not in_accordion or count > 1)))
    lines.append("</div>")
    return lines


def _overview_sub(message: str) -> str:
    """Subtitle for the shared overview accordion (request context)."""
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text or text.lower() in {"apply", "i need a dashboard", "i need a dashboard.", "i need an ap dashboard", "i need an ap dashboard."}:
        return "Real-time · month · all suppliers"
    if "real-time" in text.lower() or "supplier" in text.lower():
        return text
    if len(text) > 90:
        text = text[:87] + "…"
    return f"Real-time · month · {text}"


def _insights_blurb() -> str:
    """Subtitle under AI-generated insights — not the same as the overview line."""
    return "Auto-updates with your filters — the ledger's margin notes"


def _normalize_insights(raw: Any) -> list[str]:
    """Turn insight payloads into separate bullet lines (split glued multi-line blobs)."""
    out: list[str] = []
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        chunks = re.split(r"[\n\r]+|(?:(?<=\S)\s*[•]\s+)|(?:(?:^|\n)\s*[-*]\s+)", text)
        for chunk in chunks:
            line = re.sub(r"\s+", " ", chunk).strip(" \t-•*")
            if line and line not in out:
                out.append(line)
            if len(out) >= 6:
                return out
    return out


def _insight_body(
    insights: list[str],
    *,
    show_heading: bool = False,
) -> list[str]:
    lines = ['<div class="ez-insights">']
    if show_heading:
        lines.extend(
            [
                '  <div class="ez-insights-top">',
                "    <div>",
                '      <h3 class="ez-insights-title">AI-generated insights</h3>',
                f'      <div class="ez-insights-sub">{_esc(_insights_blurb())}</div>',
                "    </div>",
                '    <span class="ez-live"><span class="ez-live-dot"></span>LIVE</span>',
                "  </div>",
            ]
        )
    lines.append("  <ul>")
    spark_svg = (
        '<svg class="ez-spark-svg" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="10"></circle>'
        '<circle cx="12" cy="12" fill="currentColor" r="3"></circle>'
        '</svg>'
    )
    for text in insights[:6]:
        lines.append(
            f'    <li>{spark_svg}<div><span>{_esc(text)}</span></div></li>'
        )
    lines.extend(["  </ul>", "</div>"])
    return lines


def render_dashboard_html(dashboard: dict[str, Any], *, message: str = "") -> str:
    data = dashboard.get("data") if isinstance(dashboard.get("data"), dict) else {}
    kpi_meta = [row for row in (dashboard.get("kpis") or []) if isinstance(row, dict) and row.get("enabled") is not False]
    chart_meta = [row for row in (dashboard.get("charts") or []) if isinstance(row, dict) and row.get("enabled") is not False]
    kpi_data = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
    chart_data = data.get("charts") if isinstance(data.get("charts"), dict) else {}
    insights = [str(item).strip() for item in (dashboard.get("insights") or []) if str(item).strip()]
    kpi_meta = sorted(kpi_meta, key=lambda row: int(row.get("order") or 99))
    chart_meta = sorted(chart_meta, key=lambda row: int(row.get("order") or 99))

    lines: list[str] = ["<style>"]
    lines.extend(_indent(1, [row.strip() for row in _CSS.strip().splitlines() if row.strip()]))
    lines.append("</style>")
    lines.append('<div class="ez-dash">')

    primary_chart = chart_meta[0] if chart_meta else None
    primary_chart_payload = chart_data.get(str(primary_chart.get("id"))) if primary_chart and isinstance(chart_data.get(str(primary_chart.get("id"))), dict) else {}

    overview = _overview_card(
        title=_overview_title(dashboard),
        message=message,
        kpi_meta=kpi_meta,
        kpi_data=kpi_data,
        insights=insights,
        primary_chart_meta=primary_chart,
        primary_chart_data=primary_chart_payload,
    )
    sections = _build_sections(chart_meta)
    chart_acc = _accordion_rows(sections, chart_data) if sections else []
    if not chart_acc and chart_meta:
        chart_acc = _acc_card(
            title=str(chart_meta[0].get("title") or chart_meta[0].get("label") or "Charts"),
            subtitle=str(chart_meta[0].get("description") or "Charts for this request."),
            meta_lines=_meta_chip("Chart"),
            body_lines=_chart_rows(chart_meta, chart_data, in_accordion=True),
        )

    body: list[str] = ['<div class="ez-acc-list">']
    if _kpis_after_charts(kpi_meta, chart_meta):
        body.extend(chart_acc)
        body.extend(overview)
    else:
        body.extend(overview)
        body.extend(chart_acc)
    body.append("</div>")
    lines.extend(_indent(1, body))
    lines.append("</div>")
    return "\n".join(lines) + "\n"


def _chart_body(row: dict[str, Any], chart: dict[str, Any]) -> str:
    chart_type = str(chart.get("type") or row.get("type") or "column").lower()
    colors = _colors(row, chart)
    series = [item for item in (chart.get("series") or []) if isinstance(item, dict)]
    categories = list(chart.get("categories") or [])
    values = list(chart.get("values") or chart.get("bars") or [])
    if chart_type == "radar" and series:
        return _radar(series, colors)
    if chart_type in {"donut", "pie"} and series:
        return _donut(series, colors)
    if chart_type == "gauge" and series:
        return _donut(series, colors)
    if chart_type in {"lollipop", "hbar"} and categories:
        return _lollipop(categories, values, colors[0])
    if categories and values:
        kind = "line" if chart_type == "line" else "area" if chart_type == "area" else "column"
        return _plot(categories, values, colors, kind)
    return '<p class="ez-empty">No chart values yet.</p>'


def _donut(series: list[dict[str, Any]], colors: list[str]) -> str:
    total = sum(float(item.get("value") or 0) for item in series) or 1.0
    cx, cy, radius, inner_r = 74, 74, 68, 40
    start = 0.0
    slices = []
    legend = []
    for i, item in enumerate(series):
        value = float(item.get("value") or 0)
        end = start + (value / total) * 360
        color = colors[i % len(colors)]
        name = item.get("name")
        slices.append(
            f'<path class="ez-hit" {_data_tip(name, value)} d="{_pie_path(cx, cy, radius, start, end)}" fill="{_esc(color)}">'
            f"<title>{_esc(_tip_label(name, value))}</title></path>"
        )
        legend.append(
            f'<li class="ez-hit" {_data_tip(name, value)}>'
            f'<span class="ez-swatch" style="background:{_esc(color)}"></span>'
            f"{_esc(name)} · {_esc(item.get('value'))}</li>"
        )
        start = end
    svg = "\n".join(
        [
            '<svg class="ez-donut" viewBox="0 0 148 148">',
            * [f"  {row}" for row in slices],
            f'  <circle class="ez-donut-hole" cx="{cx}" cy="{cy}" r="{inner_r}" fill="var(--ez-surface)"/>',
            "</svg>",
        ]
    )
    return _plot_wrap(svg, '<ul class="ez-legend">', *[f"  {item}" for item in legend], "</ul>")


def _plot(categories: list[Any], values: list[Any], colors: list[str], kind: str) -> str:
    nums = [float(v or 0) for v in values]
    w, h, pad_l, pad_r, pad_t, pad_b = 340, 168, 34, 10, 12, 28
    inner_w = w - pad_l - pad_r
    inner_h = h - pad_t - pad_b
    max_v = _nice_max(max(nums) if nums else 1)
    ticks = []
    for p in (0, 0.25, 0.5, 0.75, 1):
        y = pad_t + inner_h * (1 - p)
        ticks.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" stroke="{GRID_LIGHT}"/>'
            f'<text x="{pad_l - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9" fill="{AXIS_MUTED}">{_esc(_compact(max_v * p))}</text>'
        )
    n = max(len(nums), 1)
    gap = inner_w / n
    labels = []
    body = []
    if kind == "column":
        bar_w = max(10, min(28, gap * 0.46))
        for i, value in enumerate(nums):
            bh = max(2, (value / max_v) * inner_h)
            x = pad_l + gap * i + (gap - bar_w) / 2
            y = pad_t + inner_h - bh
            name = categories[i] if i < len(categories) else ""
            body.append(
                f'<rect class="ez-hit" {_data_tip(name, value)} x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="5" fill="{colors[i % len(colors)]}">'
                f"<title>{_esc(_tip_label(name, value))}</title></rect>"
            )
            labels.append(
                f'<text x="{pad_l + gap * i + gap * 0.5:.1f}" y="{h - 8}" text-anchor="middle" font-size="9" fill="{AXIS_TEXT_LIGHT}">{_esc(name)}</text>'
            )
    else:
        pts = []
        for i, value in enumerate(nums):
            x = pad_l + (inner_w / 2 if n == 1 else (i / (n - 1)) * inner_w)
            y = pad_t + inner_h - (value / max_v) * inner_h
            pts.append((x, y, value, categories[i] if i < len(categories) else ""))
            labels.append(
                f'<text x="{x:.1f}" y="{h - 8}" text-anchor="middle" font-size="9" fill="{AXIS_TEXT_LIGHT}">{_esc(categories[i] if i < len(categories) else "")}</text>'
            )
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts)
        if kind == "area" and pts:
            area = f"{pad_l},{pad_t + inner_h} {line} {pad_l + inner_w},{pad_t + inner_h}"
            body.append(f'<polygon points="{area}" fill="{colors[0]}" fill-opacity="0.16"/>')
        body.append(
            f'<polyline points="{line}" fill="none" stroke="{colors[0]}" stroke-width="2.4" stroke-linejoin="round"/>'
        )
        for x, y, value, name in pts:
            body.append(
                f'<circle class="ez-hit" {_data_tip(name, value)} cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colors[0]}" fill-opacity="0.01" stroke="none"/>'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#fff" stroke="{colors[0]}" stroke-width="2"/>'
            )
    inner = "\n".join(_indent(1, _lines(*ticks, *body, *labels)))
    return _plot_wrap(f'<svg class="ez-svg" viewBox="0 0 {w} {h}">\n{inner}\n</svg>')


def _lollipop(categories: list[Any], values: list[Any], color: str) -> str:
    nums = [float(v or 0) for v in values]
    n = max(len(categories), 1)
    row_h, pad_l, pad_r, pad_t, pad_b, w = 30, 112, 40, 6, 24, 360
    plot_w = w - pad_l - pad_r
    h = pad_t + n * row_h + pad_b
    max_v = _nice_max(max(nums) if nums else 1)
    rows = []
    for i, label in enumerate(categories):
        value = nums[i] if i < len(nums) else 0
        y = pad_t + row_h * i + row_h / 2
        x = pad_l + (value / max_v) * plot_w
        name = str(label or "")
        shown = name if len(name) <= 16 else name[:15] + "…"
        rows.append(
            f'<text x="{pad_l - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-size="10" fill="{AXIS_TEXT_LIGHT}">{_esc(shown)}</text>'
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{_esc(color)}" stroke-width="2.4"/>'
            f'<circle class="ez-hit" {_data_tip(name, value)} cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{_esc(color)}" fill-opacity="0.01" stroke="none"/>'
            f'<circle class="ez-hit" {_data_tip(name, value)} cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{_esc(color)}" stroke="#fff" stroke-width="2">'
            f"<title>{_esc(_tip_label(name, value))}</title></circle>"
        )
    inner = "\n".join(_indent(1, _lines(*rows)))
    return _plot_wrap(f'<svg class="ez-lollipop" viewBox="0 0 {w} {h}">\n{inner}\n</svg>')


def _radar(series: list[dict[str, Any]], colors: list[str]) -> str:
    items = [item for item in series if float(item.get("value") or 0) > 0][:6]
    if not items:
        return '<p class="ez-empty">No chart values yet.</p>'
    max_v = max(float(item.get("value") or 0) for item in items) or 1
    cx, cy, r = 75, 75, 56
    pts = []
    axes = []
    hits = []
    legend = []
    for i, item in enumerate(items):
        angle = (math.pi * 2 * i) / len(items) - math.pi / 2
        value = float(item.get("value") or 0)
        name = item.get("name")
        rr = r * (value / max_v)
        px = cx + math.cos(angle) * rr
        py = cy + math.sin(angle) * rr
        pts.append(f"{px:.1f},{py:.1f}")
        axes.append(
            f'<line x1="{cx}" y1="{cy}" x2="{cx + math.cos(angle) * r:.1f}" y2="{cy + math.sin(angle) * r:.1f}" stroke="{GRID_LIGHT}"/>'
        )
        color = colors[i % len(colors)]
        hits.append(
            f'<circle class="ez-hit" {_data_tip(name, value)} cx="{px:.1f}" cy="{py:.1f}" r="8" fill="{_esc(color)}">'
            f"<title>{_esc(_tip_label(name, value))}</title></circle>"
        )
        legend.append(
            f'<li class="ez-hit" {_data_tip(name, value)}>'
            f'<span class="ez-swatch" style="background:{_esc(color)}"></span>{_esc(name)}</li>'
        )
    rings = "".join(
        f'<circle cx="{cx}" cy="{cy}" r="{r * s:.1f}" fill="none" stroke="{GRID_LIGHT}"/>' for s in (0.33, 0.66, 1)
    )
    svg_inner = "\n".join(
        _indent(
            1,
            _lines(
                rings,
                *axes,
                f'<polygon points="{" ".join(pts)}" fill="{colors[0]}" fill-opacity="0.28" stroke="{colors[0]}" stroke-width="2"/>',
                *hits,
            ),
        )
    )
    svg = "\n".join(
        [
            '<svg class="ez-radar-svg" viewBox="0 0 150 150" width="148" height="148">',
            *[f"  {line}" if line else "" for line in svg_inner.splitlines()],
            "</svg>",
        ]
    )
    return _plot_wrap(
        '<div class="ez-radar">',
        svg,
        '<ul class="ez-legend">',
        *[f"  {item}" for item in legend],
        "</ul>",
        "</div>",
    )
