"""Supplier catalog management and transparent multi-criteria supplier ranking."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from config import SUPPLIER_SCORE_WEIGHTS
from database.db import execute_write, fetch_all, fetch_one
from utils.validators import ValidationError, validate_supplier_payload

DbPath = Optional[Union[str, Path]]


def get_all_suppliers(
    product_id: Optional[str] = None,
    supplier_name: Optional[str] = None,
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Return supplier-product offers joined with product names."""
    query = """
        SELECT s.id, s.supplier_id, s.supplier_name, s.product_id,
               p.product_name, p.category, s.unit_price, s.delivery_days,
               s.available_quantity, s.reliability_score, s.min_order_qty
        FROM suppliers s
        JOIN products p ON s.product_id = p.product_id
        WHERE 1=1
    """
    params: list = []
    if product_id:
        query += " AND s.product_id = ?"
        params.append(product_id)
    if supplier_name and supplier_name != "All":
        query += " AND s.supplier_name = ?"
        params.append(supplier_name)
    query += " ORDER BY p.product_name ASC, CASE WHEN s.unit_price IS NULL THEN 1 ELSE 0 END, s.unit_price ASC"

    rows = fetch_all(query, params, db_path=db_path)
    if not rows:
        return pd.DataFrame(
            columns=[
                "id", "supplier_id", "supplier_name", "product_id", "product_name",
                "category", "unit_price", "delivery_days", "available_quantity",
                "reliability_score", "min_order_qty",
            ]
        )
    df = pd.DataFrame(rows)
    for _num_col in ("unit_price", "delivery_days", "available_quantity", "reliability_score", "min_order_qty"):
        df[_num_col] = pd.to_numeric(df[_num_col], errors="coerce")
    return df


def get_unique_supplier_names(db_path: DbPath = None) -> list[str]:
    """Return distinct supplier names sorted alphabetically."""
    rows = fetch_all(
        "SELECT DISTINCT supplier_name FROM suppliers ORDER BY supplier_name ASC",
        db_path=db_path,
    )
    return [r["supplier_name"] for r in rows]


def get_supplier_overview(db_path: DbPath = None) -> pd.DataFrame:
    """Aggregate supplier-level performance summary across all supplied products."""
    df = get_all_suppliers(db_path=db_path)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "supplier_id", "supplier_name", "products_supplied",
                "avg_unit_price", "avg_delivery_days", "avg_reliability", "total_available_qty",
            ]
        )

    agg = (
        df.groupby(["supplier_id", "supplier_name"], as_index=False)
        .agg(
            products_supplied=("product_id", "nunique"),
            avg_unit_price=("unit_price", lambda s: round(float(s.dropna().mean()), 2) if s.notna().any() else np.nan),
            avg_delivery_days=("delivery_days", lambda s: round(float(s.dropna().mean()), 1) if s.notna().any() else np.nan),
            avg_reliability=("reliability_score", lambda s: round(float(s.dropna().mean()), 1) if s.notna().any() else np.nan),
            total_available_qty=("available_quantity", lambda s: int(s.dropna().sum()) if s.notna().any() else np.nan),
        )
        .sort_values("products_supplied", ascending=False)
        .reset_index(drop=True)
    )
    return agg


