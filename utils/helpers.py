"""Shared formatting, security sanitization, accessibility, and date helper functions."""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from typing import Any, Optional, Union

from config import CURRENCY_SYMBOL

# WCAG 2.2 AA/AAA verified status background colors paired with #FFFFFF foreground text
# Every single color below exceeds the WCAG 2.2 AA 4.5:1 contrast threshold.
STATUS_COLOR_MAP: dict[str, str] = {
    "Out of Stock": "#B91C1C",       # 6.47:1 contrast vs #FFFFFF
    "Low Stock": "#92400E",          # 7.05:1 contrast vs #FFFFFF
    "Healthy": "#166534",            # 7.13:1 contrast vs #FFFFFF
    "In Stock (No ROP)": "#0369A1",  # 5.93:1 contrast vs #FFFFFF
    "Overstock": "#6D28D9",          # 7.04:1 contrast vs #FFFFFF
    "Critical": "#B91C1C",           # 6.47:1 contrast vs #FFFFFF
    "High": "#9A3412",               # 6.98:1 contrast vs #FFFFFF
    "Medium": "#92400E",             # 7.05:1 contrast vs #FFFFFF
    "Low": "#1D4ED8",                # 6.70:1 contrast vs #FFFFFF
    "Pending Approval": "#92400E",   # 7.05:1 contrast vs #FFFFFF
    "Approved": "#166534",           # 7.13:1 contrast vs #FFFFFF
    "Rejected": "#B91C1C",           # 6.47:1 contrast vs #FFFFFF
    "Modified": "#1D4ED8",           # 6.70:1 contrast vs #FFFFFF
    "Unavailable": "#475569",        # 7.58:1 contrast vs #FFFFFF
    "Unknown": "#475569",            # 7.58:1 contrast vs #FFFFFF
}

# Non-color textual/symbolic markers so status is NEVER communicated via color alone (WCAG 1.4.1)
STATUS_TEXT_PREFIX: dict[str, str] = {
    "Out of Stock": "✕",
    "Low Stock": "▲",
    "Healthy": "✓",
    "In Stock (No ROP)": "●",
    "Overstock": "⬆",
    "Critical": "✕",
    "High": "▲",
    "Medium": "◆",
    "Low": "●",
    "Pending Approval": "◷",
    "Approved": "✓",
    "Rejected": "✕",
    "Modified": "✎",
    "Unavailable": "—",
    "Unknown": "?",
}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_html(value: Any) -> str:
    """Escape untrusted strings before interpolating into HTML blocks (XSS defense)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def sanitize_csv_cell(value: Any) -> Any:
    """Prevent CSV / spreadsheet formula injection (DDE) on string cells.

    Numeric types are returned unchanged so exported CSVs remain cleanly analyzable.
    """
    if isinstance(value, str):
        stripped_leading = value.lstrip(" ")
        if stripped_leading.startswith(_FORMULA_PREFIXES):
            return "'" + value
    return value


def format_inr(amount: Union[int, float, None], decimals: int = 2, missing_label: str = "N/A") -> str:
    """Format a number using the Indian numbering system with ₹ symbol.

    Example: 125450.5 -> '₹1,25,450.50'
    Returns `missing_label` if value is NaN/Inf.
    """
    if amount is None:
        return f"{CURRENCY_SYMBOL}0.00"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return missing_label
    if math.isnan(value) or math.isinf(value):
        return missing_label

    sign = "-" if value < 0 else ""
    value = abs(value)
    formatted = f"{value:.{decimals}f}"
    if decimals > 0:
        integer_part, decimal_part = formatted.split(".")
        decimal_suffix = f".{decimal_part}"
    else:
        integer_part = formatted
        decimal_suffix = ""

    if len(integer_part) <= 3:
        grouped = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        parts = []
        while len(remaining) > 2:
            parts.append(remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            parts.append(remaining)
        parts.reverse()
        grouped = ",".join(parts) + "," + last_three

    return f"{sign}{CURRENCY_SYMBOL}{grouped}{decimal_suffix}"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def today_iso() -> str:
    """Return today's date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero."""
    if denominator is None or denominator == 0:
        return default
    return float(numerator) / float(denominator)


def _is_missing_num(val: object) -> bool:
    if val is None:
        return True
    try:
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return True


def classify_stock_status(
    current_stock: float,
    reorder_point: Optional[float],
) -> str:
    """Return human-readable inventory status label without fabricating missing thresholds."""
    if _is_missing_num(current_stock):
        return "Unknown"
    if float(current_stock) <= 0:
        return "Out of Stock"
    if _is_missing_num(reorder_point):
        return "In Stock (No ROP)"
    if float(current_stock) <= float(reorder_point):
        return "Low Stock"
    return "Healthy"


def status_color(status: str) -> str:
    """Return WCAG 2.2 AA-compliant hex background color associated with a status label."""
    return STATUS_COLOR_MAP.get(status, "#475569")


def status_prefix(status: str) -> str:
    """Return non-color symbolic indicator paired with a status label."""
    return STATUS_TEXT_PREFIX.get(status, "•")
