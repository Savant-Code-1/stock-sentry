"""Safe, idempotent CSV-to-SQLite database seeding."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Union

from config import DATA_DIR
from database.db import get_connection, init_db
from utils.helpers import utc_now_iso


def _read_csv_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Required sample dataset not found: {csv_path}")
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def seed_database_if_empty(
    db_path: Optional[Union[str, Path]] = None,
    data_dir: Optional[Union[str, Path]] = None,
) -> dict:
    """Initialize SQLite schema and safely seed sample CSV data ONLY on first run.

    Never duplicates sample records and never deletes existing user data,
    purchase orders, or audit logs during subsequent startups.
    """
    resolved_db = init_db(db_path)
    source_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    now = utc_now_iso()

    with get_connection(resolved_db) as conn:
        meta = conn.execute(
            "SELECT value FROM app_metadata WHERE key = ?",
            ("initial_seed_completed",),
        ).fetchone()
        product_count = conn.execute("SELECT COUNT(*) AS cnt FROM products").fetchone()["cnt"]

        if (meta and meta["value"] == "true") or product_count > 0:
            return {
                "seeded": False,
                "reason": "Database already initialized with data.",
                "db_path": str(resolved_db),
            }

        # 1. Products
        products_rows = _read_csv_rows(source_dir / "products.csv")
        conn.executemany(
            """
            INSERT OR IGNORE INTO products (
                product_id, product_name, category, current_stock, unit,
                selling_price, cost_price, reorder_point, target_stock, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["product_id"].strip(),
                    r["product_name"].strip(),
                    r["category"].strip(),
                    int(float(r["current_stock"])),
                    r["unit"].strip(),
                    float(r["selling_price"]),
                    float(r["cost_price"]),
                    int(float(r["reorder_point"])),
                    int(float(r["target_stock"])),
                    now,
                )
                for r in products_rows
            ],
        )

        # 2. Sales
        sales_rows = _read_csv_rows(source_dir / "sales.csv")
        conn.executemany(
            """
            INSERT OR IGNORE INTO sales (
                sale_id, date, product_id, quantity_sold, selling_price
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    r["sale_id"].strip(),
                    r["date"].strip(),
                    r["product_id"].strip(),
                    int(float(r["quantity_sold"])),
                    float(r["selling_price"]),
                )
                for r in sales_rows
            ],
        )

        # 3. Suppliers (supports optional min_order_qty column)
        supplier_rows = _read_csv_rows(source_dir / "suppliers.csv")
        conn.executemany(
            """
            INSERT OR IGNORE INTO suppliers (
                supplier_id, supplier_name, product_id, unit_price,
                delivery_days, available_quantity, reliability_score, min_order_qty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["supplier_id"].strip(),
                    r["supplier_name"].strip(),
                    r["product_id"].strip(),
                    float(r["unit_price"]),
                    int(float(r["delivery_days"])),
                    int(float(r["available_quantity"])),
                    float(r["reliability_score"]),
                    int(float(r.get("min_order_qty", 5) or 5)),
                )
                for r in supplier_rows
            ],
        )

        # 4. Stock Movements
        movement_rows = _read_csv_rows(source_dir / "stock_movements.csv")
        conn.executemany(
            """
            INSERT OR IGNORE INTO stock_movements (
                movement_id, date, product_id, movement_type, quantity, remarks
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["movement_id"].strip(),
                    r["date"].strip(),
                    r["product_id"].strip(),
                    r["movement_type"].strip().upper(),
                    int(float(r["quantity"])),
                    r.get("remarks", "").strip(),
                )
                for r in movement_rows
            ],
        )

        # 5. Seed two realistic initial demonstration purchase orders + audit logs
        # so the dashboard and approval queue show realistic workflow history immediately.
        sample_p007 = conn.execute(
            """
            SELECT p.product_id, p.product_name, s.supplier_id, s.supplier_name,
                   s.unit_price, s.delivery_days
            FROM products p
            JOIN suppliers s ON p.product_id = s.product_id
            WHERE p.product_id = 'P007'
            ORDER BY s.unit_price ASC
            LIMIT 1
            """
        ).fetchone()

        if sample_p007:
            qty = 45
            total_cost = round(qty * float(sample_p007["unit_price"]), 2)
            conn.execute(
                """
                INSERT OR IGNORE INTO purchase_orders (
                    order_id, product_id, product_name, supplier_id, supplier_name,
                    quantity, unit_price, total_cost, delivery_days, status,
                    reason, created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "PO-DEMO-001",
                    sample_p007["product_id"],
                    sample_p007["product_name"],
                    sample_p007["supplier_id"],
                    sample_p007["supplier_name"],
                    qty,
                    float(sample_p007["unit_price"]),
                    total_cost,
                    int(sample_p007["delivery_days"]),
                    "Pending Approval",
                    "Product is currently Out of Stock (0 units). Recommended to restore target stock.",
                    now,
                    now,
                    "Procurement Agent (Initial Demo)",
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_logs (
                    order_id, action, previous_status, new_status, timestamp, actor, changes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "PO-DEMO-001",
                    "CREATED",
                    None,
                    "Pending Approval",
                    now,
                    "Procurement Agent (Initial Demo)",
                    f"Draft PO created for {qty} units at ₹{sample_p007['unit_price']:.2f}/unit.",
                ),
            )

        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("initial_seed_completed", "true"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("seeded_at", now),
        )

    return {
        "seeded": True,
        "products": len(products_rows),
        "sales": len(sales_rows),
        "suppliers": len(supplier_rows),
        "stock_movements": len(movement_rows),
        "db_path": str(resolved_db),
    }
