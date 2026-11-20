"""Procurement recommendation engine, human-approval workflow, and audit logging.

Policy: Stock Sentry NEVER executes automatic real-world purchases.
Every generated purchase order enters 'Pending Approval' and requires human action.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from backend.forecasting import forecast_all_products
from backend.supplier import rank_supplier_dataframe
from config import DEFAULT_BUDGET_LIMIT, MAX_STOCK_CAPACITY_MULTIPLIER
from database.db import fetch_all, fetch_one, get_connection
from utils.helpers import format_inr, utc_now_iso
from utils.validators import ValidationError, validate_purchase_order_update

DbPath = Optional[Union[str, Path]]


def calculate_suggested_order_quantity(
    target_stock: int,
    current_stock: int,
    incoming_stock: int = 0,
    min_order_qty: int = 1,
    available_quantity: Optional[int] = None,
    max_capacity: Optional[int] = None,
) -> tuple[int, list[str]]:
    """Calculate validated order quantity using core formula:

        Suggested Order Quantity = Target Stock - Current Stock - Incoming Stock

    Never returns a negative number. Applies supplier MOQ, availability, and capacity checks.
    """
    notes: list[str] = []
    raw_qty = int(target_stock) - int(current_stock) - int(incoming_stock)
    if raw_qty <= 0:
        return 0, ["Current + incoming stock already meets or exceeds target stock."]

    qty = raw_qty

    if min_order_qty and min_order_qty > 1 and qty < min_order_qty:
        qty = int(min_order_qty)
        notes.append(f"Adjusted up from {raw_qty} to supplier MOQ ({min_order_qty} units).")

    effective_cap = (
        int(max_capacity)
        if max_capacity is not None
        else int(round(max(target_stock, 1) * MAX_STOCK_CAPACITY_MULTIPLIER))
    )
    max_addable = max(0, effective_cap - int(current_stock) - int(incoming_stock))
    if qty > max_addable:
        qty = max_addable
        notes.append(f"Capped at {qty} units to respect store capacity limit ({effective_cap} units).")

    if available_quantity is not None:
        if available_quantity <= 0:
            return 0, ["Supplier currently has 0 available quantity."]
        if qty > available_quantity:
            qty = int(available_quantity)
            notes.append(f"Limited to {qty} units based on supplier availability.")

    return max(0, int(qty)), notes


def get_incoming_stock_by_product(db_path: DbPath = None) -> dict[str, int]:
    """Return approved incoming stock quantities per product_id."""
    rows = fetch_all(
        """
        SELECT product_id, SUM(quantity) AS incoming_qty
        FROM purchase_orders
        WHERE status = 'Approved'
        GROUP BY product_id
        """,
        db_path=db_path,
    )
    return {r["product_id"]: int(r["incoming_qty"]) for r in rows}


def get_pending_po_map(db_path: DbPath = None) -> dict[str, int]:
    """Return pending-approval PO quantities per product_id."""
    rows = fetch_all(
        """
        SELECT product_id, SUM(quantity) AS pending_qty
        FROM purchase_orders
        WHERE status IN ('Pending Approval', 'Modified')
        GROUP BY product_id
        """,
        db_path=db_path,
    )
    return {r["product_id"]: int(r["pending_qty"]) for r in rows}


def generate_purchase_recommendations(
    budget_limit: Optional[float] = DEFAULT_BUDGET_LIMIT,
    horizon_days: int = 7,
    db_path: DbPath = None,
    precomputed_forecast_df: Optional[pd.DataFrame] = None,
) -> list[dict]:
    """Generate prioritized purchase-order recommendations with budget and supplier checks.

    Batches products, POs, and supplier offers in a single SQLite connection and reuses
    an optional precomputed forecast DataFrame to avoid redundant queries.
    """
    with get_connection(db_path) as conn:
        products = [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY product_id ASC").fetchall()]
        if not products:
            return []

        po_rows = conn.execute(
            """
            SELECT product_id, status, SUM(quantity) AS qty
            FROM purchase_orders
            WHERE status IN ('Approved', 'Pending Approval', 'Modified')
            GROUP BY product_id, status
            """
        ).fetchall()

        sup_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT s.id, s.supplier_id, s.supplier_name, s.product_id,
                       p.product_name, p.category, s.unit_price, s.delivery_days,
                       s.available_quantity, s.reliability_score, s.min_order_qty
                FROM suppliers s
                JOIN products p ON s.product_id = p.product_id
                """
            ).fetchall()
        ]

    incoming_map: dict[str, int] = {}
    pending_map: dict[str, int] = {}
    for r in po_rows:
        if r["status"] == "Approved":
            incoming_map[r["product_id"]] = incoming_map.get(r["product_id"], 0) + int(r["qty"])
        else:
            pending_map[r["product_id"]] = pending_map.get(r["product_id"], 0) + int(r["qty"])

    sup_groups: dict[str, pd.DataFrame] = {}
    if sup_rows:
        all_sup_df = pd.DataFrame(sup_rows)
        for _num_col in ("unit_price", "delivery_days", "available_quantity", "reliability_score", "min_order_qty"):
            all_sup_df[_num_col] = pd.to_numeric(all_sup_df[_num_col], errors="coerce")
        for pid_key, grp_df in all_sup_df.groupby("product_id", sort=False):
            sup_groups[str(pid_key)] = grp_df

    forecast_df = (
        precomputed_forecast_df
        if precomputed_forecast_df is not None
        else forecast_all_products(horizon_days=horizon_days, db_path=db_path)
    )
    fc_map: dict[str, dict] = {}
    if forecast_df is not None and not forecast_df.empty:
        for rec_dict in forecast_df.to_dict(orient="records"):
            fc_map[rec_dict["product_id"]] = rec_dict

    # Sort products so most urgent (out-of-stock / critical forecast risk) appear first
    def _sort_key(p: dict) -> tuple:
        fc = fc_map.get(p["product_id"])
        risk_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(fc["stockout_risk"], 4) if fc else (0 if p["current_stock"] <= 0 else 3)
        days_rem = fc["days_remaining"] if (fc and pd.notna(fc["days_remaining"])) else (0.0 if p["current_stock"] <= 0 else 9999.0)
        return (risk_rank, days_rem, int(p["current_stock"]), p["product_name"])

    products_sorted = sorted(products, key=_sort_key)

    recommendations: list[dict] = []
    cumulative_cost = 0.0

    for prod in products_sorted:
        pid = prod["product_id"]
        fc = fc_map.get(pid)

        current_stock = int(prod["current_stock"])
        stored_rop = int(prod["reorder_point"]) if prod["reorder_point"] is not None else None
        stored_target = int(prod["target_stock"]) if prod["target_stock"] is not None else None
        calc_rop = int(fc["calculated_reorder_point"]) if (fc and pd.notna(fc["calculated_reorder_point"])) else None
        days_rem = fc["days_remaining"] if (fc and pd.notna(fc["days_remaining"])) else (0.0 if current_stock <= 0 else None)
        lead_time = int(fc["lead_time_days"]) if (fc and pd.notna(fc["lead_time_days"])) else None
        fc_daily = float(fc["forecast_daily_demand"]) if (fc and pd.notna(fc["forecast_daily_demand"])) else None
        stockout_risk = fc["stockout_risk"] if fc else ("Critical" if current_stock <= 0 else ("High" if (stored_rop is not None and current_stock <= stored_rop) else "Low"))

        effective_rop = None
        if stored_rop is not None or calc_rop is not None:
            effective_rop = max(stored_rop or 0, calc_rop or 0)

        incoming_qty = incoming_map.get(pid, 0)
        pending_qty = pending_map.get(pid, 0)

        needs_reorder = (
            current_stock <= 0
            or (effective_rop is not None and (current_stock + incoming_qty) <= effective_rop)
            or (days_rem is not None and lead_time is not None and days_rem <= lead_time + 2)
        )
        if not needs_reorder:
            continue

        # Determine target stock transparently (explicit target_stock, or forecast-derived horizon target, or reorder_point)
        target_basis_note = ""
        if stored_target is not None:
            effective_target = stored_target
        elif fc_daily is not None and fc_daily > 0:
            # Cover horizon_days + lead time + 2-day buffer strictly from actual sales forecast
            effective_target = int(np.ceil(fc_daily * (horizon_days + (lead_time or 3) + 2)))
            target_basis_note = f"target derived from {horizon_days}-day statistical demand forecast ({effective_target} units)"
        elif stored_rop is not None and stored_rop > 0:
            effective_target = stored_rop
            target_basis_note = f"target set to uploaded reorder point ({stored_rop} units — no Target Stock column mapped)"
        else:
            # Cannot compute a meaningful quantity without target, ROP, or sales velocity
            continue

        base_need = max(1, effective_target - current_stock - incoming_qty)
        ranked = rank_supplier_dataframe(sup_groups.get(pid), required_qty=base_need)

        supplier_id = None
        supplier_name = "Not Mapped (No Supplier Data)"
        supplier_score = None
        moq = 1
        avail_qty = None
        unit_price = float(prod["cost_price"]) if prod["cost_price"] is not None else None
        delivery_days = None

        if not ranked.empty:
            best = ranked.iloc[0]
            if pd.notna(best["available_quantity"]) and int(best["available_quantity"]) <= 0:
                continue
            supplier_id = str(best["supplier_id"])
            supplier_name = str(best["supplier_name"])
            supplier_score = float(best["total_score"]) if pd.notna(best["total_score"]) else None
            moq = int(best["min_order_qty"]) if pd.notna(best["min_order_qty"]) else 1
            avail_qty = int(best["available_quantity"]) if pd.notna(best["available_quantity"]) else None
            if pd.notna(best["unit_price"]):
                unit_price = float(best["unit_price"])
            delivery_days = int(best["delivery_days"]) if pd.notna(best["delivery_days"]) else None

        suggested_qty, val_notes = calculate_suggested_order_quantity(
            target_stock=effective_target,
            current_stock=current_stock,
            incoming_stock=incoming_qty,
            min_order_qty=moq,
            available_quantity=avail_qty,
        )
        if suggested_qty <= 0:
            continue

        est_total_cost = round(suggested_qty * unit_price, 2) if unit_price is not None else None

        within_budget = True
        budget_note = "Within configured budget" if est_total_cost is not None else "Cost unavailable (no unit cost/supplier price mapped)"
        if est_total_cost is not None and budget_limit is not None and budget_limit > 0:
            if cumulative_cost + est_total_cost > budget_limit:
                within_budget = False
                budget_note = (
                    f"Exceeds remaining budget ({format_inr(max(0.0, budget_limit - cumulative_cost))} left "
                    f"of {format_inr(budget_limit)})."
                )
            else:
                cumulative_cost += est_total_cost

        reason_parts = []
        if current_stock <= 0:
            reason_parts.append("Currently out of stock (0 units)")
        elif effective_rop is not None:
            reason_parts.append(f"Stock ({current_stock}) is below reorder threshold ({effective_rop})")
        if days_rem is not None:
            reason_parts.append(f"est. {days_rem} day(s) of stock left")
        if incoming_qty > 0:
            reason_parts.append(f"accounting for {incoming_qty} approved incoming units")
        if pending_qty > 0:
            reason_parts.append(f"note: {pending_qty} units already in pending/modified PO")
        if target_basis_note:
            reason_parts.append(target_basis_note)
        if val_notes:
            reason_parts.extend(val_notes)

        recommendations.append(
            {
                "product_id": pid,
                "product_name": prod["product_name"],
                "category": prod["category"] or "Uncategorized",
                "unit": prod["unit"] or "unit",
                "current_stock": current_stock,
                "incoming_stock": incoming_qty,
                "pending_po_qty": pending_qty,
                "reorder_point": effective_rop,
                "target_stock": effective_target,
                "forecast_daily_demand": fc_daily,
                "days_remaining": days_rem,
                "stockout_risk": stockout_risk,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "supplier_score": supplier_score,
                "min_order_qty": moq,
                "available_quantity": avail_qty,
                "suggested_quantity": suggested_qty,
                "unit_price": unit_price,
                "estimated_total_cost": est_total_cost,
                "expected_delivery_days": delivery_days,
                "within_budget": within_budget,
                "budget_note": budget_note,
                "reason": "; ".join(reason_parts) + ".",
            }
        )

    return recommendations


