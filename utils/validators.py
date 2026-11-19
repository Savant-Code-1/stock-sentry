"""Input validation helpers to keep database records consistent and safe."""

from __future__ import annotations

from typing import Optional


class ValidationError(ValueError):
    """Raised when user or API input fails validation."""


VALID_MOVEMENT_TYPES = {"RESTOCK", "SALE", "ADJUSTMENT", "DAMAGE", "RETURN"}
VALID_PO_STATUSES = {"Pending Approval", "Approved", "Rejected", "Modified"}


def validate_non_empty_text(value: str, field_name: str, max_len: int = 120) -> str:
    """Ensure a text field is non-empty and trimmed."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} cannot be empty.")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise ValidationError(f"{field_name} must be {max_len} characters or fewer.")
    return cleaned


def validate_product_payload(
    product_id: str,
    product_name: str,
    category: str,
    current_stock: float,
    unit: str,
    selling_price: float,
    cost_price: float,
    reorder_point: float,
    target_stock: float,
) -> dict:
    """Validate fields required to add or update a product."""
    pid = validate_non_empty_text(product_id, "Product ID", max_len=30)
    pname = validate_non_empty_text(product_name, "Product Name", max_len=120)
    cat = validate_non_empty_text(category, "Category", max_len=60)
    u = validate_non_empty_text(unit, "Unit", max_len=30)

    if current_stock < 0:
        raise ValidationError("Current stock cannot be negative.")
    if cost_price <= 0:
        raise ValidationError("Cost price must be greater than zero.")
    if selling_price <= 0:
        raise ValidationError("Selling price must be greater than zero.")
    if reorder_point < 0:
        raise ValidationError("Reorder point cannot be negative.")
    if target_stock <= 0:
        raise ValidationError("Target stock must be greater than zero.")
    if target_stock < reorder_point:
        raise ValidationError("Target stock should be greater than or equal to reorder point.")

    return {
        "product_id": pid,
        "product_name": pname,
        "category": cat,
        "current_stock": int(round(current_stock)),
        "unit": u,
        "selling_price": round(float(selling_price), 2),
        "cost_price": round(float(cost_price), 2),
        "reorder_point": int(round(reorder_point)),
        "target_stock": int(round(target_stock)),
    }


def validate_stock_movement(
    current_stock: int,
    movement_type: str,
    quantity: int,
    remarks: str = "",
) -> dict:
    """Validate a stock movement and compute resulting stock."""
    mtype = movement_type.strip().upper()
    if mtype not in VALID_MOVEMENT_TYPES:
        raise ValidationError(
            f"Invalid movement type '{movement_type}'. Allowed: {sorted(VALID_MOVEMENT_TYPES)}"
        )

    if quantity == 0:
        raise ValidationError("Movement quantity cannot be zero.")

    # Normalize signed delta based on movement type
    if mtype in {"RESTOCK", "RETURN"}:
        if quantity < 0:
            raise ValidationError(f"{mtype} quantity must be positive.")
        delta = int(quantity)
    elif mtype in {"SALE", "DAMAGE"}:
        delta = -abs(int(quantity))
    else:  # ADJUSTMENT can be positive or negative
        delta = int(quantity)

    new_stock = int(current_stock) + delta
    if new_stock < 0:
        raise ValidationError(
            f"Insufficient stock: current stock is {current_stock}, cannot apply delta {delta}."
        )

    return {
        "movement_type": mtype,
        "quantity": delta,
        "new_stock": new_stock,
        "remarks": (remarks or "").strip()[:250],
    }


def validate_supplier_payload(
    supplier_id: str,
    supplier_name: str,
    product_id: str,
    unit_price: float,
    delivery_days: int,
    available_quantity: int,
    reliability_score: float,
    min_order_qty: int = 1,
) -> dict:
    """Validate supplier-product offer fields."""
    sid = validate_non_empty_text(supplier_id, "Supplier ID", max_len=30)
    sname = validate_non_empty_text(supplier_name, "Supplier Name", max_len=120)
    pid = validate_non_empty_text(product_id, "Product ID", max_len=30)

    if unit_price <= 0:
        raise ValidationError("Unit price must be greater than zero.")
    if delivery_days < 1:
        raise ValidationError("Delivery days must be at least 1.")
    if available_quantity < 0:
        raise ValidationError("Available quantity cannot be negative.")
    if not (0 <= reliability_score <= 100):
        raise ValidationError("Reliability score must be between 0 and 100.")
    if min_order_qty < 1:
        raise ValidationError("Minimum order quantity must be at least 1.")

    return {
        "supplier_id": sid,
        "supplier_name": sname,
        "product_id": pid,
        "unit_price": round(float(unit_price), 2),
        "delivery_days": int(delivery_days),
        "available_quantity": int(available_quantity),
        "reliability_score": round(float(reliability_score), 2),
        "min_order_qty": int(min_order_qty),
    }


def validate_purchase_order_update(
    quantity: int,
    unit_price: float,
    budget_limit: Optional[float] = None,
) -> float:
    """Validate quantity, unit price, and optional budget constraint."""
    if quantity <= 0:
        raise ValidationError("Order quantity must be greater than zero.")
    if unit_price <= 0:
        raise ValidationError("Unit price must be greater than zero.")
    total_cost = round(float(quantity) * float(unit_price), 2)
    if budget_limit is not None and budget_limit > 0 and total_cost > budget_limit:
        raise ValidationError(
            f"Order cost ({total_cost:.2f}) exceeds configured budget limit ({budget_limit:.2f})."
        )
    return total_cost
