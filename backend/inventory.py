"""Core inventory management business logic and database operations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from database.db import execute_write, fetch_all, fetch_one, get_connection
from utils.helpers import classify_stock_status, today_iso, utc_now_iso
from utils.validators import (
    ValidationError,
    validate_product_payload,
    validate_stock_movement,
)

DbPath = Optional[Union[str, Path]]


def get_all_products(
    db_path: DbPath = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch all products with computed stock status, overstock flag, and valuations."""
    rows = fetch_all(
        """
        SELECT product_id, product_name, category, current_stock, unit,
               selling_price, cost_price, reorder_point, target_stock, updated_at
        FROM products
        ORDER BY product_id ASC
        """,
        db_path=db_path,
    )
    if not rows:
        return pd.DataFrame(
            columns=[
                "product_id", "product_name", "category", "current_stock", "unit",
                "selling_price", "cost_price", "reorder_point", "target_stock",
                "updated_at", "stock_status", "is_overstock", "excess_units",
                "inventory_value_cost", "inventory_value_retail", "unit_margin",
            ]
        )

    df = pd.DataFrame(rows)
    df["category"] = df["category"].fillna("Uncategorized")
    df["unit"] = df["unit"].fillna("unit")
    for _num_col in ("current_stock", "selling_price", "cost_price", "reorder_point", "target_stock"):
        df[_num_col] = pd.to_numeric(df[_num_col], errors="coerce")
    df["stock_status"] = df.apply(
        lambda r: classify_stock_status(r["current_stock"], r["reorder_point"]),
        axis=1,
    )

    # Overstock detection: strictly when target_stock is provided and current_stock > target_stock
    df["is_overstock"] = df["target_stock"].notna() & (df["current_stock"] > df["target_stock"])
    df["excess_units"] = np.where(
        df["is_overstock"],
        (df["current_stock"] - df["target_stock"]).fillna(0).astype(int),
        0,
    )

    # Valuations remain NaN when price columns are absent (never fabricate prices!)
    df["inventory_value_cost"] = np.where(
        df["cost_price"].notna(),
        (df["current_stock"] * df["cost_price"]).round(2),
        np.nan,
    )
    df["inventory_value_retail"] = np.where(
        df["selling_price"].notna(),
        (df["current_stock"] * df["selling_price"]).round(2),
        np.nan,
    )
    df["unit_margin"] = np.where(
        df["selling_price"].notna() & df["cost_price"].notna(),
        (df["selling_price"] - df["cost_price"]).round(2),
        np.nan,
    )

    if search and search.strip():
        q = search.strip().lower()
        mask = (
            df["product_name"].astype(str).str.lower().str.contains(q, regex=False)
            | df["product_id"].astype(str).str.lower().str.contains(q, regex=False)
            | df["category"].astype(str).str.lower().str.contains(q, regex=False)
        )
        df = df[mask]

    if category and category != "All":
        df = df[df["category"] == category]

    if status_filter and status_filter != "All":
        if status_filter == "Overstock":
            df = df[df["is_overstock"]]
        else:
            df = df[df["stock_status"] == status_filter]

    return df.reset_index(drop=True)


def get_product_by_id(product_id: str, db_path: DbPath = None) -> Optional[dict]:
    """Fetch a single product enriched with valuation and stock status."""
    row = fetch_one(
        "SELECT * FROM products WHERE product_id = ?",
        (product_id,),
        db_path=db_path,
    )
    if not row:
        return None
    row["stock_status"] = classify_stock_status(row["current_stock"], row["reorder_point"])
    row["is_overstock"] = bool(
        row["target_stock"] is not None and row["current_stock"] > row["target_stock"]
    )
    row["inventory_value_cost"] = (
        round(row["current_stock"] * row["cost_price"], 2)
        if row["cost_price"] is not None
        else None
    )
    row["inventory_value_retail"] = (
        round(row["current_stock"] * row["selling_price"], 2)
        if row["selling_price"] is not None
        else None
    )
    return row


def get_categories(db_path: DbPath = None) -> list[str]:
    """Return sorted list of distinct product categories."""
    rows = fetch_all("SELECT DISTINCT category FROM products ORDER BY category ASC", db_path=db_path)
    return [r["category"] for r in rows if r["category"]]