def rank_suppliers_for_product(
    product_id: str,
    required_qty: int = 1,
    weights: Optional[dict] = None,
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Rank suppliers for a given product using a transparent weighted scoring model.

    If an uploaded dataset only provides a subset of supplier metrics (for example,
    only unit_price and delivery_days without reliability_score), weights are
    transparently re-normalized across ONLY the available metrics — never fabricating
    missing scores!
    """
    df = get_all_suppliers(product_id=product_id, db_path=db_path)
    if df.empty:
        return df

    base_w = weights or SUPPLIER_SCORE_WEIGHTS
    req = max(1, int(required_qty))

    has_price = bool(df["unit_price"].notna().any())
    has_delivery = bool(df["delivery_days"].notna().any())
    has_reliability = bool(df["reliability_score"].notna().any())
    has_avail = bool(df["available_quantity"].notna().any())

    active_weights: dict[str, float] = {}
    if has_price:
        active_weights["price"] = float(base_w["price"])
    if has_delivery:
        active_weights["delivery"] = float(base_w["delivery"])
    if has_reliability:
        active_weights["reliability"] = float(base_w["reliability"])
    if has_avail:
        active_weights["availability"] = float(base_w["availability"])

    weight_sum = sum(active_weights.values())
    if weight_sum > 0:
        norm_w = {k: v / weight_sum for k, v in active_weights.items()}
    else:
        norm_w = {}

    # 1. Price score (0-100): lowest price gets 100
    if has_price:
        valid_p = df["unit_price"].dropna()
        min_p, max_p = float(valid_p.min()), float(valid_p.max())
        if max_p == min_p:
            df["price_score"] = np.where(df["unit_price"].notna(), 100.0, np.nan)
        else:
            df["price_score"] = np.where(
                df["unit_price"].notna(),
                ((max_p - df["unit_price"]) / (max_p - min_p) * 70.0 + 30.0).round(2),
                np.nan,
            )
    else:
        df["price_score"] = np.nan

    # 2. Delivery score (0-100): fastest delivery gets 100
    if has_delivery:
        valid_d = df["delivery_days"].dropna()
        min_d, max_d = float(valid_d.min()), float(valid_d.max())
        if max_d == min_d:
            df["delivery_score"] = np.where(df["delivery_days"].notna(), 100.0, np.nan)
        else:
            df["delivery_score"] = np.where(
                df["delivery_days"].notna(),
                ((max_d - df["delivery_days"]) / (max_d - min_d) * 70.0 + 30.0).round(2),
                np.nan,
            )
    else:
        df["delivery_score"] = np.nan

    # 3. Reliability score (0-100)
    if has_reliability:
        df["reliability_norm"] = df["reliability_score"].clip(0, 100).round(2)
    else:
        df["reliability_norm"] = np.nan

    # 4. Availability score (0-100)
    if has_avail:
        df["availability_score"] = df["available_quantity"].apply(
            lambda q: np.nan if pd.isna(q) else (0.0 if q <= 0 else (100.0 if q >= req else round((q / req) * 100.0, 2)))
        )
    else:
        df["availability_score"] = np.nan

    # Compute row-level weighted score strictly across available non-null metrics
    def _compute_row_score(row: pd.Series) -> float:
        if not norm_w:
            return np.nan
        num = 0.0
        den = 0.0
        col_map = {
            "price": "price_score",
            "delivery": "delivery_score",
            "reliability": "reliability_norm",
            "availability": "availability_score",
        }
        for crit, w_val in norm_w.items():
            val = row[col_map[crit]]
            if pd.notna(val):
                num += float(val) * w_val
                den += w_val
        if den <= 0:
            return np.nan
        score = num / den
        # Penalize suppliers explicitly known to have 0 available quantity
        if pd.notna(row["available_quantity"]) and float(row["available_quantity"]) <= 0:
            score *= 0.25
        return round(score, 2)

    df["total_score"] = df.apply(_compute_row_score, axis=1)
    df["scored_criteria"] = ", ".join(norm_w.keys()) if norm_w else "None (supplier names only)"

    df["_score_sort"] = df["total_score"].fillna(-1.0)
    df["_price_sort"] = df["unit_price"].fillna(1e12)
    df["_days_sort"] = df["delivery_days"].fillna(1e9)
    df = (
        df.sort_values(["_score_sort", "_price_sort", "_days_sort"], ascending=[False, True, True])
        .drop(columns=["_score_sort", "_price_sort", "_days_sort"])
        .reset_index(drop=True)
    )
    df["rank"] = df.index + 1

    cheapest_price = float(df["unit_price"].dropna().min()) if has_price else None
    fastest_days = int(df["delivery_days"].dropna().min()) if has_delivery else None
    top_rel = float(df["reliability_score"].dropna().max()) if has_reliability else None

    reasons = []
    for _, r in df.iterrows():
        highlights = []
        if pd.notna(r["available_quantity"]) and float(r["available_quantity"]) <= 0:
            highlights.append("Currently out of stock")
        else:
            if cheapest_price is not None and pd.notna(r["unit_price"]) and float(r["unit_price"]) == cheapest_price:
                highlights.append(f"lowest unit price (₹{float(r['unit_price']):.2f})")
            if fastest_days is not None and pd.notna(r["delivery_days"]) and int(r["delivery_days"]) == fastest_days:
                highlights.append(f"fastest delivery ({int(r['delivery_days'])} day(s))")
            if top_rel is not None and pd.notna(r["reliability_score"]) and float(r["reliability_score"]) == top_rel:
                highlights.append(f"highest reliability ({float(r['reliability_score']):.1f}%)")
            if pd.notna(r["available_quantity"]):
                if int(r["available_quantity"]) >= req:
                    highlights.append(f"sufficient stock ({int(r['available_quantity'])} units)")
                else:
                    highlights.append(f"partial stock only ({int(r['available_quantity'])} units)")
        if not highlights:
            if not norm_w:
                highlights.append("listed supplier (no price, lead-time, or reliability metrics uploaded)")
            else:
                highlights.append("balanced across available supplier metrics")
        prefix = "Recommended (#1): " if r["rank"] == 1 else f"Rank #{r['rank']}: "
        reasons.append(prefix + "; ".join(highlights) + ".")

    df["recommendation_reason"] = reasons
    return df


def add_or_update_supplier_offer(
    supplier_id: str,
    supplier_name: str,
    product_id: str,
    unit_price: float,
    delivery_days: int,
    available_quantity: int,
    reliability_score: float,
    min_order_qty: int = 1,
    db_path: DbPath = None,
) -> dict:
    """Add a new supplier-product offer or update it if (supplier_id, product_id) exists."""
    payload = validate_supplier_payload(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        product_id=product_id,
        unit_price=unit_price,
        delivery_days=delivery_days,
        available_quantity=available_quantity,
        reliability_score=reliability_score,
        min_order_qty=min_order_qty,
    )
    prod = fetch_one(
        "SELECT product_id FROM products WHERE product_id = ?",
        (payload["product_id"],),
        db_path=db_path,
    )
    if not prod:
        raise ValidationError(f"Product ID '{payload['product_id']}' does not exist.")

    existing = fetch_one(
        "SELECT id FROM suppliers WHERE supplier_id = ? AND product_id = ?",
        (payload["supplier_id"], payload["product_id"]),
        db_path=db_path,
    )
    if existing:
        execute_write(
            """
            UPDATE suppliers
            SET supplier_name = ?, unit_price = ?, delivery_days = ?,
                available_quantity = ?, reliability_score = ?, min_order_qty = ?
            WHERE id = ?
            """,
            (
                payload["supplier_name"],
                payload["unit_price"],
                payload["delivery_days"],
                payload["available_quantity"],
                payload["reliability_score"],
                payload["min_order_qty"],
                existing["id"],
            ),
            db_path=db_path,
        )
        return {"action": "updated", **payload}

    execute_write(
        """
        INSERT INTO suppliers (
            supplier_id, supplier_name, product_id, unit_price,
            delivery_days, available_quantity, reliability_score, min_order_qty
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["supplier_id"],
            payload["supplier_name"],
            payload["product_id"],
            payload["unit_price"],
            payload["delivery_days"],
            payload["available_quantity"],
            payload["reliability_score"],
            payload["min_order_qty"],
        ),
        db_path=db_path,
    )
    return {"action": "created", **payload}


