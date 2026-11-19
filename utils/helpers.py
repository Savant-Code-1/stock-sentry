"""Shared formatting, date, and calculation helper functions."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional, Union

from config import CURRENCY_SYMBOL


def format_inr(amount: Union[int, float, None], decimals: int = 2, missing_label: str = "N/A") -> str:
    """Format a number using the Indian numbering system with ₹ symbol.

    Example: 125450.5 -> '₹1,25,450.50'
    Returns `missing_label` if value is NaN, or '₹0.00' if None when explicitly needed.
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
    """Return hex color associated with stock or anomaly status."""
    mapping = {
        "Out of Stock": "#DC2626",
        "Low Stock": "#D97706",
        "Healthy": "#16A34A",
        "In Stock (No ROP)": "#0284C7",
        "Overstock": "#7C3AED",
        "Critical": "#DC2626",
        "High": "#EA580C",
        "Medium": "#D97706",
        "Low": "#2563EB",
        "Pending Approval": "#D97706",
        "Approved": "#16A34A",
        "Rejected": "#DC2626",
        "Modified": "#2563EB",
    }
    return mapping.get(status, "#475569")
