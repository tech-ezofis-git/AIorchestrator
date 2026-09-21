"""EZOFIS v6 design tokens used by HTML and chart colors.

Prefer frontend light.css semantic tokens via .ez-dash bridges (--ez-violet / --ez-cyan).
Hex fallbacks keep standalone / SVG usable when host CSS is absent.
"""

# Prefer local .ez-dash bridges (mapped from --accent-primary / --primary-* in render CSS).
ACCENT_PRIMARY = "var(--ez-violet, var(--accent-primary, var(--primary-9, #7c5cff)))"
PRIMARY = "var(--ez-cyan, var(--primary, var(--secondary-9, #00bcd4)))"

CHART_PALETTE = [
    ACCENT_PRIMARY,
    PRIMARY,
    "var(--ez-err, var(--error-main, var(--red-9, #e5484d)))",
    "var(--pink-9, #d6409f)",
    "var(--purple-9, #8e4ec6)",
    "var(--ez-ok, var(--success-main, var(--green-9, #30a46c)))",
    "var(--orange-8, #ec9455)",
    "var(--indigo-9, #3e63dd)",
    "var(--teal-9, #12a594)",
    "var(--ez-muted, var(--gray-9, #8181a0))",
]

ACCENT_CYAN = PRIMARY
ERROR_MAIN = "var(--ez-err, var(--error-main, var(--red-9, #e5484d)))"
SUCCESS_MAIN = "var(--ez-ok, var(--success-main, var(--green-9, #30a46c)))"
WARNING_MAIN = "var(--ez-warn, var(--warning-main, var(--orange-9, #f76b15)))"
INFO_MAIN = "var(--ez-info, var(--info-main, var(--blue-9, #0090ff)))"
GRID_LIGHT = "var(--ez-line, var(--chart-grid, var(--border-default, var(--gray-3, #e6e6ef))))"
AXIS_TEXT_LIGHT = "var(--ez-sub, var(--text-secondary, var(--gray-11, #4d4d73)))"
AXIS_MUTED = "var(--ez-muted, var(--chart-axis, var(--text-muted, var(--gray-9, #8181a0))))"