def create_purchase_order(
    product_id: str,
    supplier_id: Optional[str],
    quantity: int,
    reason: str = "",
    created_by: str = "Procurement Agent",
    budget_limit: Optional[float] = None,
    db_path: DbPath = None,
) -> dict:
    """Create a new purchase order in 'Pending Approval' status with an audit record."""
    product = fetch_one(
        "SELECT * FROM products WHERE product_id = ?",
        (product_id,),
        db_path=db_path,
    )
    if not product:
        raise ValidationError(f"Product '{product_id}' not found.")

    supplier = None
    if supplier_id and supplier_id != "UNASSIGNED":
        supplier = fetch_one(
            "SELECT * FROM suppliers WHERE supplier_id = ? AND product_id = ?",
            (supplier_id, product_id),
            db_path=db_path,
        )
        if not supplier:
            raise ValidationError(
                f"Supplier '{supplier_id}' does not supply product '{product_id}'."
            )

    if int(quantity) <= 0:
        raise ValidationError("Order quantity must be greater than zero.")

    if supplier and supplier["available_quantity"] is not None:
        if int(quantity) > int(supplier["available_quantity"]):
            raise ValidationError(
                f"Requested quantity ({quantity}) exceeds supplier available quantity ({supplier['available_quantity']})."
            )

    unit_price = None
    if supplier and supplier["unit_price"] is not None:
        unit_price = float(supplier["unit_price"])
    elif product["cost_price"] is not None:
        unit_price = float(product["cost_price"])

    if unit_price is not None:
        total_cost = validate_purchase_order_update(
            quantity=int(quantity),
            unit_price=unit_price,
            budget_limit=budget_limit,
        )
    else:
        total_cost = None

    order_id = f"PO-{uuid.uuid4().hex[:8].upper()}"
    now = utc_now_iso()
    status = "Pending Approval"
    eff_sup_id = supplier["supplier_id"] if supplier else "UNASSIGNED"
    eff_sup_name = supplier["supplier_name"] if supplier else "Not Mapped"
    delivery_days = int(supplier["delivery_days"]) if (supplier and supplier["delivery_days"] is not None) else None

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO purchase_orders (
                order_id, product_id, product_name, supplier_id, supplier_name,
                quantity, unit_price, total_cost, delivery_days, status,
                reason, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                product["product_id"],
                product["product_name"],
                eff_sup_id,
                eff_sup_name,
                int(quantity),
                unit_price,
                total_cost,
                delivery_days,
                status,
                reason.strip() if reason else "Generated via Procurement Agent",
                now,
                now,
                created_by,
            ),
        )
        cost_str = format_inr(total_cost) if total_cost is not None else "Price N/A"
        conn.execute(
            """
            INSERT INTO audit_logs (
                order_id, action, previous_status, new_status, timestamp, actor, changes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                "CREATED",
                None,
                status,
                now,
                created_by,
                f"Created draft PO for {quantity} units from {eff_sup_name} ({cost_str}).",
            ),
        )

    return fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)


def approve_purchase_order(
    order_id: str,
    actor: str = "Store Owner",
    note: str = "",
    db_path: DbPath = None,
) -> dict:
    """Human approval step for a purchase order (records audit trail, no external buy)."""
    order = fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)
    if not order:
        raise ValidationError(f"Purchase order '{order_id}' not found.")
    if order["status"] == "Approved":
        raise ValidationError(f"Purchase order '{order_id}' is already Approved.")
    if order["status"] == "Rejected":
        raise ValidationError(f"Cannot approve a Rejected purchase order ('{order_id}').")

    prev_status = order["status"]
    new_status = "Approved"
    now = utc_now_iso()
    cost_str = format_inr(order["total_cost"]) if order["total_cost"] is not None else "Price N/A"
    change_text = f"Approved by {actor} for {order['quantity']} units ({cost_str})."
    if note.strip():
        change_text += f" Note: {note.strip()}"

    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (new_status, now, order_id),
        )
        conn.execute(
            """
            INSERT INTO audit_logs (
                order_id, action, previous_status, new_status, timestamp, actor, changes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, "APPROVED", prev_status, new_status, now, actor, change_text),
        )

    return fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)


