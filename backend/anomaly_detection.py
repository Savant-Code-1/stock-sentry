"""Statistical and rule-based inventory anomaly detection.

Important: Detected anomalies indicate statistically unusual inventory events only.
They are NEVER proof of theft, fraud, or wrongdoing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from config import (
    ANOMALY_DISCLAIMER,
    LARGE_ADJUSTMENT_UNITS,
    LEDGER_MISMATCH_THRESHOLD,
    SALES_SPIKE_Z_THRESHOLD,
    SUDDEN_STOCK_DROP_PCT,
)
from database.db import get_connection
from utils.helpers import utc_now_iso

DbPath = Optional[Union[str, Path]]


def run_anomaly_scan_detailed(db_path: DbPath = None) -> dict:
    """Run all applicable anomaly checks in a single batched DB read and report coverage."""
    with get_connection(db_path) as conn:
        products = [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]
        if not products:
            return {
                "anomalies": [],
                "checks_executed": [],
                "checks_skipped": ["All checks skipped: no product records loaded."],
                "disclaimer": ANOMALY_DISCLAIMER,
            }

        sales_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT product_id, date, SUM(quantity_sold) AS daily_qty
                FROM sales
                WHERE date IS NOT NULL AND TRIM(date) != ''
                GROUP BY product_id, date
                ORDER BY product_id, date ASC
                """
            ).fetchall()
        ]
        mov_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT movement_id, date, product_id, movement_type, quantity, remarks
                FROM stock_movements
                ORDER BY date ASC, movement_id ASC
                """
            ).fetchall()
        ]

    prod_map = {p["product_id"]: p for p in products}
    anomalies: list[dict] = []
    checks_executed: list[str] = []
    checks_skipped: list[str] = []
    detected_at = utc_now_iso()

    sales_df = pd.DataFrame(sales_rows) if sales_rows else pd.DataFrame()
    if not sales_df.empty:
        sales_df["daily_qty"] = pd.to_numeric(sales_df["daily_qty"], errors="coerce").fillna(0.0)
        sales_df["date_dt"] = pd.to_datetime(sales_df["date"], errors="coerce")

    # 1. Unusually High Sales Spikes (Z-score on per-product daily sales)
    if not sales_df.empty:
        eligible_sales_skus = 0
        for pid, grp in sales_df.groupby("product_id", sort=False):
            if len(grp) < 5 or pid not in prod_map:
                continue
            eligible_sales_skus += 1
            vals = grp["daily_qty"].to_numpy(dtype=float)
            baseline = vals[:-1] if vals.size > 5 else vals
            mean_val = float(baseline.mean())
            std_val = float(baseline.std(ddof=0))
            recent = grp.tail(4)

            for row in recent.itertuples(index=False):
                qty = float(row.daily_qty)
                if std_val > 0:
                    z_score = (qty - mean_val) / std_val
                else:
                    z_score = 3.0 if qty >= max(5.0, mean_val * 2.5) else 0.0

                if z_score >= SALES_SPIKE_Z_THRESHOLD and qty >= max(8.0, mean_val * 2.2):
                    severity = "High" if z_score >= 3.2 else "Medium"
                    anomalies.append(
                        {
                            "anomaly_id": f"ANOM-SALE-{pid}-{row.date}",
                            "product_id": pid,
                            "product_name": prod_map[pid]["product_name"],
                            "detected_event": "Unusually High Sales Spike",
                            "expected_value": f"{mean_val:.1f} units/day (historical avg)",
                            "actual_value": f"{int(qty)} units on {row.date} (z={z_score:.1f})",
                            "severity": severity,
                            "detection_time": detected_at,
                            "explanation": (
                                f"Recorded sales of {int(qty)} units on {row.date} are "
                                f"{qty / max(mean_val, 0.1):.1f}x higher than the usual average "
                                f"of {mean_val:.1f} units/day. Could indicate bulk customer purchase, "
                                f"local demand surge, or a billing entry duplicate."
                            ),
                            "suggested_action": (
                                "Verify the sales bill for bulk orders or entry typos, and check "
                                "shelf stock to prepare an early restock if demand is genuine."
                            ),
                            "disclaimer": ANOMALY_DISCLAIMER,
                        }
                    )
        if eligible_sales_skus > 0:
            checks_executed.append(
                f"Sales Spike Z-Score Analysis (z >= {SALES_SPIKE_Z_THRESHOLD}) across {eligible_sales_skus} SKU(s) with >=5 sales dates"
            )
        else:
            checks_skipped.append(
                "Sales Spike Z-Score Analysis: requires at least 5 dated sales entries per SKU."
            )
    else:
        checks_skipped.append(
            "Sales Spike Z-Score Analysis: no dated sales history uploaded."
        )

    # 2, 3, 4. Movement-based anomaly checks (Reductions, Repeated Patterns, Ledger Variance)
    if mov_rows:
        checks_executed.append(
            f"Stock Movement Drop, Repeated Adjustment Pattern & Ledger Variance Checks ({len(mov_rows)} movement logs)"
        )
        mov_df = pd.DataFrame(mov_rows)
        mov_df["movement_type"] = mov_df["movement_type"].astype(str).str.upper()
        mov_df["quantity"] = pd.to_numeric(mov_df["quantity"], errors="coerce").fillna(0).astype(int)
        mov_df["date_dt"] = pd.to_datetime(mov_df["date"], errors="coerce")

        # 2. Single large negative adjustment / damage drop
        neg_adj = mov_df[(mov_df["movement_type"].isin(["ADJUSTMENT", "DAMAGE"])) & (mov_df["quantity"] < 0)]
        for row in neg_adj.itertuples(index=False):
            pid = row.product_id
            if pid not in prod_map:
                continue
            mtype = row.movement_type
            qty = int(row.quantity)
            target_raw = prod_map[pid].get("target_stock")
            target = max(1, int(target_raw)) if target_raw is not None else max(1, int(prod_map[pid]["current_stock"]) + abs(qty))
            abs_qty = abs(qty)
            drop_pct = abs_qty / float(target)
            if abs_qty >= LARGE_ADJUSTMENT_UNITS or drop_pct >= SUDDEN_STOCK_DROP_PCT:
                severity = "Critical" if (abs_qty >= 25 or drop_pct >= 0.45) else "High"
                event_label = (
                    "Unexpected Stock Reduction"
                    if mtype == "ADJUSTMENT"
                    else "Sudden Large Damage / Shrinkage Entry"
                )
                anomalies.append(
                    {
                        "anomaly_id": f"ANOM-MOV-{row.movement_id}",
                        "product_id": pid,
                        "product_name": prod_map[pid]["product_name"],
                        "detected_event": event_label,
                        "expected_value": "Routine adjustment within ±5 units",
                        "actual_value": f"{qty} units ({mtype}) on {row.date or 'undated'}",
                        "severity": severity,
                        "detection_time": detected_at,
                        "explanation": (
                            f"A single {mtype.lower()} movement reduced stock by {abs_qty} units "
                            f"({drop_pct * 100:.0f}% of reference stock) on {row.date or 'undated entry'}. "
                            f"Remark logged: '{row.remarks or 'None'}'."
                        ),
                        "suggested_action": (
                            "Perform a physical shelf and backroom recount, and check whether "
                            "unlogged counter sales or damaged packs explain the difference."
                        ),
                        "disclaimer": ANOMALY_DISCLAIMER,
                    }
                )

        # 3. Repeated downward adjustments within 7 days
        for pid, grp in neg_adj.groupby("product_id", sort=False):
            if len(grp) < 3 or pid not in prod_map:
                continue
            valid_dated = grp[grp["date_dt"].notna()].sort_values("date_dt")
            if len(valid_dated) < 3:
                continue
            recent_end = valid_dated["date_dt"].max()
            window_grp = valid_dated[valid_dated["date_dt"] >= recent_end - pd.Timedelta(days=7)]
            if len(window_grp) >= 3:
                total_drop = int(abs(window_grp["quantity"].sum()))
                anomalies.append(
                    {
                        "anomaly_id": f"ANOM-PAT-{pid}-{recent_end.strftime('%Y-%m-%d')}",
                        "product_id": pid,
                        "product_name": prod_map[pid]["product_name"],
                        "detected_event": "Repeated Downward Adjustment Pattern",
                        "expected_value": "<= 1 minor adjustment per week",
                        "actual_value": f"{len(window_grp)} downward adjustments totaling -{total_drop} units in 7 days",
                        "severity": "High",
                        "detection_time": detected_at,
                        "explanation": (
                            f"{len(window_grp)} separate negative adjustments occurred within a "
                            f"7-day window, reducing stock by {total_drop} units. Repeated small "
                            f"corrections may indicate unrecorded counter sales, miscounting, "
                            f"or storage handling issues."
                        ),
                        "suggested_action": (
                            "Review daily closing tally procedure for this SKU and confirm "
                            "all counter sales are logged consistently."
                        ),
                        "disclaimer": ANOMALY_DISCLAIMER,
                    }
                )

        # 4. Recorded Stock vs Expected Recent Ledger Stock (pre-aggregated sales map — O(N) instead of O(N^2))
        dated_mov = mov_df[mov_df["date_dt"].notna()]
        if not dated_mov.empty:
            latest_mov_date = dated_mov["date_dt"].max()
            cutoff = latest_mov_date - pd.Timedelta(days=10)
            recent_mov = dated_mov[dated_mov["date_dt"] >= cutoff]

            recent_sales_map: dict[str, int] = {}
            if not sales_df.empty:
                recent_s = sales_df[sales_df["date_dt"] >= cutoff]
                if not recent_s.empty:
                    recent_sales_map = (
                        recent_s.groupby("product_id")["daily_qty"].sum().astype(int).to_dict()
                    )

            for pid, grp in recent_mov.groupby("product_id", sort=False):
                if pid not in prod_map:
                    continue
                restock_sum = int(grp[grp["movement_type"] == "RESTOCK"]["quantity"].sum())
                if restock_sum < 30:
                    continue
                recent_sales = recent_sales_map.get(pid, 0)
                non_sale_net = int(grp[grp["movement_type"] != "SALE"]["quantity"].sum())
                expected_floor = max(0, non_sale_net - recent_sales)
                actual_stock = int(prod_map[pid]["current_stock"])
                gap = expected_floor - actual_stock
                if gap >= LEDGER_MISMATCH_THRESHOLD:
                    severity = "High" if gap >= 15 else "Medium"
                    anomalies.append(
                        {
                            "anomaly_id": f"ANOM-VAR-{pid}",
                            "product_id": pid,
                            "product_name": prod_map[pid]["product_name"],
                            "detected_event": "Recorded Stock vs Expected Stock Variance",
                            "expected_value": f">= {expected_floor} units (based on recent restocks minus sales)",
                            "actual_value": f"{actual_stock} units recorded ({gap} units lower)",
                            "severity": severity,
                            "detection_time": detected_at,
                            "explanation": (
                                f"Recent restock movements (+{restock_sum} units in last 10 days) minus "
                                f"recorded sales ({recent_sales} units) suggest at least {expected_floor} units "
                                f"should remain, while current recorded stock is {actual_stock} units."
                            ),
                            "suggested_action": (
                                "Cross-check the supplier delivery invoice quantity against actual "
                                "units received and verify unlogged counter sales."
                            ),
                            "disclaimer": ANOMALY_DISCLAIMER,
                        }
                    )
    else:
        checks_skipped.append(
            "Stock Movement & Ledger Variance Checks: no stock movement columns (Movement Type / Movement Quantity) were mapped."
        )

    # 5. Extreme Overstock / Pricing Margin Anomaly Checks
    has_target_any = any(p.get("target_stock") is not None for p in products)
    has_both_prices = any(p.get("cost_price") is not None and p.get("selling_price") is not None for p in products)

    if has_target_any:
        checks_executed.append("Extreme Overstock Check (Current Stock >= 2.0x Target Stock)")
        for p in products:
            tgt = p.get("target_stock")
            stk = int(p["current_stock"])
            if tgt is not None and tgt > 0 and stk >= 2 * int(tgt):
                anomalies.append(
                    {
                        "anomaly_id": f"ANOM-OVS-{p['product_id']}",
                        "product_id": p["product_id"],
                        "product_name": p["product_name"],
                        "detected_event": "Extreme Overstock Deviation",
                        "expected_value": f"<= {int(tgt)} units (Target Stock)",
                        "actual_value": f"{stk} units ({stk / float(tgt):.1f}x target)",
                        "severity": "Medium",
                        "detection_time": detected_at,
                        "explanation": (
                            f"Current stock ({stk}) is more than double the configured target stock ({int(tgt)}). "
                            "May reflect a duplicate receipt entry, slow-moving stock, or outdated target capacity."
                        ),
                        "suggested_action": (
                            "Verify recent purchase entries for duplicates and pause reorders until excess stock clears."
                        ),
                        "disclaimer": ANOMALY_DISCLAIMER,
                    }
                )

    if has_both_prices:
        checks_executed.append("Negative Margin / Pricing Inversion Check (Selling Price < Cost Price)")
        for p in products:
            cp = p.get("cost_price")
            sp = p.get("selling_price")
            if cp is not None and sp is not None and float(sp) < float(cp):
                anomalies.append(
                    {
                        "anomaly_id": f"ANOM-PRC-{p['product_id']}",
                        "product_id": p["product_id"],
                        "product_name": p["product_name"],
                        "detected_event": "Selling Price Below Cost Price",
                        "expected_value": f">= ₹{float(cp):.2f} (Unit Cost)",
                        "actual_value": f"₹{float(sp):.2f} Selling Price",
                        "severity": "High",
                        "detection_time": detected_at,
                        "explanation": (
                            f"Recorded selling price (₹{float(sp):.2f}) is lower than cost price (₹{float(cp):.2f}). "
                            "Could be a data-entry swap, clearance discount, or outdated cost sheet."
                        ),
                        "suggested_action": "Verify unit cost and MRP/selling price on the latest invoice.",
                        "disclaimer": ANOMALY_DISCLAIMER,
                    }
                )

    severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    anomalies.sort(key=lambda a: (severity_order.get(a["severity"], 4), a["product_name"]))

    return {
        "anomalies": anomalies,
        "checks_executed": checks_executed,
        "checks_skipped": checks_skipped,
        "disclaimer": ANOMALY_DISCLAIMER,
    }


def detect_anomalies(db_path: DbPath = None) -> list[dict]:
    """Run all applicable statistical and rule-based anomaly checks and return anomaly list."""
    return run_anomaly_scan_detailed(db_path=db_path)["anomalies"]
