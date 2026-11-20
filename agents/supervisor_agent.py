"""Supervisor / Orchestrator Agent: coordinates all specialized agents and powers the assistant."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from agents.anomaly_agent import AnomalyAgent
from agents.forecasting_agent import ForecastingAgent
from agents.inventory_agent import InventoryAgent
from agents.procurement_agent import ProcurementAgent
from agents.supplier_agent import SupplierAgent
from config import ANOMALY_DISCLAIMER, LLM_API_KEY, LLM_PROVIDER, PROCUREMENT_DISCLAIMER
from utils.helpers import format_inr

DbPath = Optional[Union[str, Path]]


class SupervisorAgent:
    """Central orchestrator coordinating Stock Sentry's 5 domain agents.

    Operates strictly on whichever SQLite database `db_path` is provided
    (isolated user session DB or optional demo DB). Never fabricates missing
    business metrics when uploaded datasets omit optional columns.
    """

    name = "Supervisor / Orchestrator Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path
        self.inventory_agent = InventoryAgent(db_path=db_path)
        self.forecasting_agent = ForecastingAgent(db_path=db_path)
        self.anomaly_agent = AnomalyAgent(db_path=db_path)
        self.supplier_agent = SupplierAgent(db_path=db_path)
        self.procurement_agent = ProcurementAgent(db_path=db_path)

    @property
    def assistant_mode_label(self) -> str:
        if LLM_PROVIDER != "local_rule_based" and LLM_API_KEY:
            return f"External LLM Configured ({LLM_PROVIDER}) — with Local Tool Grounding"
        return "Local Rule-Based Assistant (Live Active Dataset — No External LLM API)"

    def run_store_health_check(self) -> dict:
        """Execute a single-pass multi-agent workflow without redundant forecast recalculation."""
        inv = self.inventory_agent.get_overview()["summary"]
        risk_5d = self.forecasting_agent.get_stockout_risk_products(within_days=5)
        portfolio_fc = risk_5d.get("portfolio_df")
        anoms = self.anomaly_agent.scan_anomalies()
        recs = self.procurement_agent.recommend_orders(
            precomputed_forecast_df=portfolio_fc if risk_5d.get("ready") else pd.DataFrame()
        )
        sup_overview = self.supplier_agent.get_supplier_overview()
        orders_df = self.procurement_agent.list_orders()
        pending_count = (
            int(orders_df["status"].isin(["Pending Approval", "Modified"]).sum())
            if not orders_df.empty
            else 0
        )

        return {
            "orchestrator": self.name,
            "agents_invoked": [
                self.inventory_agent.name,
                self.forecasting_agent.name,
                self.anomaly_agent.name,
                self.supplier_agent.name,
                self.procurement_agent.name,
            ],
            "inventory_summary": inv,
            "forecast_ready": bool(risk_5d.get("ready", True)),
            "forecast_limitation": risk_5d.get("limitation"),
            "stockout_risk_5d_count": risk_5d["count"],
            "anomaly_count": anoms["total"],
            "anomaly_severity": anoms["severity_counts"],
            "supplier_count": int(len(sup_overview)),
            "recommended_orders_count": recs["count"],
            "recommended_orders_cost": recs["total_estimated_cost"],
            "pending_purchase_orders": pending_count,
            "_anomalies_detail": anoms,
            "_recommendations_detail": recs,
            "_supplier_overview": sup_overview,
        }

    def _extract_days(self, text: str, default: int = 5) -> int:
        word_map = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "ten": 10, "fourteen": 14,
        }
        digit_match = re.search(r"(\d+)\s*day", text)
        if digit_match:
            return max(1, min(60, int(digit_match.group(1))))
        for word, val in word_map.items():
            if re.search(rf"\b{word}\s+day", text):
                return val
        return default

    def answer_query(self, user_query: str) -> dict:
        """Route a natural-language question to the appropriate agent(s) and return grounded output."""
        q_raw = (user_query or "").strip()
        q = q_raw.lower()

        if not q:
            return {
                "answer": "Please enter a question about your inventory, demand forecast, anomalies, suppliers, or purchase orders.",
                "agents_used": [self.name],
                "mode": self.assistant_mode_label,
                "supporting_table": None,
            }

        # Intent 1: Inventory Value
        if any(k in q for k in ["inventory value", "total value", "worth", "valuation", "how much inventory"]):
            inv = self.inventory_agent.get_overview()["summary"]
            cost_txt = format_inr(inv["total_cost_value"]) if inv["has_cost_data"] else "Unavailable (no Unit Cost column mapped)"
            retail_txt = format_inr(inv["total_retail_value"]) if inv["has_retail_data"] else "Unavailable (no Selling Price column mapped)"
            lines = [
                "### Current Inventory Valuation",
                f"- **Total Inventory Value (Cost Price):** {cost_txt}",
                f"- **Total Inventory Value (Retail / Selling Price):** {retail_txt}",
            ]
            if inv["has_cost_data"] and inv["has_retail_data"]:
                lines.append(
                    f"- **Estimated Gross Margin on Current Stock:** {format_inr(inv['total_retail_value'] - inv['total_cost_value'])}"
                )
            lines.extend(
                [
                    f"- **Active Products:** {inv['total_products']} SKUs ({inv['total_units']:,} total units)",
                    f"- **Stock Health:** {inv['healthy_count']} Healthy | {inv['low_stock_count']} Low Stock | {inv['out_of_stock_count']} Out of Stock | {inv['overstock_count']} Overstock",
                ]
            )
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.inventory_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": inv["category_breakdown"],
            }

        # Intent 2: Run out in next N days (Forecasting)
        if any(k in q for k in ["run out", "stockout", "next", "days remaining", "forecast"]):
            days = self._extract_days(q, default=5)
            risk_res = self.forecasting_agent.get_stockout_risk_products(within_days=days)
            if not risk_res.get("ready", True):
                return {
                    "answer": f"### Demand Forecasting Unavailable\n{risk_res['limitation']}",
                    "agents_used": [self.name, self.forecasting_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            df = risk_res["products"]
            if df.empty:
                return {
                    "answer": f"Good news: No products are projected to run out within the next **{days} day(s)** based on current statistical demand baselines.",
                    "agents_used": [self.name, self.forecasting_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            lines = [
                f"### Products Projected to Run Out Within {days} Day(s) ({len(df)} found)",
                "_Based on Exponential Smoothing baseline demand estimates (not deep ML):_",
                "",
            ]
            for _, r in df.head(12).iterrows():
                dr = "0 days (Out of Stock)" if r["current_stock"] <= 0 else f"{r['days_remaining']} days"
                lines.append(
                    f"- **{r['product_name']}** (`{r['product_id']}`): Stock **{r['current_stock']}**, "
                    f"Est. demand **{r['forecast_daily_demand']}/day**, Remaining: **{dr}** — Risk: **{r['stockout_risk']}**"
                )
            if len(df) > 12:
                lines.append(f"\n_...and {len(df) - 12} more item(s). See table below._")
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.forecasting_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": df[["product_id", "product_name", "current_stock", "forecast_daily_demand", "days_remaining", "stockout_risk"]],
            }

        # Intent 3: Best Supplier comparison
        if any(k in q for k in ["supplier", "vendor", "best price", "cheapest", "who sells", "where to buy"]):
            sup_all = self.supplier_agent.list_suppliers()
            if sup_all.empty:
                return {
                    "answer": "### Supplier Analysis Unavailable\nNo supplier records exist in the active dataset. Map a **Supplier / Vendor Name** column during upload to enable supplier comparison.",
                    "agents_used": [self.name, self.supplier_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            matches = self.supplier_agent.find_best_suppliers_by_keyword(q, required_qty=15)
            if not matches:
                return {
                    "answer": (
                        "I could not match a specific product name in your supplier question. "
                        "Try naming one of the products from your uploaded dataset (or open **Supplier Analysis**)."
                    ),
                    "agents_used": [self.name, self.supplier_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            lines = ["### Supplier Comparison Recommendation", ""]
            first_table = matches[0]["ranked_table"]
            for m in matches:
                top = m["top_supplier"]
                score_txt = f"{top['total_score']}/100" if pd.notna(top.get("total_score")) else "N/A"
                price_txt = format_inr(top["unit_price"]) if pd.notna(top.get("unit_price")) else "N/A"
                days_txt = f"{int(top['delivery_days'])} day(s)" if pd.notna(top.get("delivery_days")) else "N/A"
                rel_txt = f"{top['reliability_score']}%" if pd.notna(top.get("reliability_score")) else "N/A"
                lines.append(
                    f"- **{m['product_name']}** (`{m['product_id']}`): Top supplier is "
                    f"**{top['supplier_name']}** (Score: **{score_txt}**, "
                    f"Price: **{price_txt}**, Delivery: **{days_txt}**, Reliability: **{rel_txt}**). "
                    f"_{top['recommendation_reason']}_"
                )
            cols = ["rank", "supplier_name", "unit_price", "delivery_days", "available_quantity", "reliability_score", "total_score", "recommendation_reason"]
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.supplier_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": first_table[cols],
            }

        # Intent 4: Anomalies explanation
        if any(k in q for k in ["anomaly", "anomalies", "unusual", "suspicious", "mismatch", "spike", "discrepancy"]):
            scan = self.anomaly_agent.scan_anomalies()
            anoms = scan["anomalies"]
            if not anoms:
                skipped_note = ""
                if scan["checks_skipped"]:
                    skipped_note = "\n\nChecks skipped due to missing columns:\n" + "\n".join(f"- {s}" for s in scan["checks_skipped"])
                return {
                    "answer": f"No statistical or rule-based inventory anomalies are currently detected on the active dataset.{skipped_note}",
                    "agents_used": [self.name, self.anomaly_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            lines = [
                f"### Detected Inventory & Sales Anomalies ({len(anoms)} total)",
                f"> {ANOMALY_DISCLAIMER}",
                "",
            ]
            for a in anoms[:8]:
                lines.append(
                    f"- **[{a['severity']}] {a['product_name']}** — *{a['detected_event']}*: "
                    f"Expected `{a['expected_value']}`, Actual `{a['actual_value']}`. "
                    f"{a['explanation']} **Suggested action:** {a['suggested_action']}"
                )
            if len(anoms) > 8:
                lines.append(f"\n_Showing top 8 of {len(anoms)} anomalies. Open Anomaly Detection for full details._")
            tbl = pd.DataFrame(anoms)[["product_name", "detected_event", "expected_value", "actual_value", "severity", "suggested_action"]]
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.anomaly_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": tbl,
            }

        # Intent 5: Reorder / Purchase Recommendations
        if any(k in q for k in ["reorder", "purchase", "order", "procure", "buy today", "restock", "recommendation"]):
            rec_res = self.procurement_agent.recommend_orders()
            recs = rec_res["recommendations"]
            if not recs:
                return {
                    "answer": "No purchase orders are currently recommended (either all tracked items are above reorder thresholds, or Target Stock / Reorder Point / dated sales columns are not mapped).",
                    "agents_used": [self.name, self.forecasting_agent.name, self.supplier_agent.name, self.procurement_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            cost_summary = format_inr(rec_res["total_estimated_cost"]) if rec_res["total_estimated_cost"] is not None else "Unavailable (no cost/supplier prices mapped)"
            lines = [
                f"### Purchase Order Recommendations ({len(recs)} products need restocking)",
                f"- **Total Estimated Procurement Cost:** {cost_summary}",
                f"> {PROCUREMENT_DISCLAIMER}",
                "",
            ]
            for r in recs[:10]:
                cost_txt = format_inr(r["estimated_total_cost"]) if r["estimated_total_cost"] is not None else "Price N/A"
                price_txt = format_inr(r["unit_price"]) if r["unit_price"] is not None else "Price N/A"
                del_txt = f"{r['expected_delivery_days']}d" if r["expected_delivery_days"] is not None else "N/A"
                lines.append(
                    f"- **{r['product_name']}**: Order **{r['suggested_quantity']} {r['unit']}(s)** via "
                    f"**{r['supplier_name']}** @ {price_txt} = **{cost_txt}** "
                    f"(Delivery: {del_txt}) — _{r['reason']}_"
                )
            tbl = pd.DataFrame(recs)[
                ["product_name", "current_stock", "suggested_quantity", "supplier_name", "unit_price", "estimated_total_cost", "expected_delivery_days", "within_budget"]
            ]
            return {
                "answer": "\n".join(lines),
                "agents_used": [
                    self.name,
                    self.forecasting_agent.name,
                    self.supplier_agent.name,
                    self.procurement_agent.name,
                ],
                "mode": self.assistant_mode_label,
                "supporting_table": tbl,
            }

        # Intent 6: Overstock products
        if any(k in q for k in ["overstock", "excess", "too much stock", "surplus"]):
            ov = self.inventory_agent.get_overstock_alerts()
            df = ov["products"]
            if df.empty:
                return {
                    "answer": "No overstocked products are detected (either all items are within Target Stock, or no Target Stock column is mapped).",
                    "agents_used": [self.name, self.inventory_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            lines = [f"### Overstocked Products ({len(df)} items)", ""]
            for _, r in df.head(12).iterrows():
                lines.append(
                    f"- **{r['product_name']}** (`{r['product_id']}`): Stock **{r['current_stock']}** vs Target **{int(r['target_stock'])}** (Excess: **+{int(r['excess_units'])}**)"
                )
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.inventory_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": df[["product_id", "product_name", "category", "current_stock", "target_stock", "excess_units"]],
            }

        # Intent 7: Low-stock / Out-of-stock products
        if any(k in q for k in ["low", "running low", "out of stock", "empty", "shortage", "running out"]):
            if "out of stock" in q or "empty" in q:
                alert = self.inventory_agent.get_out_of_stock_alerts()
                title = "Out-of-Stock Products"
            else:
                alert = self.inventory_agent.get_low_stock_alerts(include_out_of_stock=True)
                title = "Low-Stock & Out-of-Stock Products"
            df = alert["products"]
            if df.empty:
                return {
                    "answer": f"There are currently no {title.lower()} detected in the active dataset.",
                    "agents_used": [self.name, self.inventory_agent.name],
                    "mode": self.assistant_mode_label,
                    "supporting_table": None,
                }
            lines = [f"### {title} ({len(df)} items)", ""]
            for _, r in df.head(12).iterrows():
                rop_txt = str(int(r["reorder_point"])) if pd.notna(r["reorder_point"]) else "Not mapped"
                lines.append(
                    f"- **{r['product_name']}** (`{r['product_id']}`, {r['category']}): "
                    f"**{r['current_stock']} {r['unit']}(s)** left (Reorder Point: {rop_txt}) — **{r['stock_status']}**"
                )
            if len(df) > 12:
                lines.append(f"\n_...and {len(df) - 12} more item(s). See table below._")
            return {
                "answer": "\n".join(lines),
                "agents_used": [self.name, self.inventory_agent.name],
                "mode": self.assistant_mode_label,
                "supporting_table": df[["product_id", "product_name", "category", "current_stock", "reorder_point", "target_stock", "stock_status"]],
            }

        # Default fallback: Full store health summary
        health = self.run_store_health_check()
        inv = health["inventory_summary"]
        val_txt = format_inr(inv["total_cost_value"]) if inv["has_cost_data"] else "N/A (no cost column)"
        rec_cost_txt = format_inr(health["recommended_orders_cost"]) if health["recommended_orders_cost"] is not None else "Price N/A"
        lines = [
            "### Stock Sentry Active Dataset Health Overview",
            f"- **Inventory:** {inv['total_products']} products ({inv['total_units']:,} units) | Cost Value: **{val_txt}**",
            f"- **Attention Needed:** {inv['out_of_stock_count']} out of stock, {inv['low_stock_count']} low stock, {inv['overstock_count']} overstock",
            f"- **5-Day Stockout Risk:** {health['stockout_risk_5d_count']} product(s)" if health["forecast_ready"] else f"- **Demand Forecasting:** Unavailable ({health['forecast_limitation']})",
            f"- **Detected Anomalies:** {health['anomaly_count']} event(s)",
            f"- **Recommended Reorders:** {health['recommended_orders_count']} item(s) (est. {rec_cost_txt})",
            f"- **Suppliers Tracked:** {health['supplier_count']} | **Pending Purchase Orders:** {health['pending_purchase_orders']}",
        ]
        return {
            "answer": "\n".join(lines),
            "agents_used": health["agents_invoked"],
            "mode": self.assistant_mode_label,
            "supporting_table": None,
        }
