"""Inventory Agent: monitors stock levels, valuations, overstock, sales analytics, and stock movements."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from backend import inventory as inv_backend

DbPath = Optional[Union[str, Path]]


class InventoryAgent:
    """Specialized agent responsible for inventory monitoring and stock updates."""

    name = "Inventory Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path

    def get_overview(self) -> dict:
        summary = inv_backend.get_inventory_summary(db_path=self.db_path)
        return {
            "agent": self.name,
            "summary": summary,
        }

    def list_products(
        self,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> pd.DataFrame:
        return inv_backend.get_all_products(
            db_path=self.db_path,
            search=search,
            category=category,
            status_filter=status_filter,
        )

    def get_low_stock_alerts(self, include_out_of_stock: bool = True) -> dict:
        df = inv_backend.get_low_stock_products(
            db_path=self.db_path,
            include_out_of_stock=include_out_of_stock,
        )
        return {
            "agent": self.name,
            "count": int(len(df)),
            "products": df,
        }

    def get_out_of_stock_alerts(self) -> dict:
        df = inv_backend.get_out_of_stock_products(db_path=self.db_path)
        return {
            "agent": self.name,
            "count": int(len(df)),
            "products": df,
        }

    def get_overstock_alerts(self) -> dict:
        df = inv_backend.get_overstock_products(db_path=self.db_path)
        return {
            "agent": self.name,
            "count": int(len(df)),
            "products": df,
        }

    def get_sales_analytics(self) -> dict:
        analytics = inv_backend.get_sales_analytics(db_path=self.db_path)
        analytics["agent"] = self.name
        return analytics

    def add_product(self, **kwargs) -> dict:
        return inv_backend.add_product(db_path=self.db_path, **kwargs)

    def edit_product(self, **kwargs) -> dict:
        return inv_backend.update_product(db_path=self.db_path, **kwargs)

    def record_stock_change(
        self,
        product_id: str,
        movement_type: str,
        quantity: int,
        remarks: str = "",
    ) -> dict:
        return inv_backend.update_stock(
            product_id=product_id,
            movement_type=movement_type,
            quantity=quantity,
            remarks=remarks,
            db_path=self.db_path,
        )

    def get_movements(self, product_id: Optional[str] = None, limit: int = 200) -> pd.DataFrame:
        return inv_backend.get_stock_movements(
            product_id=product_id,
            limit=limit,
            db_path=self.db_path,
        )
