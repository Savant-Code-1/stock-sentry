"""Statistical baseline demand forecasting and stockout risk calculations.

Note: Uses transparent statistical methods (Moving Average and Exponential Smoothing).
These are baseline statistical estimates, NOT deep machine-learning predictions.
Never fabricates forecasts when dated sales history is unavailable.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from config import (
    DEFAULT_FORECAST_HORIZON_DAYS,
    DEFAULT_FORECAST_WINDOW_DAYS,
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_SAFETY_STOCK_DAYS,
    EMA_ALPHA,
)
from database.db import fetch_all, fetch_one
from utils.helpers import safe_divide

DbPath = Optional[Union[str, Path]]


def calculate_average_daily_sales(total_units_sold: float, number_of_days: int) -> float:
    """Average Daily Sales = Total Units Sold / Number of Days."""
    if number_of_days <= 0:
        return 0.0
    return round(safe_divide(total_units_sold, number_of_days, default=0.0), 2)


def calculate_days_remaining(current_stock: float, avg_daily_sales: float) -> Optional[float]:
    """Days Remaining = Current Stock / Average Daily Sales."""
    if current_stock <= 0:
        return 0.0
    if avg_daily_sales <= 0:
        return None
    return round(float(current_stock) / float(avg_daily_sales), 1)


def calculate_reorder_point(
    avg_daily_demand: float,
    supplier_lead_time_days: int,
    safety_stock: float,
) -> int:
    """Reorder Point = Average Daily Demand * Supplier Lead Time + Safety Stock."""
    if avg_daily_demand < 0 or supplier_lead_time_days < 0 or safety_stock < 0:
        raise ValueError("Forecast parameters cannot be negative.")
    raw = (float(avg_daily_demand) * int(supplier_lead_time_days)) + float(safety_stock)
    return int(np.ceil(raw))


def compute_moving_average(series: pd.Series, window: int = DEFAULT_FORECAST_WINDOW_DAYS) -> float:
    """Compute simple moving average over the last `window` days."""
    if series.empty:
        return 0.0
    tail = series.tail(max(1, window))
    return round(float(tail.mean()), 2)


def compute_exponential_smoothing(series: pd.Series, alpha: float = EMA_ALPHA) -> float:
    """Compute single exponential smoothing forecast for the next day."""
    if series.empty:
        return 0.0
    level = float(series.iloc[0])
    for val in series.iloc[1:]:
        level = alpha * float(val) + (1.0 - alpha) * level
    return round(max(0.0, level), 2)


def classify_stockout_risk(
    current_stock: int,
    days_remaining: Optional[float],
    lead_time_days: int,
) -> str:
    """Categorize stockout risk relative to supplier delivery lead time."""
    if current_stock <= 0:
        return "Critical"
    if days_remaining is None:
        return "Low"
    if days_remaining <= lead_time_days:
        return "Critical"
    if days_remaining <= lead_time_days + 3:
        return "High"
    if days_remaining <= lead_time_days + 7:
        return "Medium"
    return "Low"


def check_forecasting_readiness(db_path: DbPath = None) -> dict:
    """Verify whether the active database has sufficient dated sales history for forecasting."""
    row = fetch_one(
        """
        SELECT COUNT(*) AS total_sales,
               COUNT(DISTINCT date) AS distinct_dates,
               MIN(date) AS min_date,
               MAX(date) AS max_date
        FROM sales
        WHERE date IS NOT NULL AND TRIM(date) != ''
        """,
        db_path=db_path,
    )
    total_sales = int(row["total_sales"]) if row else 0
    distinct_dates = int(row["distinct_dates"]) if row else 0

    if total_sales == 0:
        return {
            "ready": False,
            "total_dated_sales": 0,
            "distinct_dates": 0,
            "reason": "No dated sales records exist in the active dataset. Upload data containing 'Units Sold' and 'Transaction Date' to enable demand forecasting.",
        }
    if distinct_dates < 2:
        return {
            "ready": False,
            "total_dated_sales": total_sales,
            "distinct_dates": distinct_dates,
            "reason": f"Only {distinct_dates} distinct sales date was found. At least 2 distinct dates are required to estimate daily demand trends.",
        }
    return {
        "ready": True,
        "total_dated_sales": total_sales,
        "distinct_dates": distinct_dates,
        "min_date": row["min_date"],
        "max_date": row["max_date"],
        "reason": f"Ready: {total_sales} dated sales rows spanning {distinct_dates} distinct date(s) ({row['min_date']} to {row['max_date']}).",
    }


def _get_min_supplier_lead_time(product_id: str, db_path: DbPath = None) -> tuple[int, bool]:
    """Return (lead_time_days, from_supplier_data)."""
    row = fetch_one(
        """
        SELECT MIN(delivery_days) AS min_days
        FROM suppliers
        WHERE product_id = ?
          AND delivery_days IS NOT NULL
          AND (available_quantity IS NULL OR available_quantity > 0)
        """,
        (product_id,),
        db_path=db_path,
    )
    if row and row["min_days"] is not None:
        return int(row["min_days"]), True
    return DEFAULT_LEAD_TIME_DAYS, False


def get_daily_sales_series(product_id: str, db_path: DbPath = None) -> pd.DataFrame:
    """Return a complete daily date-indexed sales DataFrame (filling 0 on no-sale days)."""
    bounds = fetch_one(
        "SELECT MIN(date) AS min_d, MAX(date) AS max_d FROM sales WHERE date IS NOT NULL AND TRIM(date) != ''",
        db_path=db_path,
    )
    rows = fetch_all(
        """
        SELECT date, SUM(quantity_sold) AS quantity_sold,
               SUM(CASE WHEN selling_price IS NOT NULL THEN quantity_sold * selling_price ELSE 0 END) AS revenue
        FROM sales
        WHERE product_id = ? AND date IS NOT NULL AND TRIM(date) != ''
        GROUP BY date
        ORDER BY date ASC
        """,
        (product_id,),
        db_path=db_path,
    )

    if not bounds or not bounds["min_d"] or not bounds["max_d"]:
        return pd.DataFrame(columns=["date", "quantity_sold", "revenue"])

    full_dates = pd.date_range(bounds["min_d"], bounds["max_d"], freq="D")
    base = pd.DataFrame({"date": full_dates})
    if rows:
        sales_df = pd.DataFrame(rows)
        sales_df["date"] = pd.to_datetime(sales_df["date"])
        merged = base.merge(sales_df, on="date", how="left")
        merged["quantity_sold"] = merged["quantity_sold"].fillna(0).astype(float)
        merged["revenue"] = merged["revenue"].fillna(0.0).astype(float)
    else:
        merged = base
        merged["quantity_sold"] = 0.0
        merged["revenue"] = 0.0
    return merged


def forecast_product(
    product_id: str,
    horizon_days: int = DEFAULT_FORECAST_HORIZON_DAYS,
    window_days: int = DEFAULT_FORECAST_WINDOW_DAYS,
    method: str = "ema",
    lead_time_days: Optional[int] = None,
    db_path: DbPath = None,
) -> dict:
    """Generate baseline statistical demand forecast and reorder recommendation."""
    product = fetch_one(
        "SELECT * FROM products WHERE product_id = ?",
        (product_id,),
        db_path=db_path,
    )
    if not product:
        raise ValueError(f"Product '{product_id}' not found.")

    readiness = check_forecasting_readiness(db_path=db_path)
    daily_df = get_daily_sales_series(product_id, db_path=db_path)
    total_units_sold = float(daily_df["quantity_sold"].sum()) if not daily_df.empty else 0.0
    history_days = int(len(daily_df)) if not daily_df.empty else 0
    current_stock = int(product["current_stock"])
    stored_rop = int(product["reorder_point"]) if product["reorder_point"] is not None else None
    target_stock = int(product["target_stock"]) if product["target_stock"] is not None else None

    if not readiness["ready"]:
        return {
            "forecast_available": False,
            "limitation_reason": readiness["reason"],
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "category": product["category"] or "Uncategorized",
            "unit": product["unit"] or "unit",
            "current_stock": current_stock,
            "stored_reorder_point": stored_rop,
            "target_stock": target_stock,
            "total_units_sold": 0,
            "history_days": 0,
            "avg_daily_sales": None,
            "sma_daily_demand": None,
            "ema_daily_demand": None,
            "forecast_daily_demand": None,
            "forecast_horizon_days": horizon_days,
            "forecast_total_demand": None,
            "lead_time_days": None,
            "safety_stock": None,
            "calculated_reorder_point": None,
            "days_remaining": 0.0 if current_stock <= 0 else None,
            "stockout_risk": "Critical" if current_stock <= 0 else "Unavailable",
            "reorder_timing": "Demand forecast unavailable (insufficient dated sales history).",
            "method_label": "Unavailable",
            "assumptions": [readiness["reason"]],
            "history_df": pd.DataFrame(columns=["date", "quantity_sold", "revenue"]),
            "forecast_df": pd.DataFrame(columns=["date", "predicted_daily_demand", "projected_stock"]),
        }

    avg_daily_sales = calculate_average_daily_sales(total_units_sold, history_days)
    sma_daily = compute_moving_average(daily_df["quantity_sold"], window=window_days)
    ema_daily = compute_exponential_smoothing(daily_df["quantity_sold"], alpha=EMA_ALPHA)

    method_norm = (method or "ema").lower()
    if method_norm == "sma":
        forecast_daily = sma_daily
        method_label = f"{window_days}-Day Simple Moving Average (SMA)"
    else:
        forecast_daily = ema_daily
        method_label = f"Single Exponential Smoothing (alpha={EMA_ALPHA})"

    if lead_time_days is not None:
        effective_lead_time = int(lead_time_days)
        lt_source = "user-configured"
    else:
        effective_lead_time, from_sup = _get_min_supplier_lead_time(product_id, db_path=db_path)
        lt_source = "uploaded/catalog supplier lead time" if from_sup else f"fallback assumption ({DEFAULT_LEAD_TIME_DAYS} days — no supplier lead time provided)"

    std_dev = float(daily_df["quantity_sold"].std(ddof=0)) if history_days > 1 else 0.0
    safety_stock = round(
        max(forecast_daily * DEFAULT_SAFETY_STOCK_DAYS, 1.28 * std_dev * np.sqrt(max(1, effective_lead_time))),
        1,
    )
    calculated_rop = calculate_reorder_point(forecast_daily, effective_lead_time, safety_stock)

    demand_rate = forecast_daily if forecast_daily > 0 else avg_daily_sales
    days_remaining = calculate_days_remaining(current_stock, demand_rate)
    risk = classify_stockout_risk(current_stock, days_remaining, effective_lead_time)

    threshold_rop = max(stored_rop or 0, calculated_rop)
    if current_stock <= 0:
        reorder_timing = "Reorder immediately (currently out of stock)."
    elif current_stock <= threshold_rop:
        reorder_timing = f"Reorder today (stock is at or below reorder threshold of {threshold_rop} units)."
    elif days_remaining is not None:
        days_until_reorder = max(0, int(np.floor(days_remaining - effective_lead_time - DEFAULT_SAFETY_STOCK_DAYS)))
        if days_until_reorder == 0:
            reorder_timing = "Reorder within 24 hours to avoid stockout during supplier delivery."
        else:
            reorder_timing = f"Plan reorder in approximately {days_until_reorder} day(s)."
    else:
        reorder_timing = "No recent sales velocity detected for this SKU; monitor manually."

    last_date = daily_df["date"].max()
    future_rows = []
    projected_stock = float(current_stock)
    for step in range(1, max(1, horizon_days) + 1):
        fdate = last_date + timedelta(days=step)
        projected_stock = max(0.0, projected_stock - forecast_daily)
        future_rows.append(
            {
                "date": fdate,
                "predicted_daily_demand": round(forecast_daily, 2),
                "projected_stock": round(projected_stock, 1),
            }
        )
    forecast_df = pd.DataFrame(future_rows)

    assumptions = [
        f"Forecast Method: {method_label} (statistical baseline, not deep ML).",
        f"Historical window analyzed: {history_days} days ({int(total_units_sold)} total units sold).",
        f"Supplier lead time: {effective_lead_time} day(s) ({lt_source}).",
        f"Safety stock buffer: {safety_stock:.1f} units (covers demand variability & lead-time uncertainty).",
        "Assumes near-term demand continues recent historical trends without sudden festival/price shocks.",
    ]

    return {
        "forecast_available": True,
        "limitation_reason": None,
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        "category": product["category"] or "Uncategorized",
        "unit": product["unit"] or "unit",
        "current_stock": current_stock,
        "stored_reorder_point": stored_rop,
        "target_stock": target_stock,
        "total_units_sold": int(total_units_sold),
        "history_days": history_days,
        "avg_daily_sales": avg_daily_sales,
        "sma_daily_demand": sma_daily,
        "ema_daily_demand": ema_daily,
        "forecast_daily_demand": forecast_daily,
        "forecast_horizon_days": horizon_days,
        "forecast_total_demand": round(forecast_daily * horizon_days, 1),
        "lead_time_days": effective_lead_time,
        "safety_stock": safety_stock,
        "calculated_reorder_point": calculated_rop,
        "days_remaining": days_remaining,
        "stockout_risk": risk,
        "reorder_timing": reorder_timing,
        "method_label": method_label,
        "assumptions": assumptions,
        "history_df": daily_df,
        "forecast_df": forecast_df,
    }


def forecast_all_products(
    horizon_days: int = DEFAULT_FORECAST_HORIZON_DAYS,
    window_days: int = DEFAULT_FORECAST_WINDOW_DAYS,
    method: str = "ema",
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Run baseline demand forecasting across all products."""
    readiness = check_forecasting_readiness(db_path=db_path)
    if not readiness["ready"]:
        return pd.DataFrame(
            columns=[
                "product_id", "product_name", "category", "current_stock",
                "avg_daily_sales", "forecast_daily_demand", "forecast_horizon_demand",
                "lead_time_days", "calculated_reorder_point", "stored_reorder_point",
                "days_remaining", "stockout_risk", "reorder_timing",
            ]
        )

    products = fetch_all("SELECT product_id FROM products ORDER BY product_id ASC", db_path=db_path)
    records = []
    for p in products:
        res = forecast_product(
            product_id=p["product_id"],
            horizon_days=horizon_days,
            window_days=window_days,
            method=method,
            db_path=db_path,
        )
        records.append(
            {
                "product_id": res["product_id"],
                "product_name": res["product_name"],
                "category": res["category"],
                "current_stock": res["current_stock"],
                "avg_daily_sales": res["avg_daily_sales"],
                "forecast_daily_demand": res["forecast_daily_demand"],
                "forecast_horizon_demand": res["forecast_total_demand"],
                "lead_time_days": res["lead_time_days"],
                "calculated_reorder_point": res["calculated_reorder_point"],
                "stored_reorder_point": res["stored_reorder_point"],
                "days_remaining": res["days_remaining"],
                "stockout_risk": res["stockout_risk"],
                "reorder_timing": res["reorder_timing"],
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return df

    risk_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Unavailable": 4}
    df["_risk_rank"] = df["stockout_risk"].map(risk_order).fillna(5)
    df["_days_sort"] = df["days_remaining"].fillna(9999.0)
    df = df.sort_values(["_risk_rank", "_days_sort"]).drop(columns=["_risk_rank", "_days_sort"]).reset_index(drop=True)
    return df
