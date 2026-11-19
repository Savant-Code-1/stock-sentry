"""Supplier Agent: manages supplier offers and ranks suppliers transparently."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from backend import inventory as inv_backend
from backend import supplier as sup_backend

DbPath = Optional[Union[str, Path]]


class SupplierAgent:
    """Specialized agent responsible for supplier comparison and ranking."""

    name = "Supplier Comparison Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path

    def list_suppliers(
        self,
        product_id: Optional[str] = None,
        supplier_name: Optional[str] = None,
    ) -> pd.DataFrame:
        return sup_backend.get_all_suppliers(
            product_id=product_id,
            supplier_name=supplier_name,
            db_path=self.db_path,
        )

    def get_supplier_overview(self) -> pd.DataFrame:
        return sup_backend.get_supplier_overview(db_path=self.db_path)

    def compare_suppliers(
        self,
        product_id: str,
        required_qty: int = 10,
        weights: Optional[dict] = None,
    ) -> dict:
        ranked = sup_backend.rank_suppliers_for_product(
            product_id=product_id,
            required_qty=required_qty,
            weights=weights,
            db_path=self.db_path,
        )
        top_supplier = ranked.iloc[0].to_dict() if not ranked.empty else None
        scored_criteria = ranked.iloc[0]["scored_criteria"] if not ranked.empty else "None"
        return {
            "agent": self.name,
            "product_id": product_id,
            "required_qty": required_qty,
            "ranked_table": ranked,
            "top_supplier": top_supplier,
            "scored_criteria": scored_criteria,
        }

    def find_best_suppliers_by_keyword(self, keyword: str, required_qty: int = 10) -> list[dict]:
        """Match products by keyword (e.g., 'cooking oil', 'atta', 'tea') and rank suppliers."""
        products_df = inv_backend.get_all_products(db_path=self.db_path)
        if products_df.empty:
            return []

        kw = keyword.lower().strip()
        synonyms = {
            "cooking oil": ["oil", "sunflower", "mustard"],
            "edible oil": ["oil", "sunflower", "mustard"],
            "flour": ["atta", "besan"],
            "biscuit": ["parle", "good day", "marie", "hide & seek"],
            "biscuits": ["parle", "good day", "marie", "hide & seek"],
            "soap": ["dove", "lifebuoy", "bar"],
            "detergent": ["surf", "rin"],
            "tea": ["tea", "tata tea", "red label"],
            "milk": ["milk", "amul taaza"],
            "rice": ["rice", "basmati"],
            "noodles": ["maggi", "noodles"],
        }
        tokens = []
        for phrase, mapped in synonyms.items():
            if phrase in kw:
                tokens.extend(mapped)
        if not tokens:
            stop = {"which", "supplier", "is", "best", "for", "the", "a", "an", "to", "buy", "of", "get", "me"}
            tokens = [t for t in kw.replace("?", "").split() if t and t not in stop]

        matched_rows = []
        for _, prod in products_df.iterrows():
            haystack = f"{prod['product_name']} {prod['category']} {prod['product_id']}".lower()
            if any(tok in haystack for tok in tokens):
                matched_rows.append(prod)

        results = []
        for prod in matched_rows[:4]:
            comp = self.compare_suppliers(prod["product_id"], required_qty=required_qty)
            if comp["top_supplier"]:
                results.append(
                    {
                        "product_id": prod["product_id"],
                        "product_name": prod["product_name"],
                        "top_supplier": comp["top_supplier"],
                        "ranked_table": comp["ranked_table"],
                    }
                )
        return results

    def upsert_supplier(self, **kwargs) -> dict:
        return sup_backend.add_or_update_supplier_offer(db_path=self.db_path, **kwargs)

    def edit_supplier(self, **kwargs) -> dict:
        return sup_backend.update_supplier_offer_by_id(db_path=self.db_path, **kwargs)
