"""Tests for supplier management and transparent weighted supplier ranking."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.supplier_agent import SupplierAgent
from backend import inventory, supplier
from database.seed import seed_database_if_empty


class TestSupplier(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_sup.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_ranking_prefers_better_price_speed_reliability_and_stock(self) -> None:
        inventory.add_product(
            product_id="P900",
            product_name="Test Basmati 1kg",
            category="Grocery",
            current_stock=5,
            unit="pack",
            selling_price=150.0,
            cost_price=120.0,
            reorder_point=10,
            target_stock=30,
            db_path=self.db_path,
        )
        # Strong supplier
        supplier.add_or_update_supplier_offer(
            "SUP-BEST", "Best Wholesale", "P900", 110.0, 1, 100, 96.0, 5, db_path=self.db_path
        )
        # Expensive, slow, low-reliability supplier
        supplier.add_or_update_supplier_offer(
            "SUP-WEAK", "Slow Expensive", "P900", 140.0, 5, 100, 75.0, 5, db_path=self.db_path
        )
        # Out-of-stock supplier
        supplier.add_or_update_supplier_offer(
            "SUP-OOS", "Empty Supplier", "P900", 100.0, 1, 0, 99.0, 1, db_path=self.db_path
        )

        ranked = supplier.rank_suppliers_for_product("P900", required_qty=20, db_path=self.db_path)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked.iloc[0]["supplier_id"], "SUP-BEST")
        self.assertEqual(ranked.iloc[-1]["supplier_id"], "SUP-OOS")
        self.assertIn("Recommended (#1)", ranked.iloc[0]["recommendation_reason"])

    def test_keyword_lookup_for_cooking_oil(self) -> None:
        agent = SupplierAgent(db_path=self.db_path)
        matches = agent.find_best_suppliers_by_keyword("Which supplier is best for cooking oil?")
        self.assertGreater(len(matches), 0)
        self.assertIn("Oil", matches[0]["product_name"])


if __name__ == "__main__":
    unittest.main()