def reject_purchase_order(
    order_id: str,
    actor: str = "Store Owner",
    note: str = "",
    db_path: DbPath = None,
) -> dict:
    """Human rejection step for a purchase order with audit logging."""
    order = fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)
    if not order:
        raise ValidationError(f"Purchase order '{order_id}' not found.")
    if order["status"] == "Rejected":
        raise ValidationError(f"Purchase order '{order_id}' is already Rejected.")

    prev_status = order["status"]
    new_status = "Rejected"
    now = utc_now_iso()
    change_text = f"Rejected by {actor}."
    if note.strip():
        change_text += f" Reason: {note.strip()}"

    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (new_status, now, order_id),
        )
        conn.execute(
            """
            INSERT INTO audit_logs (
                order_id, action, previous_status, new_status, timestamp, actor, changes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, "REJECTED", prev_status, new_status, now, actor, change_text),
        )

    return fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)


def modify_purchase_order(
    order_id: str,
    new_quantity: Optional[int] = None,
    new_supplier_id: Optional[str] = None,
    budget_limit: Optional[float] = None,
    actor: str = "Store Owner",
    note: str = "",
    db_path: DbPath = None,
) -> dict:
    """Modify quantity and/or supplier before approval, setting status to 'Modified'."""
    order = fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)
    if not order:
        raise ValidationError(f"Purchase order '{order_id}' not found.")
    if order["status"] in {"Approved", "Rejected"}:
        raise ValidationError(
            f"Cannot modify purchase order '{order_id}' because it is already {order['status']}."
        )

    target_supplier_id = new_supplier_id or order["supplier_id"]
    supplier = None
    if target_supplier_id and target_supplier_id != "UNASSIGNED":
        supplier = fetch_one(
            "SELECT * FROM suppliers WHERE supplier_id = ? AND product_id = ?",
            (target_supplier_id, order["product_id"]),
            db_path=db_path,
        )
        if not supplier:
            raise ValidationError(
                f"Supplier '{target_supplier_id}' does not supply product '{order['product_id']}'."
            )

    target_qty = int(new_quantity) if new_quantity is not None else int(order["quantity"])
    if target_qty <= 0:
        raise ValidationError("Order quantity must be greater than zero.")

    if supplier and supplier["available_quantity"] is not None and target_qty > int(supplier["available_quantity"]):
        raise ValidationError(
            f"Quantity ({target_qty}) exceeds supplier available quantity ({supplier['available_quantity']})."
        )

    unit_price = (
        float(supplier["unit_price"])
        if (supplier and supplier["unit_price"] is not None)
        else (float(order["unit_price"]) if order["unit_price"] is not None else None)
    )
    if unit_price is not None:
        total_cost = validate_purchase_order_update(
            quantity=target_qty,
            unit_price=unit_price,
            budget_limit=budget_limit,
        )
    else:
        total_cost = None

    eff_sup_id = supplier["supplier_id"] if supplier else order["supplier_id"]
    eff_sup_name = supplier["supplier_name"] if supplier else order["supplier_name"]
    eff_delivery = (
        int(supplier["delivery_days"])
        if (supplier and supplier["delivery_days"] is not None)
        else order["delivery_days"]
    )

    changes = []
    if target_qty != int(order["quantity"]):
        changes.append(f"quantity: {order['quantity']} -> {target_qty}")
    if eff_sup_id != order["supplier_id"]:
        changes.append(f"supplier: {order['supplier_name']} -> {eff_sup_name}")
    if total_cost is not None and order["total_cost"] is not None and round(total_cost, 2) != round(float(order["total_cost"]), 2):
        changes.append(f"total_cost: {format_inr(order['total_cost'])} -> {format_inr(total_cost)}")
    if note.strip():
        changes.append(f"note: {note.strip()}")

    if not changes:
        raise ValidationError("No changes were specified for this purchase order.")

    prev_status = order["status"]
    new_status = "Modified"
    now = utc_now_iso()

    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE purchase_orders
            SET supplier_id = ?, supplier_name = ?, quantity = ?,
                unit_price = ?, total_cost = ?, delivery_days = ?,
                status = ?, updated_at = ?
            WHERE order_id = ?
            """,
            (
                eff_sup_id,
                eff_sup_name,
                target_qty,
                unit_price,
                total_cost,
                eff_delivery,
                new_status,
                now,
                order_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_logs (
                order_id, action, previous_status, new_status, timestamp, actor, changes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, "MODIFIED", prev_status, new_status, now, actor, "; ".join(changes)),
        )

    return fetch_one("SELECT * FROM purchase_orders WHERE order_id = ?", (order_id,), db_path=db_path)


def get_purchase_orders(
    status_filter: Optional[str] = None,
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Return all purchase orders sorted newest first."""
    if status_filter and status_filter != "All":
        rows = fetch_all(
            "SELECT * FROM purchase_orders WHERE status = ? ORDER BY updated_at DESC, order_id DESC",
            (status_filter,),
            db_path=db_path,
        )
    else:
        rows = fetch_all(
            "SELECT * FROM purchase_orders ORDER BY updated_at DESC, order_id DESC",
            db_path=db_path,
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=[
            "order_id", "product_id", "product_name", "supplier_id", "supplier_name",
            "quantity", "unit_price", "total_cost", "delivery_days", "status",
            "reason", "created_at", "updated_at", "created_by",
        ]
    )


def get_audit_logs(
    order_id: Optional[str] = None,
    limit: int = 200,
    db_path: DbPath = None,
) -> pd.DataFrame:
    """Return immutable purchase-order audit records sorted newest first."""
    if order_id:
        rows = fetch_all(
            "SELECT * FROM audit_logs WHERE order_id = ? ORDER BY log_id DESC LIMIT ?",
            (order_id, limit),
            db_path=db_path,
        )
    else:
        rows = fetch_all(
            "SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT ?",
            (limit,),
            db_path=db_path,
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["log_id", "order_id", "action", "previous_status", "new_status", "timestamp", "actor", "changes"]
    )