def get_inventory_summary(db_path: DbPath = None) -> dict:
    """Compute high-level inventory KPIs, overstock count, and category breakdown."""
    df = get_all_products(db_path=db_path)
    if df.empty:
        return {
            "total_products": 0,
            "total_units": 0,
            "has_cost_data": False,
            "has_retail_data": False,
            "has_reorder_points": False,
            "has_target_stock": False,
            "total_cost_value": None,
            "total_retail_value": None,
            "low_stock_count": 0,
            "out_of_stock_count": 0,
            "healthy_count": 0,
            "overstock_count": 0,
            "category_breakdown": pd.DataFrame(),
        }

    has_cost = bool(df["cost_price"].notna().any())
    has_retail = bool(df["selling_price"].notna().any())
    has_rop = bool(df["reorder_point"].notna().any())
    has_target = bool(df["target_stock"].notna().any())

    category_breakdown = (
        df.groupby("category", as_index=False)
        .agg(
            products=("product_id", "count"),
            total_units=("current_stock", "sum"),
            cost_value=("inventory_value_cost", lambda s: round(float(s.dropna().sum()), 2) if s.notna().any() else np.nan),
            retail_value=("inventory_value_retail", lambda s: round(float(s.dropna().sum()), 2) if s.notna().any() else np.nan),
        )
        .sort_values("total_units", ascending=False)
        .reset_index(drop=True)
    )

    return {
        "total_products": int(len(df)),
        "total_units": int(df["current_stock"].sum()),
        "has_cost_data": has_cost,
        "has_retail_data": has_retail,
        "has_reorder_points": has_rop,
        "has_target_stock": has_target,
        "total_cost_value": round(float(df["inventory_value_cost"].dropna().sum()), 2) if has_cost else None,
        "total_retail_value": round(float(df["inventory_value_retail"].dropna().sum()), 2) if has_retail else None,
        "low_stock_count": int((df["stock_status"] == "Low Stock").sum()),
        "out_of_stock_count": int((df["stock_status"] == "Out of Stock").sum()),
        "healthy_count": int((df["stock_status"] == "Healthy").sum()),
        "overstock_count": int(df["is_overstock"].sum()),
        "category_breakdown": category_breakdown,
    }


def get_low_stock_products(
    db_path: DbPath = None,
    include_out_of_stock: bool = True,
) -> pd.DataFrame:
    """Return products at or below reorder point (plus out-of-stock if requested)."""
    df = get_all_products(db_path=db_path)
    if df.empty:
        return df
    statuses = ["Out of Stock", "Low Stock"] if include_out_of_stock else ["Low Stock"]
    return (
        df[df["stock_status"].isin(statuses)]
        .sort_values(["current_stock", "product_name"], ascending=[True, True])
        .reset_index(drop=True)
    )


def get_out_of_stock_products(db_path: DbPath = None) -> pd.DataFrame:
    """Return products with zero current stock."""
    return get_all_products(db_path=db_path, status_filter="Out of Stock")


def get_overstock_products(db_path: DbPath = None) -> pd.DataFrame:
    """Return products whose current stock exceeds their target/max stock."""
    df = get_all_products(db_path=db_path)
    if df.empty:
        return df
    return (
        df[df["is_overstock"]]
        .sort_values(["excess_units", "product_name"], ascending=[False, True])
        .reset_index(drop=True)
    )


