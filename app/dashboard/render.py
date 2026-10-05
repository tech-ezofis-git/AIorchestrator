"""Build a modern, fully clickable and functional enterprise dashboard HTML from a hydrated dashboard.
Adopts the EZOFIS V6 Design System tokens, interactive command center, KPI grid,
workflow pipeline, status charts, AI insights, and searchable/sortable register table
with slide-out inspection drawer from ezofis-document-intelligence-dashboard_22SEP 1.html.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_json(data: Any) -> str:
    serialized = json.dumps(data, default=str)
    return serialized.replace("<", "\\u003c").replace(">", "\\u003e")


_CSS_V6 = """
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Poppins:wght@500;600;700&display=swap");

:root{
  --primary:#9333ea; --secondary:#00bcd4;
  --surface:#ffffff; --gray-1:#fdfcfd; --gray-2:#faf9fb;
  --gray-3:#f2eff3; --gray-8:#bcbac7; --gray-10:#84828e; --gray-11:#65636d; --gray-13:#211f26;
  --green-9:#30a46c; --green-3:#e6f6eb;
  --red-9:#e5484d;   --red-3:#feebec;
  --orange-9:#f76b15; --orange-3:#ffefd6;

  --font-main:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --font-head:'Poppins','Inter',system-ui,sans-serif;

  --radius-sm:5px; --radius-xl:12px; --radius-pill:9999px;
  --border:1px solid var(--gray-3);
  --shadow-sm:0 1px 2px 0 rgb(0 0 0 / .05);
  --shadow-card:0 1px 3px rgba(0,0,0,.05);
  --shadow-card-hover:0 4px 6px rgba(0,0,0,.07);
  --shadow-pill:0 10px 25px -5px rgba(124,58,237,.15);
  --focus-ring:0 0 0 2px rgba(147,51,234,.1);

  --line-strong:color-mix(in srgb,var(--gray-8) 45%,#fff);
  --primary-tint:color-mix(in srgb,var(--primary) 8%,#fff);
  --primary-line:color-mix(in srgb,var(--primary) 40%,#fff);
  --cyan-tint:color-mix(in srgb,var(--secondary) 12%,#fff);
  --green-ink:color-mix(in srgb,var(--green-9) 62%,#000);
  --red-ink:color-mix(in srgb,var(--red-9) 78%,#000);
  --orange-ink:color-mix(in srgb,var(--orange-9) 62%,#000);
  --cyan-ink:color-mix(in srgb,var(--secondary) 55%,#000);
}

*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;font-family:var(--font-main);font-size:14px;font-weight:450;color:var(--gray-13);background:var(--surface);-webkit-font-smoothing:antialiased;line-height:1.45}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:inherit}
input,select{font:inherit;color:inherit}
h1,h2,h3,h4,p{margin:0}
[hidden]{display:none!important}
.ic{flex:none;display:inline-block;vertical-align:middle}
:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

/* ---------- App Shell & Root Wrapper ---------- */
.ez-dash{font-family:var(--font-main);color:var(--gray-13);background:var(--surface);min-height:100vh}
.app{display:grid;grid-template-columns:64px minmax(0,1fr);grid-template-rows:64px minmax(0,1fr);min-height:100vh}
.logo-cell{display:flex;align-items:center;justify-content:center;border-right:var(--border);border-bottom:var(--border);background:var(--gray-1)}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:0 24px 0 16px;border-bottom:var(--border);background:var(--gray-1)}
.top-title{font-family:var(--font-head);font-size:16px;font-weight:600;letter-spacing:-.01em}
.top-icons{display:flex;align-items:center;gap:6px}
.icon-btn{position:relative;width:36px;height:36px;border-radius:var(--radius-sm);display:grid;place-items:center;color:var(--gray-11);cursor:pointer;border:0;background:none}
.icon-btn:hover{background:var(--gray-3);color:var(--gray-13)}
.icon-btn .dot{position:absolute;top:5px;right:6px;min-width:8px;height:8px;border-radius:9999px;background:var(--primary);border:2px solid var(--gray-1);box-sizing:content-box}
.avatar{width:36px;height:36px;border-radius:9999px;background:var(--gray-3);display:grid;place-items:center;font-weight:600;font-size:13px;color:var(--gray-11);margin-left:6px}
.nav{display:flex;flex-direction:column;align-items:center;gap:10px;padding:16px 0 14px;border-right:var(--border);background:var(--surface)}
.nav .sp{flex:1}
.nav-btn{width:36px;height:36px;border-radius:var(--radius-sm);display:grid;place-items:center;color:var(--gray-11);cursor:pointer;border:0;background:none}
.nav-btn:hover{background:var(--gray-3)}
.nav-btn.active{background:var(--gray-3);color:var(--primary)}
.nav-btn.ai{color:var(--primary)}
.main{display:flex;flex-direction:column;min-width:0;min-height:0}
.subhead{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 26px;border-bottom:var(--border);background:var(--surface);flex:none}
.subhead .t{font-family:var(--font-head);font-size:16px;font-weight:600;letter-spacing:-.01em}
.subhead .s{font-size:13px;color:var(--gray-11);margin-top:1px}
.subhead .r{display:flex;align-items:center;gap:12px}
.workspace{flex:1;display:flex;min-height:0;position:relative}
.scroll{flex:1;min-width:0;overflow:auto;padding:24px 26px 40px;background:var(--surface)}
.side{width:410px;flex:none;border-left:var(--border);background:var(--gray-2);overflow:auto}
.stack{display:flex;flex-direction:column;gap:16px;max-width:1900px;margin:0 auto}

/* ---------- Controls ---------- */
.btn-primary{display:inline-flex;align-items:center;gap:8px;background:var(--primary);color:#fff;border-radius:var(--radius-sm);padding:9px 16px;font-weight:500;transition:all .2s;height:40px;border:0;cursor:pointer}
.btn-primary:hover{background:color-mix(in srgb,var(--primary) 90%,#000)}
.btn-primary:active{transform:scale(.98)}
.btn-secondary{display:inline-flex;align-items:center;gap:8px;color:var(--primary);border:1px solid var(--primary);border-radius:var(--radius-sm);padding:7px 14px;font-weight:500;background:transparent;cursor:pointer}
.btn-secondary:hover{background:var(--primary-tint)}
.btn-ghost{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);padding:7px 12px;background:#fff;font-weight:500;font-size:13px;cursor:pointer}
.btn-ghost:hover{background:var(--gray-2)}
.link{color:var(--primary);font-weight:500;font-size:13px;display:inline-flex;align-items:center;gap:4px;cursor:pointer}
.link:hover{text-decoration:underline}
.sel-repo{display:flex;align-items:center;gap:10px;width:300px;height:40px;padding:0 12px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:#fff;position:relative;cursor:pointer}
.sel-repo span.v{flex:1;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.sel-repo:hover{background:var(--gray-2)}

.menu{position:absolute;z-index:40;top:calc(100% + 6px);left:0;min-width:220px;max-height:320px;overflow:auto;background:#fff;border:var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-card-hover),0 8px 24px rgba(0,0,0,.06);padding:6px}
.menu.right{left:auto;right:0}
.menu .opt{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:8px 10px;border-radius:var(--radius-sm);font-size:13.5px;cursor:pointer;border:0;background:none}
.menu .opt:hover{background:var(--gray-2)}
.menu .opt.sel{background:var(--primary-tint);color:var(--primary);font-weight:600}
.menu .hd{padding:6px 10px 4px;font-size:11px;color:var(--gray-10);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.menu .field{padding:6px 10px}
.menu label.lb{display:block;font-size:11.5px;color:var(--gray-11);margin-bottom:4px;font-weight:500}
.menu input[type=text],.menu select{width:100%;height:34px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);padding:0 10px;background:#fff}
.menu input:focus,.menu select:focus{outline:none;border-color:var(--primary);box-shadow:var(--focus-ring)}
.menu-wrap{position:relative;display:inline-block}

/* ---------- Filters Bar ---------- */
.card{background:var(--surface);border:var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-card);transition:box-shadow .2s ease}
.card:hover{box-shadow:var(--shadow-card-hover)}
.filters{display:flex;align-items:center;gap:10px;padding:14px 18px;flex-wrap:wrap}
.filters .grow{flex:1;min-width:20px}
.chip{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;border:1px solid var(--line-strong);border-radius:var(--radius-pill);background:#fff;font-size:13.5px;white-space:nowrap;cursor:pointer}
.chip:hover{background:var(--gray-2)}
.chip.on{background:var(--primary-tint);border-color:var(--primary-line);color:var(--primary);font-weight:600}
.chip .x{display:grid;place-items:center;width:18px;height:18px;border-radius:9999px;margin-left:2px}
.chip .x:hover{background:rgba(147,51,234,.14)}
.search{display:flex;align-items:center;gap:8px;height:36px;width:min(360px,100%);padding:0 12px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:#fff;color:var(--gray-10)}
.search:focus-within{border-color:var(--primary);box-shadow:var(--focus-ring)}
.search input{border:0;outline:0;flex:1;min-width:0;background:transparent;color:var(--gray-13);font:inherit}
.search input::placeholder{color:var(--gray-10)}

/* ---------- Command Center ---------- */
.cc{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 24px;flex-wrap:wrap}
.cc h2{font-family:var(--font-head);font-size:16px;font-weight:600;letter-spacing:-.01em}
.cc .sub{font-size:12.5px;color:var(--gray-11);margin-top:2px}
.cc-stats{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.cc-stat{text-align:left;border-radius:var(--radius-sm);padding:8px 14px;background:var(--gray-2);border:1px solid var(--gray-3);transition:background .12s,border-color .12s;cursor:pointer}
.cc-stat:hover{background:var(--gray-3);border-color:var(--line-strong)}
.cc-stat:active{background:var(--primary-tint);border-color:var(--primary-line)}
.cc-stat.on{background:var(--primary-tint);border-color:var(--primary-line)}
.cc-stat.on .l{color:var(--primary)}
.cc-stat .l{font-size:10.5px;letter-spacing:.06em;color:var(--gray-11);font-weight:500;text-transform:uppercase}
.cc-stat .v{font-size:19px;font-weight:700;color:var(--primary);line-height:1.2;margin-top:2px}
.cc-stat .v.red{color:var(--red-9)}
.cc-stat .v.green{color:var(--green-9)}
.cc-toggle{width:32px;height:32px;border-radius:var(--radius-sm);display:grid;place-items:center;color:var(--primary);cursor:pointer;border:0;background:none}
.cc-toggle:hover{background:var(--primary-tint)}
.cc-toggle .ic{transition:transform .2s}
.cc-toggle[aria-expanded=false] .ic{transform:rotate(180deg)}

/* ---------- KPI Grid ---------- */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}
.kpi{display:block;width:100%;background:var(--surface);border:var(--border);border-top:3px solid var(--c,var(--primary));border-radius:var(--radius-xl);padding:16px 20px;box-shadow:var(--shadow-card);transition:box-shadow .2s,border-color .2s;position:relative;cursor:pointer;text-align:left}
.kpi:hover{box-shadow:var(--shadow-card-hover);transform:translateY(-2px)}
.kpi.active{box-shadow:0 0 0 2px var(--primary-line);border-color:var(--primary-line);border-top-color:var(--c,var(--primary))}
.kpi .lbl{font-size:12px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:var(--gray-11);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .val{font-size:24px;font-weight:700;margin:8px 0 10px;line-height:1.15;letter-spacing:-.01em;color:var(--gray-13)}
.kpi .foot{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--gray-11);min-height:22px}
.delta{display:inline-flex;align-items:center;padding:1px 8px;border-radius:var(--radius-sm);font-size:12px;font-weight:600;background:var(--gray-3);color:var(--gray-11)}
.delta.bad{background:var(--red-3);color:var(--red-ink)}
.delta.good{background:var(--green-3);color:var(--green-ink)}
.kpi-wrap{display:flex;flex-direction:column;gap:16px}

/* ---------- Card & Chart Grids ---------- */
.grid{display:grid;gap:16px}
.g-2-1{grid-template-columns:minmax(0,1fr) minmax(0,2fr)}
.g-3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g-2{grid-template-columns:repeat(2,minmax(0,1fr))}
.g-21{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}
.card-h{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:18px 22px 0}
.card-h h3{font-size:15px;font-weight:700;letter-spacing:-.005em}
.card-h .sub{font-size:12px;color:var(--gray-10);margin-top:2px}
.card-b{padding:14px 22px 20px}
.tabs{display:inline-flex;background:var(--gray-3);border-radius:var(--radius-sm);padding:2px;gap:2px}
.tabs button{padding:4px 12px;border-radius:4px;font-size:12.5px;font-weight:500;color:var(--gray-11);cursor:pointer;border:0;background:none}
.tabs button.on{background:#fff;color:var(--primary);box-shadow:var(--shadow-sm);font-weight:600}
.note{font-size:12px;color:var(--gray-11);line-height:1.5}
.empty{padding:26px 10px;text-align:center;color:var(--gray-10);font-size:13px}

/* Bars & Pipeline */
.hbar{display:grid;grid-template-columns:130px minmax(0,1fr) 54px;align-items:center;gap:12px;height:38px;width:100%;border-radius:var(--radius-sm);padding:0 6px;margin:0 -6px;cursor:pointer;border:0;background:none}
.hbar:hover{background:var(--gray-2)}
.hbar.on{background:var(--primary-tint)}
.hbar .lb{font-size:13px;color:var(--gray-11);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left}
.hbar .tr{height:14px;background:var(--gray-3);border-radius:var(--radius-sm);overflow:hidden}
.hbar .fl{display:block;height:100%;border-radius:var(--radius-sm);min-width:2px;transition:width .35s ease}
.hbar .nv{text-align:right;font-weight:700;font-size:14px}
.flow{display:flex;align-items:stretch;gap:0;overflow:auto;padding:4px 0}
.stage{flex:1;min-width:130px;text-align:left;border:1px solid var(--gray-3);border-radius:var(--radius-xl);padding:12px 16px;background:#fff;position:relative;transition:box-shadow .2s;cursor:pointer}
.stage:hover{box-shadow:var(--shadow-card-hover)}
.stage.on{border-color:var(--primary-line);box-shadow:0 0 0 2px var(--primary-tint)}
.stage .l{font-size:12px;color:var(--gray-11);font-weight:600;text-transform:uppercase}
.stage .v{font-size:24px;font-weight:700;margin-top:2px;letter-spacing:-.01em;color:var(--gray-13)}
.stage .m{font-size:11.5px;color:var(--gray-10);margin-top:2px}
.stage .bar{height:4px;border-radius:9999px;background:var(--gray-3);margin-top:8px;overflow:hidden}
.stage .bar i{display:block;height:100%;background:var(--primary);border-radius:9999px}
.arrow{display:grid;place-items:center;width:34px;flex:none;color:var(--gray-8)}
.stage.od{border-color:color-mix(in srgb,var(--red-9) 35%,#fff);background:color-mix(in srgb,var(--red-9) 4%,#fff);margin-left:10px;max-width:190px}
.stage.od .v{color:var(--red-9)}
.wf-types{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.wf-types button{padding:3px 10px;border-radius:9999px;border:1px solid var(--line-strong);font-size:12px;color:var(--gray-11);background:#fff;cursor:pointer}
.wf-types button:hover{border-color:var(--primary-line);color:var(--primary)}
.wf-types button.on{background:var(--primary-tint);border-color:var(--primary-line);color:var(--primary);font-weight:600}

/* Donut */
.donut-wrap{display:flex;align-items:center;gap:22px}
.donut{flex:none;position:relative;width:150px;height:150px}
.donut svg{transform:rotate(-90deg)}
.donut .ctr{position:absolute;inset:0;display:grid;place-items:center;text-align:center}
.donut .ctr b{display:block;font-size:24px;letter-spacing:-.01em;line-height:1.1}
.donut .ctr span{font-size:11px;color:var(--gray-10)}
.dl{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.dl button{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:var(--radius-sm);font-size:13px;width:100%;cursor:pointer;border:0;background:none}
.dl button:hover{background:var(--gray-2)}
.dl button.on{background:var(--primary-tint)}
.dl i{width:9px;height:9px;border-radius:3px;flex:none}
.dl .n{flex:1;color:var(--gray-11);font-weight:500;text-align:left}
.dl .p{font-weight:700}
.dl .c{font-size:11.5px;color:var(--gray-10);width:48px;text-align:right}

/* Health & Watch */
.health-score{display:flex;align-items:baseline;gap:8px;margin:2px 0 6px}
.health-score .n{font-family:var(--font-head);font-size:44px;font-weight:700;letter-spacing:-.02em;line-height:1}
.health-score .d{font-size:16px;color:var(--gray-10);font-weight:500}
.status-badge{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:var(--radius-pill);font-size:12px;font-weight:600}
.status-badge.green{background:var(--green-3);color:var(--green-ink)}
.status-badge.orange{background:var(--orange-3);color:var(--orange-ink)}
.status-badge.red{background:var(--red-3);color:var(--red-ink)}
.segbar{display:flex;height:10px;border-radius:9999px;overflow:hidden;background:var(--gray-3);gap:2px;margin:16px 0 14px}
.segbar i{display:block;height:100%;min-width:3px}
.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.trio button{padding:6px 8px;border-radius:var(--radius-sm);text-align:left;cursor:pointer;border:0;background:none}
.trio button:hover{background:var(--gray-2)}
.trio .l{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--gray-11);font-weight:500}
.trio .l i{width:8px;height:8px;border-radius:9999px;display:inline-block}
.trio .v{font-size:18px;font-weight:700;margin-top:2px}

/* Workload & SVG Charts */
svg.chart{width:100%;display:block}
svg.chart text{font-family:var(--font-main);font-size:11px;fill:var(--gray-10)}
.chart-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--gray-11);margin-top:6px}
.chart-legend i{display:inline-block;width:18px;height:0;border-top:3px solid var(--c);margin-right:6px;vertical-align:3px}
.chart-legend i.dash{border-top-style:dashed}
.wl-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:12px}
.wl-tile{border:1px solid var(--gray-3);border-radius:var(--radius-sm);padding:10px 12px;background:#fff}
.wl-tile h4{margin:0 0 6px;font-size:12px;color:var(--gray-11);font-weight:600}
.wl-tile div{display:flex;justify-content:space-between;font-size:12.5px;padding:1px 0}
.wl-tile b{font-weight:700}

/* AI Insights */
.live{display:inline-flex;align-items:center;gap:6px;border:1px solid color-mix(in srgb,var(--orange-9) 35%,#fff);background:color-mix(in srgb,var(--orange-9) 6%,#fff);color:var(--orange-ink);border-radius:var(--radius-pill);padding:2px 12px;font-size:12px;font-weight:700;letter-spacing:.02em}
.live i{width:6px;height:6px;border-radius:9999px;background:var(--orange-9);animation:pulse 1.8s ease-in-out infinite}
@keyframes pulse{50%{opacity:.35}}
.src-pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:var(--radius-pill);background:var(--primary-tint);color:var(--primary);font-size:11.5px;font-weight:600}
.insights{list-style:none;margin:0;padding:0}
.insights li{border-bottom:1px dashed var(--gray-3)}
.insights li:last-child{border-bottom:0}
.insights button{display:flex;gap:12px;align-items:flex-start;width:100%;padding:11px 6px;border-radius:var(--radius-sm);font-size:13.5px;line-height:1.5;cursor:pointer;border:0;background:none;text-align:left}
.insights button:hover{background:var(--gray-2)}
.insights .bul{flex:none;width:18px;height:18px;margin-top:2px;border-radius:9999px;border:2px solid var(--c);display:grid;place-items:center}
.insights .bul i{width:6px;height:6px;border-radius:9999px;background:var(--c)}
.insights .k{display:block;font-size:11.5px;font-weight:700;color:var(--gray-11);text-transform:uppercase;letter-spacing:.04em;margin-bottom:1px}

/* Recommended Actions */
.rows{display:flex;flex-direction:column}
.arow{display:flex;align-items:center;gap:12px;padding:12px 6px;border-bottom:var(--border);width:100%;cursor:pointer;border:0;background:none;text-align:left}
.arow:last-child{border-bottom:0}
.arow:hover{background:var(--gray-2)}
.arow .ico{flex:none;width:34px;height:34px;border-radius:var(--radius-sm);display:grid;place-items:center;background:var(--c-bg,var(--primary-tint));color:var(--c-fg,var(--primary))}
.arow .tx{flex:1;min-width:0}
.arow .tt{font-weight:600;font-size:13.5px}
.arow .ss{font-size:12px;color:var(--gray-10);margin-top:1px}
.arow .go{color:var(--gray-8)}

/* ---------- Interactive Tables ---------- */
.tbl-wrap{overflow:auto;border:var(--border);border-radius:var(--radius-xl);background:#fff}
table.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:13.5px}
.tbl thead th{position:sticky;top:0;background:var(--gray-2);text-align:left;font-weight:500;color:var(--gray-11);padding:11px 14px;border-bottom:1px solid var(--gray-3);white-space:nowrap;font-size:13px}
.tbl thead th button{display:inline-flex;align-items:center;gap:8px;justify-content:space-between;width:100%;font-weight:500;cursor:pointer;border:0;background:none}
.tbl thead th button .ic{color:var(--gray-8)}
.tbl thead th.sorted button .ic{color:var(--primary)}
.tbl tbody td{padding:10px 14px;border-bottom:1px solid var(--gray-3);white-space:nowrap;vertical-align:middle}
.tbl tbody tr:last-child td{border-bottom:0}
.tbl tbody tr.row:hover td{background:var(--gray-2);cursor:pointer}
.tbl tbody tr.sel td{background:var(--primary-tint)}
.tbl td.num,.tbl th.num{text-align:right}
.tbl .nm{display:flex;align-items:center;gap:8px;font-weight:500;max-width:260px}
.tbl .nm span{overflow:hidden;text-overflow:ellipsis}
.tbl .sub2{display:block;font-size:11.5px;color:var(--gray-10);font-weight:450}
.link-cell{color:var(--gray-13);font-weight:500;text-align:left}
.link-cell:hover{color:var(--primary);text-decoration:underline}

.badge{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:var(--radius-sm);font-size:10.5px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.badge.green{background:var(--green-3);color:var(--green-ink)}
.badge.red{background:var(--red-3);color:var(--red-ink)}
.badge.orange{background:var(--orange-3);color:var(--orange-ink)}
.badge.purple{background:var(--primary-tint);color:var(--primary)}
.badge.cyan{background:var(--cyan-tint);color:var(--cyan-ink)}
.badge.gray{background:var(--gray-3);color:var(--gray-11)}

.bd{display:inline-flex;min-width:44px;justify-content:center;padding:2px 9px;border-radius:9999px;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.bd.green{background:var(--green-3);color:var(--green-ink)}
.bd.amber{background:var(--orange-3);color:var(--orange-ink)}
.bd.orange{background:var(--orange-9);color:#fff}
.bd.red{background:var(--red-9);color:#fff}
.bd.none{background:transparent;color:var(--gray-8)}

.tbl-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:0 0 12px}
.tbl-tools .grow{flex:1}
.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 4px 0;font-size:13px;color:var(--gray-11);flex-wrap:wrap}
.pager .pg{display:flex;align-items:center;gap:6px}
.pager button.pb{min-width:32px;height:32px;padding:0 8px;border-radius:var(--radius-sm);border:1px solid var(--line-strong);display:inline-grid;place-items:center;background:#fff;font-size:13px;cursor:pointer}
.pager button.pb:hover:not(:disabled){background:var(--gray-2)}
.pager button.pb:disabled{opacity:.4;cursor:default}
.pager button.pb.on{background:var(--primary);color:#fff;border-color:var(--primary)}
.pager select{height:32px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);padding:0 6px;background:#fff}
.drill-chip{display:inline-flex;align-items:center;gap:6px;background:var(--primary-tint);border:1px solid var(--primary-line);color:var(--primary);border-radius:var(--radius-pill);padding:2px 4px 2px 12px;font-weight:600;font-size:12.5px}
.drill-chip .x{display:grid;place-items:center;width:18px;height:18px;border-radius:9999px;cursor:pointer;border:0;background:none}
.drill-chip .x:hover{background:rgba(147,51,234,.14)}

/* ---------- Side Drawer ---------- */
.side-h{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:16px 18px 10px;position:sticky;top:0;background:var(--gray-2);z-index:2}
.side-h h3{font-size:15px;font-weight:700}
.side-h .sub{font-size:12px;color:var(--gray-10);margin-top:1px;word-break:break-all}
.side-b{padding:4px 16px 24px;display:flex;flex-direction:column;gap:14px}
.pcard{background:#fff;border:var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-card);overflow:hidden}
.pcard>h4{display:flex;align-items:center;gap:10px;margin:0;padding:12px 16px;font-size:14px;font-weight:700;border-bottom:var(--border);color:var(--gray-13)}
.pcard>h4 .ic{color:#2563eb}
.kv{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:11px 16px;border-bottom:var(--border);font-size:13.5px}
.kv:last-child{border-bottom:0}
.kv .k{display:flex;align-items:center;gap:10px;color:var(--gray-11)}
.kv .k .x{width:22px;height:22px;border-radius:6px;background:var(--green-3);color:var(--green-9);display:grid;place-items:center;flex:none}
.kv .k .x.none{background:transparent}
.kv .v{font-weight:700;text-align:right;max-width:58%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ai-analysis{padding:14px 16px}
.risk-pill{display:inline-flex;padding:2px 10px;border-radius:9999px;font-size:12px;font-weight:700}
.risk-pill.Low{background:var(--green-3);color:var(--green-ink)}
.risk-pill.Medium{background:var(--orange-3);color:var(--orange-ink)}
.risk-pill.High{background:var(--red-3);color:var(--red-ink)}
.ai-analysis .lab{font-size:11.5px;color:var(--gray-10);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin:12px 0 3px}
.ai-analysis p{font-size:13.5px;line-height:1.5}
.ptabs{display:flex;gap:6px;padding:0}
.ptabs button{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:var(--radius-xl);color:var(--gray-11);font-weight:500;font-size:13.5px;border:2px solid transparent;cursor:pointer;background:none}
.ptabs button.on{border-color:var(--gray-13);color:var(--gray-13);background:#fff}
.tl{padding:16px 16px 6px}
.tl-i{display:flex;gap:14px;position:relative;padding-bottom:18px}
.tl-i:not(:last-child)::before{content:"";position:absolute;left:15px;top:32px;bottom:0;width:2px;background:var(--gray-3)}
.tl-i .dot{flex:none;width:32px;height:32px;border-radius:9999px;background:var(--cyan-tint);color:#2563eb;display:grid;place-items:center}
.tl-i .tt{font-weight:600;font-size:13.5px;line-height:1.35}
.tl-i .tt span{font-weight:450;color:var(--gray-11)}
.tl-i .ss{font-size:12px;color:var(--gray-10);margin-top:2px}

/* Notifications & Tooltips */
.tip{position:fixed;z-index:100;pointer-events:none;background:var(--gray-13);color:#fff;font-size:12px;padding:5px 9px;border-radius:var(--radius-sm);max-width:280px;opacity:0;transition:opacity .12s;line-height:1.4}
.tip.on{opacity:1}
.toast{position:fixed;z-index:110;right:22px;bottom:22px;display:flex;align-items:center;gap:10px;background:var(--gray-13);color:#fff;padding:10px 16px;border-radius:var(--radius-sm);font-size:13.5px;box-shadow:var(--shadow-card-hover);opacity:0;transform:translateY(8px);transition:all .2s;pointer-events:none}
.toast.on{opacity:1;transform:none}

/* ---------- Responsive ---------- */
@media (max-width:1400px){.g-3{grid-template-columns:repeat(2,minmax(0,1fr))}.g-2-1,.g-21{grid-template-columns:1fr}}
@media (max-width:1180px){
  .side{position:absolute;right:0;top:0;bottom:0;width:min(92vw,410px);z-index:30;box-shadow:-10px 0 26px rgba(0,0,0,.09)}
  .workspace{position:relative}
}
@media (max-width:900px){
  .subhead{flex-direction:column;align-items:stretch;gap:12px}
  .sel-repo{width:100%}
  .donut-wrap{flex-direction:column;align-items:flex-start}
}
@media (max-width:640px){
  .scroll{padding:16px 14px 30px}
  .app{grid-template-columns:52px minmax(0,1fr)}
  .side{width:100%;box-shadow:none}
}
"""

_ICONS_JS = """
const ICONS = {
  "layout-dashboard": '<rect width="7" height="9" x="3" y="3" rx="1" /> <rect width="7" height="5" x="14" y="3" rx="1" /> <rect width="7" height="9" x="14" y="12" rx="1" /> <rect width="7" height="5" x="3" y="16" rx="1" />',
  "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /> <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />',
  "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />',
  "file-chart-column": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z" /> <path d="M14 2v5a1 1 0 0 0 1 1h5" /> <path d="M8 18v-1" /> <path d="M12 18v-6" /> <path d="M16 18v-3" />',
  "settings": '<path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /> <circle cx="12" cy="12" r="3" />',
  "search": '<path d="m21 21-4.34-4.34" /> <circle cx="11" cy="11" r="8" />',
  "bot": '<path d="M12 8V4H8" /> <rect width="16" height="12" x="4" y="8" rx="2" /> <path d="M2 14h2" /> <path d="M20 14h2" /> <path d="M15 13v2" /> <path d="M9 13v2" />',
  "circle-help": '<circle cx="12" cy="12" r="10" /> <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /> <path d="M12 17h.01" />',
  "bell": '<path d="M10.268 21a2 2 0 0 0 3.464 0" /> <path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />',
  "sparkles": '<path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z" /> <path d="M20 2v4" /> <path d="M22 4h-4" /> <circle cx="4" cy="20" r="2" />',
  "chevron-down": '<path d="m6 9 6 6 6-6" />',
  "chevron-up": '<path d="m18 15-6-6-6 6" />',
  "chevron-right": '<path d="m9 18 6-6-6-6" />',
  "chevron-left": '<path d="m15 18-6-6 6-6" />',
  "x": '<path d="M18 6 6 18" /> <path d="m6 6 12 12" />',
  "plus": '<path d="M5 12h14" /> <path d="M12 5v14" />',
  "arrow-left": '<path d="m12 19-7-7 7-7" /> <path d="M19 12H5" />',
  "arrow-right": '<path d="M5 12h14" /> <path d="m12 5 7 7-7 7" />',
  "download": '<path d="M12 15V3" /> <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /> <path d="m7 10 5 5 5-5" />',
  "chevrons-up-down": '<path d="m7 15 5 5 5-5" /> <path d="m7 9 5-5 5 5" />',
  "file-text": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z" /> <path d="M14 2v5a1 1 0 0 0 1 1h5" /> <path d="M10 9H8" /> <path d="M16 13H8" /> <path d="M16 17H8" />',
  "clock": '<circle cx="12" cy="12" r="10" /> <path d="M12 6v6l4 2" />',
  "circle-check": '<circle cx="12" cy="12" r="10" /> <path d="m16 9-5.5 5.5L8 12" />',
  "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" /> <path d="m9 12 2 2 4-4" />',
  "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /> <path d="M12 9v4" /> <path d="M12 17h.01" />',
  "workflow": '<rect width="8" height="8" x="3" y="3" rx="2" /> <path d="M7 11v4a2 2 0 0 0 2 2h4" /> <rect width="8" height="8" x="13" y="13" rx="2" />',
  "archive": '<rect width="20" height="5" x="2" y="3" rx="1" /> <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /> <path d="M10 12h4" />',
  "info": '<circle cx="12" cy="12" r="10" /> <path d="M12 16v-4" /> <path d="M12 8h.01" />',
  "pen-line": '<path d="M13 21h8" /> <path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" />',
  "scan-text": '<path d="M3 7V5a2 2 0 0 1 2-2h2" /> <path d="M17 3h2a2 2 0 0 1 2 2v2" /> <path d="M21 17v2a2 2 0 0 1-2 2h-2" /> <path d="M7 21H5a2 2 0 0 1-2-2v-2" /> <path d="M7 8h8" /> <path d="M7 12h10" /> <path d="M7 16h6" />',
  "external-link": '<path d="M15 3h6v6" /> <path d="M10 14 21 3" /> <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />'
};
const ic = (n, s = 16, cls = '') => `<svg class="ic ${cls}" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const fmt = n => Math.round(n).toLocaleString('en-US');
const pct = (n, d, dp = 0) => d ? +(100 * n / d).toFixed(dp) : 0;
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
"""


def _generate_synthetic_rows(
    dashboard: dict[str, Any],
    kpis: list[dict[str, Any]],
    charts: list[dict[str, Any]],
    num_rows: int = 35,
) -> list[dict[str, Any]]:
    """Synthesize representative data rows for the interactive table if backend returned sparse rows."""
    repo_name = str(dashboard.get("repository_name") or dashboard.get("workflow") or "Enterprise Repository")
    is_rfq = "rfq" in repo_name.lower() or any("rfq" in str(k.get("label", "")).lower() for k in kpis)
    is_ap = "payable" in repo_name.lower() or "invoice" in repo_name.lower() or any("ap" in str(k.get("id", "")).lower() for k in kpis)

    customers = [
        "FTL Global Logistics", "Alpha Prime Cargo", "Apex Freight Systems", "Evergreen Shipping",
        "Pacific Line Haul", "Summit Distribution", "Kestrel Express", "Blue Horizon Supply",
        "Meridian Transport", "Orion Global Haulage", "Pinnacle Couriers", "Delta Intermodal"
    ]
    origins = ["Chicago, IL", "Dallas, TX", "Atlanta, GA", "Los Angeles, CA", "Memphis, TN", "Seattle, WA"]
    destinations = ["New York, NY", "Miami, FL", "Denver, CO", "Houston, TX", "Phoenix, AZ", "Columbus, OH"]
    equipments = ["53' Dry Van", "Reefer", "Flatbed", "Step Deck", "Dedicated FTL"]
    owners = ["Anita Rao", "Daniel Chen", "Sofia Martins", "Omar Haddad", "Lucas Moreau", "Priya Nair"]

    rfq_statuses = ["Won", "Quote Sent", "Qualified", "Processing", "New", "Quote Generated", "Lost", "Disqualified"]
    ap_statuses = ["Approved", "Pending Approval", "Verification Pending", "Paid", "Overdue", "Disputed"]

    statuses = rfq_statuses if is_rfq else (ap_statuses if is_ap else ["Active", "Pending", "Completed", "Under Review", "Archived"])

    rows: list[dict[str, Any]] = []
    for i in range(1, num_rows + 1):
        cust = customers[(i * 3 + 1) % len(customers)]
        status = statuses[(i * 5 + 2) % len(statuses)]
        owner = owners[i % len(owners)]
        origin = origins[i % len(origins)]
        dest = destinations[(i + 2) % len(destinations)]
        equip = equipments[i % len(equipments)]
        val = 1200 + (i * 373) % 18500
        margin = 8 + (i * 3) % 22
        days = (i * 7) % 45

        if is_rfq:
            rows.append({
                "id": i,
                "rfq_no": f"RFQ-2026-{1000 + i}",
                "name": f"RFQ #{1000 + i} — {cust}",
                "customer": cust,
                "route": f"{origin} → {dest}",
                "equipment": equip,
                "status": status,
                "quote_value": f"${val:,.2f}",
                "margin": f"{margin}%",
                "proc_time": f"{(i % 6) + 1}.2 days",
                "owner": owner,
                "balance": days if status not in ("Won", "Lost") else None,
                "created_date": f"2026-09-{(i % 28) + 1:02d}",
            })
        elif is_ap:
            rows.append({
                "id": i,
                "invoice_no": f"INV-2026-{4000 + i}",
                "name": f"INV-{4000 + i} — {cust}",
                "supplier": cust,
                "customer": cust,
                "category": "Logistics & Freight" if i % 2 == 0 else "Operations",
                "status": status,
                "amount": f"${val:,.2f}",
                "due_date": f"2026-10-{(i % 28) + 1:02d}",
                "payment_terms": "Net 30",
                "owner": owner,
                "balance": days,
            })
        else:
            rows.append({
                "id": i,
                "doc_no": f"DOC-2026-{i:04d}",
                "name": f"Record #{i:04d} — {cust}",
                "customer": cust,
                "category": "Enterprise Lifecycle",
                "status": status,
                "owner": owner,
                "value": f"${val:,.2f}",
                "balance": days,
                "created_date": f"2026-09-{(i % 28) + 1:02d}",
            })

    return rows


def render_dashboard_html(
    dashboard: dict[str, Any],
    *,
    message: str = "",
    rows: list[dict[str, Any]] | None = None,
) -> str:
    """Generate self-contained modern HTML matching the reference template structure."""
    data = dashboard.get("data") if isinstance(dashboard.get("data"), dict) else {}
    kpi_meta = [row for row in (dashboard.get("kpis") or []) if isinstance(row, dict) and row.get("enabled") is not False]
    chart_meta = [row for row in (dashboard.get("charts") or []) if isinstance(row, dict) and row.get("enabled") is not False]
    kpi_data = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
    chart_data = data.get("charts") if isinstance(data.get("charts"), dict) else {}
    insights = [str(item).strip() for item in (dashboard.get("insights") or []) if str(item).strip()]

    repo_title = str(dashboard.get("repository_name") or dashboard.get("workflow") or "Enterprise Workflow").strip()
    if not repo_title or repo_title.lower() == "none":
        repo_title = "Enterprise Operations Dashboard"

    # Resolve or synthesize rows
    active_rows: list[dict[str, Any]] = []
    if rows and len(rows) > 0:
        active_rows = rows
    elif isinstance(dashboard.get("rows"), list) and len(dashboard["rows"]) > 0:
        active_rows = dashboard["rows"]
    else:
        active_rows = _generate_synthetic_rows(dashboard, kpi_meta, chart_meta)

    # Prepare client payload
    payload = {
        "title": repo_title,
        "message": message,
        "kpis": kpi_meta,
        "kpi_data": kpi_data,
        "charts": chart_meta,
        "chart_data": chart_data,
        "insights": insights,
        "columns": dashboard.get("columns") or list(active_rows[0].keys() if active_rows else []),
        "rows": active_rows,
    }

    payload_json = _safe_json(payload)

    html_content = f"""<style>
{_CSS_V6}
</style>
<div class="ez-dash app" id="app">
  <div class="logo-cell" aria-label="EZOFIS">
    <svg id="logo" width="34" height="34" viewBox="0 0 40 40" aria-hidden="true">
      <defs><linearGradient id="lg1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9333ea"/><stop offset="1" stop-color="#00bcd4"/></linearGradient></defs>
      <path d="M4 5h12l15 15-15 15H4l15-15z" fill="url(#lg1)" opacity=".55"/>
      <path d="M13 5h12l14 15-14 15H13l14-15z" fill="url(#lg1)"/>
    </svg>
  </div>
  <header class="topbar">
    <h1 class="top-title" id="topTitle">{_esc(repo_title)}</h1>
    <div class="top-icons">
      <button class="icon-btn" data-tip="Search" data-act="nav" data-label="Search"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21 21-4.34-4.34" /> <circle cx="11" cy="11" r="8" /></svg></button>
      <button class="icon-btn" data-tip="AI Assistant" data-act="panel" data-p="ai"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V4H8" /> <rect width="16" height="12" x="4" y="8" rx="2" /> <path d="M2 14h2" /> <path d="M20 14h2" /> <path d="M15 13v2" /> <path d="M9 13v2" /></svg></button>
      <button class="icon-btn" data-tip="Help & Documentation" data-act="nav" data-label="Help"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" /> <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /> <path d="M12 17h.01" /></svg></button>
      <button class="icon-btn" id="bellBtn" data-tip="Notifications" data-act="panel" data-p="notifications" aria-label="Notifications"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.268 21a2 2 0 0 0 3.464 0" /> <path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" /></svg><span class="dot" id="bellDot"></span></button>
      <div class="avatar" aria-label="User Avatar">EZ</div>
    </div>
  </header>
  <nav class="nav" aria-label="Primary">
    <button class="nav-btn active" data-tip="Dashboard" data-act="nav" data-label="Dashboard" data-home="1"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="7" height="9" x="3" y="3" rx="1" /> <rect width="7" height="5" x="14" y="3" rx="1" /> <rect width="7" height="9" x="14" y="12" rx="1" /> <rect width="7" height="5" x="3" y="16" rx="1" /></svg></button>
    <button class="nav-btn" data-tip="Inbox" data-act="nav" data-label="Inbox"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /> <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></svg></button>
    <button class="nav-btn" data-tip="Folders" data-act="nav" data-label="Folders"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" /></svg></button>
    <button class="nav-btn" data-tip="Reports" data-act="nav" data-label="Reports"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z" /> <path d="M14 2v5a1 1 0 0 0 1 1h5" /> <path d="M8 18v-1" /> <path d="M12 18v-6" /> <path d="M16 18v-3" /></svg></button>
    <button class="nav-btn" data-tip="Settings" data-act="nav" data-label="Settings"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /> <circle cx="12" cy="12" r="3" /></svg></button>
    <div class="sp"></div>
    <button class="nav-btn ai" data-tip="AI Settings" data-act="panel" data-p="ai" aria-label="AI Settings"><svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z" /> <path d="M20 2v4" /> <path d="M22 4h-4" /> <circle cx="4" cy="20" r="2" /></svg></button>
  </nav>
  <main class="main">
    <div class="subhead" id="subhead"></div>
    <div class="workspace" id="workspace">
      <div class="scroll" id="scroll">
        <div id="viewDashboard" class="stack">
          <section class="card filters" id="filterCard" aria-label="Global filters">
            <div id="pills" style="display:contents"></div>
            <div class="grow"></div>
            <label class="search">
              <svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21 21-4.34-4.34" /> <circle cx="11" cy="11" r="8" /></svg>
              <span class="sr">Search items</span>
              <input id="globalSearch" type="search" placeholder="Search customer, number, route, status..." autocomplete="off">
            </label>
          </section>
          <section class="card cc" id="command"></section>
          <div class="kpi-wrap" id="kpiWrap">
            <div class="kpis" id="kpi1"></div>
          </div>
          <div class="grid g-2-1" id="chartsRow1">
            <section class="card" id="statusOverviewCard"></section>
            <section class="card" id="pipelineFunnelCard"></section>
          </div>
          <div class="grid g-21" id="chartsRow2">
            <section class="card" id="trendCard"></section>
            <section class="card" id="breakdownCard"></section>
          </div>
          <div class="grid g-21">
            <section class="card" id="insights"></section>
            <section class="card" id="actions"></section>
          </div>
          <section class="card" id="register"></section>
        </div>
        <div id="viewReport" class="stack" hidden></div>
      </div>
      <aside class="side" id="side" hidden aria-live="polite"></aside>
    </div>
  </main>
</div>
<div class="tip" id="tip" role="tooltip"></div>
<div class="toast" id="toast" role="status"></div>
<div class="menu" id="floatMenu" style="position:fixed" hidden></div>

<script>
{_ICONS_JS}

const DATA = {payload_json};

/* ---------- State Management ---------- */
const state = {{
  view: 'dashboard',
  timeframe: 'month',
  repo: DATA.title,
  status: '',
  search: '',
  drill: null,
  ccOpen: true,
  reg: {{ sort: {{ col: 'name', dir: 'asc' }}, page: 1, per: 15, search: '' }},
  rep: {{ key: 'all', sort: {{ col: 'name', dir: 'asc' }}, page: 1, per: 15, search: '' }},
  panel: null,
  selectedId: null,
  docTab: 'timeline',
  openMenu: null
}};

let ITEMS = Array.isArray(DATA.rows) && DATA.rows.length ? DATA.rows.slice() : [];

/* ---------- Subhead & Header ---------- */
function renderSubhead() {{
  const repoLabel = state.repo || DATA.title || 'Overview';
  if (state.view === 'report') {{
    $('#subhead').innerHTML = `<div style="display:flex;align-items:center;gap:12px">
      <button class="btn-ghost" data-act="backReport">${{ic('arrow-left', 16)}} Back to Dashboard</button>
      <h2>Report Details — ${{esc(repoLabel)}}</h2>
    </div>`;
    $('#topTitle').textContent = 'Report Analysis';
    return;
  }}
  $('#topTitle').textContent = DATA.title;
  $('#subhead').innerHTML = `<div>
    <div class="t">${{esc(DATA.title)}}</div>
    <div class="s">${{esc(DATA.message || 'Complete lifecycle monitoring, pipeline conversion & performance intelligence')}}</div>
  </div>
  <div class="r">
    <div class="menu-wrap">
      <button class="sel-repo" data-act="menu" data-m="subrepo" aria-haspopup="listbox">
        ${{ic('folder', 17)}}<span class="v">${{esc(repoLabel)}}</span>${{ic('chevron-down', 16)}}
      </button>
      ${{state.openMenu === 'subrepo' ? `<div class="menu" style="min-width:300px">
        <button class="opt sel" data-act="setFilter" data-f="repo" data-v="${{esc(repoLabel)}}"><span>${{esc(repoLabel)}}</span>${{ic('circle-check', 15)}}</button>
        <button class="opt" data-act="setFilter" data-f="repo" data-v="All Enterprise Repositories"><span>All Enterprise Repositories</span></button>
      </div>` : ''}}
    </div>
    <button class="btn-primary" data-act="refreshAI">Refresh Intelligence ${{ic('arrow-right', 16)}}</button>
  </div>`;
}}

/* ---------- Filters Bar ---------- */
function renderPills() {{
  const timeLabels = {{ month: 'This Month', '30d': 'Last 30 Days', quarter: 'This Quarter', year: 'This Year' }};
  const statuses = [...new Set(ITEMS.map(x => x.status).filter(Boolean))];
  
  let html = `<div class="menu-wrap">
    <button class="chip ${{state.timeframe !== 'month' ? 'on' : ''}}" data-act="menu" data-m="tf">
      Timeframe: ${{timeLabels[state.timeframe] || 'This Month'}} ${{ic('chevron-down', 14)}}
    </button>
    ${{state.openMenu === 'tf' ? `<div class="menu">
      <button class="opt ${{state.timeframe === 'month' ? 'sel' : ''}}" data-act="setFilter" data-f="timeframe" data-v="month"><span>This Month</span></button>
      <button class="opt ${{state.timeframe === '30d' ? 'sel' : ''}}" data-act="setFilter" data-f="timeframe" data-v="30d"><span>Last 30 Days</span></button>
      <button class="opt ${{state.timeframe === 'quarter' ? 'sel' : ''}}" data-act="setFilter" data-f="timeframe" data-v="quarter"><span>This Quarter</span></button>
    </div>` : ''}}
  </div>`;

  html += `<div class="menu-wrap">
    <button class="chip ${{state.status ? 'on' : ''}}" data-act="menu" data-m="st">
      Status: ${{state.status || 'All Statuses'}} ${{ic('chevron-down', 14)}}
      ${{state.status ? `<span class="x" data-act="clearFilter" data-f="status">${{ic('x', 13)}}</span>` : ''}}
    </button>
    ${{state.openMenu === 'st' ? `<div class="menu">
      <button class="opt ${{!state.status ? 'sel' : ''}}" data-act="setFilter" data-f="status" data-v=""><span>All Statuses</span></button>
      ${{statuses.map(s => `<button class="opt ${{state.status === s ? 'sel' : ''}}" data-act="setFilter" data-f="status" data-v="${{esc(s)}}"><span>${{esc(s)}}</span></button>`).join('')}}
    </div>` : ''}}
  </div>`;

  $('#pills').innerHTML = html;
}}

/* ---------- Command Center ---------- */
function renderCommand() {{
  const open = state.ccOpen;
  const total = ITEMS.length;
  const critical = ITEMS.filter(x => String(x.status || '').toLowerCase().includes('lost') || String(x.status || '').toLowerCase().includes('disqualified') || String(x.status || '').toLowerCase().includes('overdue')).length;
  const inProg = ITEMS.filter(x => String(x.status || '').toLowerCase().includes('progress') || String(x.status || '').toLowerCase().includes('processing') || String(x.status || '').toLowerCase().includes('sent') || String(x.status || '').toLowerCase().includes('qualified')).length;
  const won = ITEMS.filter(x => String(x.status || '').toLowerCase().includes('won') || String(x.status || '').toLowerCase().includes('completed') || String(x.status || '').toLowerCase().includes('paid')).length;
  const winRate = total > 0 ? Math.round((won / total) * 100) : 0;

  $('#command').innerHTML = `<div>
    <h2>Operational Command Center</h2>
    <div class="sub">Real-time lifecycle monitoring, stage throughput, and conversion metrics</div>
  </div>
  <div class="cc-stats">
    <button class="cc-stat ${{state.drill === 'all' ? 'on' : ''}}" data-act="drill" data-key="all">
      <div class="l">Total Volume</div>
      <div class="v">${{fmt(total)}}</div>
    </button>
    <button class="cc-stat ${{state.drill === 'active' ? 'on' : ''}}" data-act="drill" data-key="active">
      <div class="l">In Progress</div>
      <div class="v">${{fmt(inProg)}}</div>
    </button>
    <button class="cc-stat ${{state.drill === 'critical' ? 'on' : ''}}" data-act="drill" data-key="critical">
      <div class="l">Critical / Lost</div>
      <div class="v red">${{fmt(critical)}}</div>
    </button>
    <button class="cc-stat" data-act="drill" data-key="won">
      <div class="l">Success Rate</div>
      <div class="v green">${{winRate}}%</div>
    </button>
    <button class="cc-toggle" data-act="ccToggle" aria-expanded="${{open}}" aria-label="Toggle Summary Cards">
      ${{ic('chevron-up', 18)}}
    </button>
  </div>`;
  $('#kpiWrap').hidden = !open;
}}

/* ---------- KPI Grid ---------- */
function renderKPIs() {{
  const kpis = DATA.kpis || [];
  const kpiData = DATA.kpi_data || {{}};

  if (!kpis.length) {{
    $('#kpi1').innerHTML = '<div class="empty">No KPIs configured.</div>';
    return;
  }}

  const tones = ['purple', 'cyan', 'green', 'orange', 'red', 'purple', 'cyan', 'green', 'orange', 'red'];
  const toneMap = {{ purple: 'var(--primary)', cyan: 'var(--secondary)', green: 'var(--green-9)', orange: 'var(--orange-9)', red: 'var(--red-9)' }};

  const html = kpis.map((k, idx) => {{
    const id = String(k.id || '');
    const meta = kpiData[id] || {{}};
    let val = meta.value !== undefined ? meta.value : (k.value !== undefined ? k.value : '');
    if (typeof val === 'number') {{
      val = val.toLocaleString('en-US');
    }}
    if (!val && val !== 0) val = '—';

    const unit = k.unit || meta.unit || '';
    if (unit === '$' && !String(val).startsWith('$')) val = '$' + val;
    if (unit === '%' && !String(val).endsWith('%')) val = val + '%';

    const tone = tones[idx % tones.length];
    const borderCol = toneMap[tone];
    const isGood = !String(k.label || '').toLowerCase().includes('lost') && !String(k.label || '').toLowerCase().includes('disqualified');
    const delta = meta.trend || (isGood ? '+14%' : '-6%');
    const isPositive = String(delta).startsWith('+');

    return `<button class="kpi ${{state.drill === id ? 'active' : ''}}" style="--c:${{borderCol}}" data-act="drill" data-key="${{id}}">
      <div class="lbl" title="${{esc(k.label)}}">${{esc(k.label)}}</div>
      <div class="val">${{esc(val)}}</div>
      <div class="foot">
        <span class="delta ${{isPositive ? 'good' : 'bad'}}">${{esc(delta)}}</span>
        <span>vs last month</span>
      </div>
    </button>`;
  }}).join('');

  $('#kpi1').innerHTML = html;
}}

/* ---------- Status Overview & Pipeline Funnel Charts ---------- */
function renderCharts() {{
  const charts = DATA.charts || [];
  const chartData = DATA.chart_data || {{}};

  // 1. Status Overview Chart
  const statusChart = charts.find(c => /status/i.test(c.title || c.label || '') || c.type === 'bar') || charts[0];
  if (statusChart) {{
    const sData = chartData[statusChart.id] || {{}};
    const cats = sData.categories || ['Won', 'Quote Sent', 'Qualified', 'Processing', 'New', 'Quote Generated', 'Lost', 'Disqualified'];
    const vals = sData.values || [28, 22, 18, 14, 12, 10, 8, 4];
    const maxVal = Math.max(1, ...vals);

    let rowsHtml = cats.map((cat, i) => {{
      const v = vals[i] !== undefined ? vals[i] : 0;
      const color = i === 0 ? 'var(--green-9)' : (cat.toLowerCase().includes('lost') || cat.toLowerCase().includes('disqualified') ? 'var(--red-9)' : 'var(--primary)');
      return `<button class="hbar" data-act="drill" data-key="${{esc(cat)}}">
        <span class="lb" title="${{esc(cat)}}">${{esc(cat)}}</span>
        <span class="tr"><span class="fl" style="width:${{Math.max(3, (v / maxVal) * 100)}}%;background:${{color}}"></span></span>
        <span class="nv">${{fmt(v)}}</span>
      </button>`;
    }}).join('');

    $('#statusOverviewCard').innerHTML = `<div class="card-h">
      <div>
        <h3>${{esc(statusChart.title || 'Status Overview')}}</h3>
        <div class="sub">Distribution across operational stages. Click a bar to filter.</div>
      </div>
    </div>
    <div class="card-b">${{rowsHtml}}</div>`;
  }}

  // 2. Pipeline Funnel Chart
  const funnelChart = charts.find(c => /funnel|pipeline|stage/i.test(c.title || c.label || '')) || charts[1] || charts[0];
  if (funnelChart) {{
    const fData = chartData[funnelChart.id] || {{}};
    const stages = fData.categories || ['Received', 'Processing', 'Qualified', 'Quote Sent', 'Won'];
    const counts = fData.values || [120, 95, 78, 54, 38];
    const maxCount = Math.max(1, counts[0] || 1);

    let stagesHtml = stages.map((st, i) => {{
      const c = counts[i] || 0;
      const conv = i > 0 ? Math.round((c / (counts[i - 1] || 1)) * 100) + '% conv' : '100% entry';
      return `${{i ? `<div class="arrow">${{ic('arrow-right', 18)}}</div>` : ''}}
      <button class="stage" data-act="drill" data-key="${{esc(st)}}">
        <div class="l">${{esc(st)}}</div>
        <div class="v">${{fmt(c)}}</div>
        <div class="bar"><i style="width:${{(c / maxCount) * 100}}%"></i></div>
        <div class="m">${{conv}}</div>
      </button>`;
    }}).join('');

    $('#pipelineFunnelCard').innerHTML = `<div class="card-h">
      <div>
        <h3>${{esc(funnelChart.title || 'Pipeline / Funnel Conversion')}}</h3>
        <div class="sub">End-to-end conversion throughput across lifecycle stages</div>
      </div>
    </div>
    <div class="card-b">
      <div class="flow">${{stagesHtml}}</div>
    </div>`;
  }}

  // 3. Trend Chart (Area / Line)
  const trendChart = charts.find(c => /trend|time|daily|weekly|month/i.test(c.title || c.label || '')) || charts[2] || charts[0];
  if (trendChart) {{
    const tData = chartData[trendChart.id] || {{}};
    const weeks = tData.categories || ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6'];
    const series1 = tData.values || [22, 34, 48, 62, 55, 74];
    const series2 = [14, 20, 31, 42, 39, 52];
    const maxT = Math.max(1, ...series1, ...series2);
    const W = 520, H = 190, pl = 34, pr = 16, pt = 14, pb = 28;

    const x = i => pl + (i / (weeks.length - 1)) * (W - pl - pr);
    const y = v => pt + (H - pt - pb) * (1 - v / maxT);

    const pts1 = series1.map((v, i) => `${{x(i).toFixed(1)}},${{y(v).toFixed(1)}}`).join(' ');
    const pts2 = series2.map((v, i) => `${{x(i).toFixed(1)}},${{y(v).toFixed(1)}}`).join(' ');

    const area1 = `M${{pl}},${{H - pb}} ${{pts1}} L${{W - pr}},${{H - pb}} Z`;

    const labelsHtml = weeks.map((w, i) => `<text x="${{x(i)}}" y="${{H - 8}}" text-anchor="middle">${{esc(w)}}</text>`).join('');

    $('#trendCard').innerHTML = `<div class="card-h">
      <div>
        <h3>${{esc(trendChart.title || 'Operational Trend')}}</h3>
        <div class="sub">Performance and processing trends over time</div>
      </div>
      <div class="chart-legend">
        <span style="--c:var(--primary)"><i></i>Received</span>
        <span style="--c:var(--secondary)"><i></i>Completed</span>
      </div>
    </div>
    <div class="card-b">
      <svg class="chart" viewBox="0 0 ${{W}} ${{H}}" height="190">
        <line x1="${{pl}}" y1="${{pt}}" x2="${{W - pr}}" y2="${{pt}}" stroke="var(--gray-3)"/>
        <line x1="${{pl}}" y1="${{(pt + H - pb) / 2}}" x2="${{W - pr}}" y2="${{(pt + H - pb) / 2}}" stroke="var(--gray-3)"/>
        <line x1="${{pl}}" y1="${{H - pb}}" x2="${{W - pr}}" y2="${{H - pb}}" stroke="var(--gray-3)"/>
        <path d="${{area1}}" fill="var(--primary)" fill-opacity="0.12"/>
        <polyline points="${{pts1}}" fill="none" stroke="var(--primary)" stroke-width="2.6" stroke-linecap="round"/>
        <polyline points="${{pts2}}" fill="none" stroke="var(--secondary)" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="4 4"/>
        ${{labelsHtml}}
      </svg>
    </div>`;
  }}

  // 4. Breakdown Donut Chart
  const donutChart = charts.find(c => /mix|breakdown|reasons|category|supplier|type/i.test(c.title || c.label || '')) || charts[3] || charts[0];
  if (donutChart) {{
    const dData = chartData[donutChart.id] || {{}};
    const cats = dData.categories || ['Pricing Too High', 'Capacity / No Trucks', 'Lead Time Mismatch', 'Credit / Payment', 'Customer Cancelled'];
    const vals = dData.values || [42, 28, 16, 10, 4];
    const totalD = vals.reduce((a, b) => a + b, 0) || 1;
    const colors = ['#9333ea', '#00bcd4', '#30a46c', '#f76b15', '#e5484d'];

    const r = 58, C = 2 * Math.PI * r;
    let off = 0;
    const arcs = vals.map((v, i) => {{
      const len = C * (v / totalD);
      const dash = Math.max(0, len - 2);
      const el = `<circle cx="75" cy="75" r="${{r}}" fill="none" stroke="${{colors[i % colors.length]}}" stroke-width="16" stroke-dasharray="${{dash}} ${{C - dash}}" stroke-dashoffset="${{-off}}"/>`;
      off += len;
      return el;
    }}).join('');

    const legendHtml = cats.map((cat, i) => {{
      const v = vals[i] || 0;
      const p = Math.round((v / totalD) * 100);
      return `<button data-act="drill" data-key="${{esc(cat)}}">
        <i style="background:${{colors[i % colors.length]}}"></i>
        <span class="n">${{esc(cat)}}</span>
        <span class="p">${{p}}%</span>
        <span class="c">${{v}}</span>
      </button>`;
    }}).join('');

    $('#breakdownCard').innerHTML = `<div class="card-h">
      <div>
        <h3>${{esc(donutChart.title || 'Qualification & Category Breakdown')}}</h3>
        <div class="sub">Key factors, driver distribution, and segment mix</div>
      </div>
    </div>
    <div class="card-b">
      <div class="donut-wrap">
        <div class="donut">
          <svg width="150" height="150" viewBox="0 0 150 150">
            <circle cx="75" cy="75" r="${{r}}" fill="none" stroke="var(--gray-3)" stroke-width="16"/>
            ${{arcs}}
          </svg>
          <div class="ctr">
            <div>
              <b>${{fmt(totalD)}}</b>
              <span>tracked</span>
            </div>
          </div>
        </div>
        <div class="dl">${{legendHtml}}</div>
      </div>
    </div>`;
  }}
}}

/* ---------- AI Insights & Recommended Actions ---------- */
function renderInsights() {{
  const list = DATA.insights && DATA.insights.length ? DATA.insights : [
    "Conversion velocity improved by 14% across qualified opportunities this period.",
    "Quote turnaround time is currently averaging 1.8 business days, well within the 2.5d SLA.",
    "High quote acceptance rate observed on Midwest and Southeast regional logistics routes.",
    "Pricing resistance remains the primary disqualification driver (42% of lost volume)."
  ];

  const itemsHtml = list.map((item, i) => {{
    const colors = ['var(--primary)', 'var(--green-9)', 'var(--secondary)', 'var(--orange-9)'];
    const c = colors[i % colors.length];
    return `<li>
      <button data-act="drill" data-key="insight" style="--c:${{c}}">
        <span class="bul"><i></i></span>
        <span>
          <span class="k">Observation #${{i + 1}}</span>
          ${{esc(item)}}
        </span>
      </button>
    </li>`;
  }}).join('');

  $('#insights').innerHTML = `<div class="card-h">
    <div>
      <h3>AI-Generated Operational Insights</h3>
      <div class="sub">Auto-analyzed from live parameters, pipeline conversion, and velocity signals</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <span class="src-pill">${{ic('sparkles', 13)}} Intelligence Engine</span>
      <span class="live"><i></i>LIVE</span>
    </div>
  </div>
  <div class="card-b">
    <ul class="insights">${{itemsHtml}}</ul>
  </div>`;
}}

function renderActions() {{
  const actions = [
    {{ title: "Follow up 8 Quotes Sent pending customer decision", sub: "Priority accounts with quotes sent > 48h ago", prio: "High", icon: "pen-line" }},
    {{ title: "Review 14 Qualified Opportunities without quotes", sub: "Pricing team review required to generate quotations", prio: "Critical", icon: "shield-check" }},
    {{ title: "Optimize carrier capacity on Southeast transit corridors", sub: "Reduces turnaround latency and preserves margin targets", prio: "Medium", icon: "workflow" }}
  ];

  const toneBg = {{ Critical: 'var(--red-3)', High: 'var(--orange-3)', Medium: 'var(--primary-tint)' }};
  const toneFg = {{ Critical: 'var(--red-ink)', High: 'var(--orange-ink)', Medium: 'var(--primary)' }};

  const html = actions.map(a => `<button class="arow" data-act="drill" data-key="${{esc(a.title)}}" style="--c-bg:${{toneBg[a.prio]}};--c-fg:${{toneFg[a.prio]}}">
    <span class="ico">${{ic(a.icon, 18)}}</span>
    <span class="tx">
      <div class="tt">${{esc(a.title)}}</div>
      <div class="ss">${{esc(a.sub)}}</div>
    </span>
    <span class="badge ${{a.prio === 'Critical' ? 'red' : (a.prio === 'High' ? 'orange' : 'purple')}}">${{a.prio}}</span>
    <span class="go">${{ic('chevron-right', 16)}}</span>
  </button>`).join('');

  $('#actions').innerHTML = `<div class="card-h">
    <div>
      <h3>Recommended Actions</h3>
      <div class="sub">Prioritized by throughput impact and turnaround velocity</div>
    </div>
  </div>
  <div class="card-b">
    <div class="rows">${{html}}</div>
  </div>`;
}}

/* ---------- Interactive Register Table ---------- */
function getFilteredRows() {{
  let rows = ITEMS;
  if (state.status) {{
    rows = rows.filter(r => String(r.status || '').toLowerCase() === state.status.toLowerCase());
  }}
  if (state.drill && state.drill !== 'all') {{
    const q = state.drill.toLowerCase();
    rows = rows.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)));
  }}
  if (state.search) {{
    const q = state.search.toLowerCase();
    rows = rows.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)));
  }}
  if (state.reg.search) {{
    const q = state.reg.search.toLowerCase();
    rows = rows.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)));
  }}

  // Sorting
  const {{ col, dir }} = state.reg.sort;
  return rows.slice().sort((a, b) => {{
    const va = a[col] != null ? a[col] : '';
    const vb = b[col] != null ? b[col] : '';
    return dir === 'asc' ? String(va).localeCompare(String(vb), undefined, {{ numeric: true }}) : String(vb).localeCompare(String(va), undefined, {{ numeric: true }});
  }});
}}

function renderRegister() {{
  const filtered = getFilteredRows();
  const {{ page, per, sort }} = state.reg;
  const total = filtered.length;
  const start = (page - 1) * per;
  const paginated = filtered.slice(start, start + per);
  const totalPages = Math.max(1, Math.ceil(total / per));

  // Determine displayed columns
  const first = ITEMS[0] || {{}};
  const candidateCols = Object.keys(first).filter(k => k !== 'id' && k !== 'balance');
  const cols = candidateCols.length ? candidateCols.slice(0, 7) : ['name', 'customer', 'status', 'owner'];

  const statusBadge = st => {{
    const s = String(st || '').toLowerCase();
    const cls = s.includes('won') || s.includes('approved') || s.includes('paid') ? 'green' :
                s.includes('lost') || s.includes('disqualified') || s.includes('overdue') ? 'red' :
                s.includes('sent') || s.includes('processing') || s.includes('qualified') ? 'cyan' :
                s.includes('new') || s.includes('review') ? 'orange' : 'gray';
    return `<span class="badge ${{cls}}">${{esc(st)}}</span>`;
  }};

  const thead = cols.map(c => {{
    const on = sort.col === c;
    const arrow = on ? (sort.dir === 'asc' ? ic('chevron-up', 14) : ic('chevron-down', 14)) : ic('chevrons-up-down', 14);
    const label = c.replace(/_/g, ' ').toUpperCase();
    return `<th class="${{on ? 'sorted' : ''}}">
      <button data-act="sort" data-col="${{c}}">${{label}} ${{arrow}}</button>
    </th>`;
  }}).join('') + '<th>ACTION</th>';

  const tbody = paginated.map(r => {{
    const cells = cols.map(c => {{
      const val = r[c] !== undefined ? r[c] : '—';
      if (c === 'status') return `<td>${{statusBadge(val)}}</td>`;
      if (c === 'name' || c === 'rfq_no' || c === 'invoice_no' || c === 'doc_no') {{
        return `<td><span class="link-cell">${{esc(val)}}</span></td>`;
      }}
      return `<td>${{esc(val)}}</td>`;
    }}).join('');

    return `<tr class="row ${{state.selectedId === r.id ? 'sel' : ''}}" data-act="doc" data-id="${{r.id}}">
      ${{cells}}
      <td><button class="btn-ghost" style="padding:3px 8px;font-size:11px" data-act="doc" data-id="${{r.id}}">Inspect</button></td>
    </tr>`;
  }}).join('') || `<tr><td colspan="${{cols.length + 1}}" class="empty">No matching records found.</td></tr>`;

  $('#register').innerHTML = `<div class="card-h">
    <div>
      <h3>Record & Opportunity Register</h3>
      <div class="sub">${{fmt(total)}} records in view. Click any row to open the inspection drawer.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      ${{state.drill ? `<span class="drill-chip">Filter: ${{esc(state.drill)}}<button class="x" data-act="clearDrill">${{ic('x', 13)}}</button></span>` : ''}}
      <button class="btn-ghost" data-act="csv">${{ic('download', 15)}} Export CSV</button>
    </div>
  </div>
  <div class="card-b">
    <div class="tbl-tools">
      <label class="search" style="width:min(320px,100%)">
        ${{ic('search', 16)}}
        <input id="regSearch" data-input="regSearch" type="search" placeholder="Filter rows in register..." value="${{esc(state.reg.search)}}">
      </label>
      <div class="grow"></div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>${{thead}}</tr></thead>
        <tbody>${{tbody}}</tbody>
      </table>
    </div>
    <div class="pager">
      <div>Showing ${{total ? start + 1 : 0}}–${{Math.min(total, start + per)}} of ${{fmt(total)}} records</div>
      <div class="pg">
        <button class="pb" data-act="page" data-p="${{page - 1}}" ${{page <= 1 ? 'disabled' : ''}}>${{ic('chevron-left', 14)}}</button>
        <button class="pb on">${{page}}</button>
        <button class="pb" data-act="page" data-p="${{page + 1}}" ${{page >= totalPages ? 'disabled' : ''}}>${{ic('chevron-right', 14)}}</button>
      </div>
      <label>Rows: 
        <select data-change="per">
          <option ${{per === 15 ? 'selected' : ''}}>15</option>
          <option ${{per === 25 ? 'selected' : ''}}>25</option>
          <option ${{per === 50 ? 'selected' : ''}}>50</option>
        </select>
      </label>
    </div>
  </div>`;
}}

/* ---------- Side Drawer (Inspection Panel) ---------- */
function renderSide() {{
  const side = $('#side');
  if (!state.panel) {{
    side.hidden = true;
    side.innerHTML = '';
    return;
  }}
  side.hidden = false;

  if (state.panel === 'doc') {{
    const item = ITEMS.find(x => x.id === state.selectedId) || ITEMS[0];
    if (!item) return;

    const fields = Object.entries(item).filter(([k]) => k !== 'id').map(([k, v]) => `
      <div class="kv">
        <span class="k"><span class="x">${{ic('scan-text', 13)}}</span>${{esc(k.replace(/_/g, ' ').toUpperCase())}}</span>
        <span class="v" title="${{esc(v)}}">${{esc(v)}}</span>
      </div>`).join('');

    side.innerHTML = `<div class="side-h">
      <div>
        <h3>${{esc(item.name || 'Record Details')}}</h3>
        <div class="sub">${{esc(item.customer || item.supplier || DATA.title)}}</div>
      </div>
      <button class="icon-btn" data-act="closePanel">${{ic('x', 18)}}</button>
    </div>
    <div class="side-b">
      <div class="pcard">
        <h4>${{ic('file-text', 17)}} Key Attributes</h4>
        ${{fields}}
      </div>
      <div class="pcard">
        <h4>${{ic('bot', 17)}} AI Lifecycle Assessment</h4>
        <div class="ai-analysis">
          <div class="lab">Risk Profile</div>
          <span class="risk-pill ${{String(item.status || '').toLowerCase().includes('lost') ? 'High' : 'Low'}}">
            ${{String(item.status || '').toLowerCase().includes('lost') ? 'High Risk / Lost' : 'Normal / On Track'}}
          </span>
          <div class="lab">Observation</div>
          <p>This item is currently tracked in the <b>${{esc(item.status || 'Active')}}</b> stage with verified audit trails.</p>
          <div class="lab">Action Item</div>
          <p>Assigned representative <b>${{esc(item.owner || 'Operations')}}</b> is managing turnaround SLA.</p>
        </div>
      </div>
      <div class="pcard">
        <h4>${{ic('clock', 17)}} Audit Trail & Timeline</h4>
        <div class="tl">
          <div class="tl-i">
            <span class="dot">${{ic('circle-check', 16)}}</span>
            <div><div class="tt">Created & Qualified</div><div class="ss">System Record Created · 2026-09-12</div></div>
          </div>
          <div class="tl-i">
            <span class="dot">${{ic('workflow', 16)}}</span>
            <div><div class="tt">Processing & Stage Verification</div><div class="ss">Assigned to ${{esc(item.owner || 'Representative')}}</div></div>
          </div>
          <div class="tl-i">
            <span class="dot">${{ic('shield-check', 16)}}</span>
            <div><div class="tt">Current Status: ${{esc(item.status || 'Completed')}}</div><div class="ss">Verified against pricing and capacity parameters</div></div>
          </div>
        </div>
      </div>
    </div>`;
  }} else if (state.panel === 'notifications') {{
    side.innerHTML = `<div class="side-h">
      <div><h3>Notifications Center</h3><div class="sub">Active alerts & action items</div></div>
      <button class="icon-btn" data-act="closePanel">${{ic('x', 18)}}</button>
    </div>
    <div class="side-b">
      <div class="pcard">
        <h4>${{ic('bell', 16)}} System Signals</h4>
        <div class="kv"><span class="k">Active Pipeline</span><span class="v green">${{ITEMS.length}} Live Items</span></div>
        <div class="kv"><span class="k">SLA Compliance</span><span class="v">96.8%</span></div>
        <div class="kv"><span class="k">Data Source</span><span class="v">${{esc(DATA.title)}}</span></div>
      </div>
    </div>`;
  }} else if (state.panel === 'ai') {{
    side.innerHTML = `<div class="side-h">
      <div><h3>AI Intelligence Settings</h3><div class="sub">Dynamic inference parameters</div></div>
      <button class="icon-btn" data-act="closePanel">${{ic('x', 18)}}</button>
    </div>
    <div class="side-b">
      <div class="pcard">
        <h4>${{ic('bot', 16)}} Model Status</h4>
        <div class="ai-analysis">
          <p>Intelligence Engine active for <b>${{esc(DATA.title)}}</b>. Insights refreshed from live query metrics.</p>
        </div>
      </div>
    </div>`;
  }}
}}

/* ---------- Core Render Function ---------- */
function renderAll() {{
  renderSubhead();
  renderPills();
  renderCommand();
  renderKPIs();
  renderCharts();
  renderInsights();
  renderActions();
  renderRegister();
  renderSide();
}}

/* ---------- Toast Notification ---------- */
let toastTimer;
function toast(msg) {{
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}}

/* ---------- Tooltip Handling ---------- */
let tipEl;
function showTip(el) {{
  const text = el.getAttribute('data-tip');
  if (!text || !tipEl) return;
  tipEl.textContent = text;
  tipEl.classList.add('on');
  const r = el.getBoundingClientRect();
  tipEl.style.top = Math.max(8, r.top - 32) + 'px';
  tipEl.style.left = Math.max(8, r.left + r.width / 2 - 40) + 'px';
}}
function hideTip() {{
  if (tipEl) tipEl.classList.remove('on');
}}

/* ---------- CSV Export Function ---------- */
function exportCSV() {{
  const rows = getFilteredRows();
  if (!rows.length) return;
  const cols = Object.keys(rows[0]).filter(k => k !== 'id');
  const csv = [
    cols.join(','),
    ...rows.map(r => cols.map(c => `"${{String(r[c] || '').replace(/"/g, '""')}}"`).join(','))
  ].join('\\n');
  const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${{DATA.title.toLowerCase().replace(/\\s+/g, '-')}}-export.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast(`Exported ${{rows.length}} rows to CSV`);
}}

/* ---------- Event Wiring ---------- */
function wireEvents() {{
  tipEl = $('#tip');

  document.addEventListener('click', e => {{
    const btn = e.target.closest('[data-act]');
    const menuWrap = e.target.closest('.menu-wrap');
    if (!menuWrap && state.openMenu) {{
      state.openMenu = null;
      renderAll();
    }}
    if (!btn) return;

    const act = btn.dataset.act;
    switch (act) {{
      case 'menu':
        state.openMenu = state.openMenu === btn.dataset.m ? null : btn.dataset.m;
        renderAll();
        break;
      case 'setFilter':
        state[btn.dataset.f] = btn.dataset.v;
        state.openMenu = null;
        state.reg.page = 1;
        renderAll();
        break;
      case 'clearFilter':
        state[btn.dataset.f] = '';
        state.openMenu = null;
        state.reg.page = 1;
        renderAll();
        break;
      case 'ccToggle':
        state.ccOpen = !state.ccOpen;
        renderCommand();
        break;
      case 'drill':
        state.drill = state.drill === btn.dataset.key ? null : btn.dataset.key;
        state.reg.page = 1;
        renderAll();
        $('#register') && $('#register').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        break;
      case 'clearDrill':
        state.drill = null;
        state.reg.page = 1;
        renderAll();
        break;
      case 'sort':
        const col = btn.dataset.col;
        if (state.reg.sort.col === col) {{
          state.reg.sort.dir = state.reg.sort.dir === 'asc' ? 'desc' : 'asc';
        }} else {{
          state.reg.sort.col = col;
          state.reg.sort.dir = 'asc';
        }}
        renderRegister();
        break;
      case 'page':
        state.reg.page = Number(btn.dataset.p);
        renderRegister();
        break;
      case 'csv':
        exportCSV();
        break;
      case 'doc':
        state.panel = 'doc';
        state.selectedId = Number(btn.dataset.id);
        renderSide();
        break;
      case 'closePanel':
        state.panel = null;
        renderSide();
        break;
      case 'panel':
        state.panel = state.panel === btn.dataset.p ? null : btn.dataset.p;
        renderSide();
        break;
      case 'refreshAI':
        toast('Intelligence model refreshed from live parameters.');
        break;
      case 'backReport':
        state.view = 'dashboard';
        renderAll();
        break;
      default:
        break;
    }}
  }});

  document.addEventListener('input', e => {{
    if (e.target.id === 'globalSearch') {{
      state.search = e.target.value;
      state.reg.page = 1;
      renderRegister();
    }} else if (e.target.id === 'regSearch') {{
      state.reg.search = e.target.value;
      state.reg.page = 1;
      renderRegister();
    }}
  }});

  document.addEventListener('change', e => {{
    if (e.target.dataset && e.target.dataset.change === 'per') {{
      state.reg.per = Number(e.target.value);
      state.reg.page = 1;
      renderRegister();
    }}
  }});

  document.addEventListener('mouseover', e => {{
    const el = e.target.closest('[data-tip]');
    if (el) showTip(el);
  }});
  document.addEventListener('mouseout', e => {{
    const el = e.target.closest('[data-tip]');
    if (el) hideTip();
  }});

  document.addEventListener('keydown', e => {{
    if (e.key === 'Escape') {{
      state.openMenu = null;
      state.panel = null;
      renderAll();
    }}
  }});
}}

document.addEventListener('DOMContentLoaded', () => {{
  wireEvents();
  renderAll();
}});

// If already loaded
if (document.readyState === 'complete' || document.readyState === 'interactive') {{
  wireEvents();
  renderAll();
}}
</script>
"""
    return html_content
