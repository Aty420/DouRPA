"""Global UI readability/design tokens for DouRPA.

All values are logical Qt pixels unless otherwise noted. Qt 6 applies Windows
DPI scaling on top of these logical values, so the same hierarchy remains
readable at 100% and 125% Windows scale without CSS zoom/transform tricks.
"""

FONT = {
    "page_title": 30,
    "section_title": 19,
    "sidebar": 16,
    "body": 14,
    "label": 14,
    "table_header": 14,
    "aux": 13,
    "small": 12,
    "metric_value": 32,
    "run_status": 19,
}

SIZE = {
    "control_height": 42,
    "top_control_height": 48,
    "table_row_height": 44,
    "table_header_height": 42,
    "sidebar_item_height": 50,
    "compact_sidebar_width": 250,
    "normal_sidebar_width": 270,
}