def get_sales_analytics(db_path: DbPath = None) -> dict:
    """Compute sales analytics strictly from actual recorded sales rows."""
    rows = fetch_all(
        """
        SELECT s.sale_id, s.date, s.product_id, p.product_name, p.category,
               s.quantity_sold, s.selling_price
        FROM sales s
        JOIN products p ON s.product_id = p.product_id
        ORDER BY s.date ASC
        """,
        db_path=db_path,
    )
    if not rows:
        return {
            "available": False,
            "total_records": 0,
            "total_units_sold": 0,
            "has_revenue": False,
            "total_revenue": None,
            "has_dates": False,
            "distinct_dates": 0,
            "top_products": pd.DataFrame(),
            "daily_trend": pd.DataFrame(),
            "category_sales": pd.DataFrame(),
        }

    df = pd.DataFrame(rows)
    df["category"] = df["category"].fillna("Uncategorized")
    df["quantity_sold"] = pd.to_numeric(df["quantity_sold"], errors="coerce").fillna(0).astype(int)
    df["selling_price"] = pd.to_numeric(df["selling_price"], errors="coerce")
    has_rev = bool(df["selling_price"].notna().any())
    df["revenue"] = np.where(
        df["selling_price"].notna(),
        (df["quantity_sold"] * df["selling_price"]).round(2),
        np.nan,
    )

    top_products = (
        df.groupby(["product_id", "product_name", "category"], as_index=False)
        .agg(
            units_sold=("quantity_sold", "sum"),
            transactions=("sale_id", "count"),
            revenue=("revenue", lambda s: round(float(s.dropna().sum()), 2) if s.notna().any() else np.nan),
        )
        .sort_values("units_sold", ascending=False)
        .reset_index(drop=True)
    )

    category_sales = (
        df.groupby("category", as_index=False)
        .agg(
            units_sold=("quantity_sold", "sum"),
            revenue=("revenue", lambda s: round(float(s.dropna().sum()), 2) if s.notna().any() else np.nan),
        )
        .sort_values("units_sold", ascending=False)
        .reset_index(drop=True)
    )

    dated = df[df["date"].notna()].copy()
    has_dates = not dated.empty
    distinct_dates = int(dated["date"].nunique()) if has_dates else 0
    if has_dates:
        daily_trend = (
            dated.groupby("date", as_index=False)
            .agg(
                units_sold=("quantity_sold", "sum"),
                revenue=("revenue", lambda s: round(float(s.dropna().sum()), 2) if s.notna().any() else np.nan),
            )
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        daily_trend = pd.DataFrame(columns=["date", "units_sold", "revenue"])

    return {
        "available": True,
        "total_records": int(len(df)),
        "total_units_sold": int(df["quantity_sold"].sum()),
        "has_revenue": has_rev,
        "total_revenue": round(float(df["revenue"].dropna().sum()), 2) if has_rev else None,
        "has_dates": has_dates,
        "distinct_dates": distinct_dates,
        "top_products": top_products,
        "daily_trend": daily_trend,
        "category_sales": category_sales,
    }


def generate_next_product_id(db_path: DbPath = None) -> str:
    """Generate the next sequential product ID (e.g., P056)."""
    rows = fetch_all("SELECT product_id FROM products", db_path=db_path)
    max_num = 0
    for r in rows:
        pid = r["product_id"]
        if pid.startswith("P") and pid[1:].isdigit():
            max_num = max(max_num, int(pid[1:]))
    return f"P{max_num + 1:03d}"


def add_product(
    product_id: str,
    product_name: str,
    category: str,
    current_stock: int,
    unit: str,
    selling_price: float,
    cost_price: float,
    reorder_point: int,
    target_stock: int,
    db_path: DbPath = None,
) -> dict:
    """Validate and insert a new product into inventory."""
    payload = validate_product_payload(
        product_id=product_id,
        product_name=product_name,
        category=category,
        current_stock=current_stock,
        unit=unit,
        selling_price=selling_price,
        cost_price=cost_price,
        reorder_point=reorder_point,
        target_stock=target_stock,
    )
    existing = get_product_by_id(payload["product_id"], db_path=db_path)
    if existing:
        raise ValidationError(f"Product ID '{payload['product_id']}' already exists.")

    now = utc_now_iso()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO products (
                product_id, product_name, category, current_stock, unit,
                selling_price, cost_price, reorder_point, target_stock, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["product_id"],
                payload["product_name"],
                payload["category"],
                payload["current_stock"],
                payload["unit"],
                payload["selling_price"],
                payload["cost_price"],
                payload["reorder_point"],
                payload["target_stock"],
                now,
            ),
        )
        if payload["current_stock"] > 0:
            mov_id = f"M-{uuid.uuid4().hex[:8].upper()}"
            conn.execute(
                """
                INSERT INTO stock_movements (
                    movement_id, date, product_id, movement_type, quantity, remarks
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    mov_id,
                    today_iso(),
                    payload["product_id"],
                    "RESTOCK",
                    payload["current_stock"],
                    "Opening stock on product creation",
                ),
            )
    return get_product_by_id(payload["product_id"], db_path=db_path)


def update_product(
    product_id: str,
    product_name: str,
    category: str,
    unit: str,
    selling_price: float,
    cost_price: float,
    reorder_point: int,
    target_stock: int,
    db_path: DbPath = None,
) -> dict:
    """Update non-stock metadata for an existing product."""
    existing = get_product_by_id(product_id, db_path=db_path)
    if not existing:
        raise ValidationError(f"Product '{product_id}' not found.")

    payload = validate_product_payload(
        product_id=product_id,
        product_name=product_name,
        category=category,
        current_stock=existing["current_stock"],
        unit=unit,
        selling_price=selling_price,
        cost_price=cost_price,
        reorder_point=reorder_point,
        target_stock=target_stock,
    )

    execute_write(
        """
        UPDATE products
        SET product_name = ?, category = ?, unit = ?, selling_price = ?,
            cost_price = ?, reorder_point = ?, target_stock = ?, updated_at = ?
        WHERE product_id = ?
        """,
        (
            payload["product_name"],
            payload["category"],
            payload["unit"],
            payload["selling_price"],
            payload["cost_price"],
            payload["reorder_point"],
            payload["target_stock"],
            utc_now_iso(),
            payload["product_id"],
        ),
        db_path=db_path,
    )
    return get_product_by_id(payload["product_id"], db_path=db_path)


def update_stock(
    product_id: str,
    movement_type: str,
    quantity: int,
    remarks: str = "",
    movement_date: Optional[str] = None,
    db_path: DbPath = None,
) -> dict:
    """Apply a validated stock movement, update current stock, and log movement."""
    product = get_product_by_id(product_id, db_path=db_path)
    if not product:
        raise ValidationError(f"Product '{product_id}' not found.")

    validated = validate_stock_movement(
        current_stock=int(product["current_stock"]),
        movement_type=movement_type,
        quantity=quantity,
        remarks=remarks,
    )
    mdate = movement_date or today_iso()
    mov_id = f"M-{uuid.uuid4().hex[:8].upper()}"

    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE products SET current_stock = ?, updated_at = ? WHERE product_id = ?",
            (validated["new_stock"], utc_now_iso(), product_id),
        )
        conn.execute(
            """
            INSERT INTO stock_movements (
                movement_id, date, product_id, movement_type, quantity, remarks
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mov_id,
                mdate,
                product_id,
                validated["movement_type"],
                validated["quantity"],
                validated["remarks"],
            ),
        )
        if validated["movement_type"] == "SALE":
            sale_id = f"S-{uuid.uuid4().hex[:8].upper()}"
            sp = float(product["selling_price"]) if product["selling_price"] is not None else None
            conn.execute(
                """
                INSERT INTO sales (sale_id, date, product_id, quantity_sold, selling_price)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sale_id,
                    mdate,
                    product_id,
                    abs(validated["quantity"]),
                    sp,
                ),
            )

    return {
        "movement_id": mov_id,
        "product_id": product_id,
        "product_name": product["product_name"],
        "previous_stock": int(product["current_stock"]),
        "delta": validated["quantity"],
        "new_stock": validated["new_stock"],
        "movement_type": validated["movement_type"],
        "remarks": validated["remarks"],
        "date": mdate,
    }


def get_stock_movements(
    product_id: Optional[str] = None,
    limit: int = 200,
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Return stock movement history joined with product names."""
    if product_id:
        rows = fetch_all(
            """
            SELECT sm.movement_id, sm.date, sm.product_id, p.product_name,
                   sm.movement_type, sm.quantity, sm.remarks
            FROM stock_movements sm
            JOIN products p ON sm.product_id = p.product_id
            WHERE sm.product_id = ?
            ORDER BY sm.date DESC, sm.movement_id DESC
            LIMIT ?
            """,
            (product_id, limit),
            db_path=db_path,
        )
    else:
        rows = fetch_all(
            """
            SELECT sm.movement_id, sm.date, sm.product_id, p.product_name,
                   sm.movement_type, sm.quantity, sm.remarks
            FROM stock_movements sm
            JOIN products p ON sm.product_id = p.product_id
            ORDER BY sm.date DESC, sm.movement_id DESC
            LIMIT ?
            """,
            (limit,),
            db_path=db_path,
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["movement_id", "date", "product_id", "product_name", "movement_type", "quantity", "remarks"]
    )