def update_supplier_offer_by_id(
    record_id: int,
    supplier_name: str,
    unit_price: float,
    delivery_days: int,
    available_quantity: int,
    reliability_score: float,
    min_order_qty: int = 1,
    db_path: DbPath = None,
) -> dict:
    """Update an existing supplier offer row by primary key `id`."""
    existing = fetch_one("SELECT * FROM suppliers WHERE id = ?", (record_id,), db_path=db_path)
    if not existing:
        raise ValidationError(f"Supplier record #{record_id} not found.")

    payload = validate_supplier_payload(
        supplier_id=existing["supplier_id"],
        supplier_name=supplier_name,
        product_id=existing["product_id"],
        unit_price=unit_price,
        delivery_days=delivery_days,
        available_quantity=available_quantity,
        reliability_score=reliability_score,
        min_order_qty=min_order_qty,
    )
    execute_write(
        """
        UPDATE suppliers
        SET supplier_name = ?, unit_price = ?, delivery_days = ?,
            available_quantity = ?, reliability_score = ?, min_order_qty = ?
        WHERE id = ?
        """,
        (
            payload["supplier_name"],
            payload["unit_price"],
            payload["delivery_days"],
            payload["available_quantity"],
            payload["reliability_score"],
            payload["min_order_qty"],
            record_id,
        ),
        db_path=db_path,
    )
    return {"id": record_id, **payload}
