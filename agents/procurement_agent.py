"""Procurement Agent: recommends purchase orders and enforces human approval."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from backend import procurement as proc_backend
from config import DEFAULT_BUDGET_LIMIT, PROCUREMENT_DISCLAIMER

DbPath = Optional[Union[str, Path]]


class ProcurementAgent:
    """Specialized agent responsible for purchase recommendations and approval workflows."""

    name = "Procurement Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path

    def recommend_orders(
        self,
        budget_limit: Optional[float] = DEFAULT_BUDGET_LIMIT,
        horizon_days: int = 7,
    ) -> dict:
        recs = proc_backend.generate_purchase_recommendations(
            budget_limit=budget_limit,
            horizon_days=horizon_days,
            db_path=self.db_path,
        )
        priced_costs = [r["estimated_total_cost"] for r in recs if r["estimated_total_cost"] is not None]
        total_est = round(sum(priced_costs), 2) if priced_costs else None
        in_budget_count = sum(1 for r in recs if r["within_budget"])
        return {
            "agent": self.name,
            "count": len(recs),
            "within_budget_count": in_budget_count,
            "total_estimated_cost": total_est,
            "has_pricing": bool(priced_costs),
            "budget_limit": budget_limit,
            "recommendations": recs,
            "policy": PROCUREMENT_DISCLAIMER,
        }

    def draft_purchase_order(
        self,
        product_id: str,
        supplier_id: Optional[str],
        quantity: int,
        reason: str = "",
        created_by: str = "Procurement Agent",
        budget_limit: Optional[float] = None,
    ) -> dict:
        return proc_backend.create_purchase_order(
            product_id=product_id,
            supplier_id=supplier_id,
            quantity=quantity,
            reason=reason,
            created_by=created_by,
            budget_limit=budget_limit,
            db_path=self.db_path,
        )

    def approve_order(self, order_id: str, actor: str = "Store Owner", note: str = "") -> dict:
        return proc_backend.approve_purchase_order(
            order_id=order_id, actor=actor, note=note, db_path=self.db_path
        )

    def reject_order(self, order_id: str, actor: str = "Store Owner", note: str = "") -> dict:
        return proc_backend.reject_purchase_order(
            order_id=order_id, actor=actor, note=note, db_path=self.db_path
        )

    def modify_order(
        self,
        order_id: str,
        new_quantity: Optional[int] = None,
        new_supplier_id: Optional[str] = None,
        budget_limit: Optional[float] = None,
        actor: str = "Store Owner",
        note: str = "",
    ) -> dict:
        return proc_backend.modify_purchase_order(
            order_id=order_id,
            new_quantity=new_quantity,
            new_supplier_id=new_supplier_id,
            budget_limit=budget_limit,
            actor=actor,
            note=note,
            db_path=self.db_path,
        )

    def list_orders(self, status_filter: Optional[str] = None) -> pd.DataFrame:
        return proc_backend.get_purchase_orders(status_filter=status_filter, db_path=self.db_path)

    def list_audit_logs(self, order_id: Optional[str] = None, limit: int = 200) -> pd.DataFrame:
        return proc_backend.get_audit_logs(order_id=order_id, limit=limit, db_path=self.db_path)
