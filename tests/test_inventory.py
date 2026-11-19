"""Unit and integration tests for inventory calculations, low-stock detection, and DB seeding."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend import inventory
from database.seed import seed_database_if_empty
from utils.helpers import format_inr
from utils.validators import ValidationError


class TestInventory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_inv.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_idempotent_seeding_preserves_data(self) -> None:
        second = seed_database_if_empty(db_path=self.db_path)
        self.assertFalse(second["seeded"])
        df = inventory.get_all_products(db_path=self.db_path)
        self.assertEqual(len(df), 55)

    def test_inventory_summary_calculations(self) -> None:
        summary = inventory.get_inventory_summary(db_path=self.db_path)
        df = inventory.get_all_products(db_path=self.db_path)
        expected_cost = round(float((df["current_stock"] * df["cost_price"]).sum()), 2)
        expected_retail = round(float((df["current_stock"] * df["selling_price"]).sum()), 2)
        self.assertEqual(summary["total_products"], 55)
        self.assertAlmostEqual(summary["total_cost_value"], expected_cost, places=2)
        self.assertAlmostEqual(summary["total_retail_value"], expected_retail, places=2)
        self.assertGreater(summary["low_stock_count"], 0)
        self.assertGreater(summary["out_of_stock_count"], 0)

    def test_low_stock_and_out_of_stock_detection(self) -> None:
        low_df = inventory.get_low_stock_products(db_path=self.db_path, include_out_of_stock=True)
        oos_df = inventory.get_out_of_stock_products(db_path=self.db_path)
        self.assertTrue((oos_df["current_stock"] == 0).all())
        self.assertTrue((low_df["current_stock"] <= low_df["reorder_point"]).all())
        self.assertGreaterEqual(len(low_df), len(oos_df))

    def test_add_edit_and_update_stock(self) -> None:
        created = inventory.add_product(
            product_id="P999",
            product_name="Test Organic Honey 500g",
            category="Grocery",
            current_stock=20,
            unit="jar",
            selling_price=320.0,
            cost_price=260.0,
            reorder_point=8,
            target_stock=30,
            db_path=self.db_path,
        )
        self.assertEqual(created["current_stock"], 20)
        self.assertEqual(created["inventory_value_cost"], 5200.0)

        # Restock +10
        res = inventory.update_stock("P999", "RESTOCK", 10, "Test delivery", db_path=self.db_path)
        self.assertEqual(res["new_stock"], 30)

        # Sale -5
        res2 = inventory.update_stock("P999", "SALE", 5, "Counter sale", db_path=self.db_path)
        self.assertEqual(res2["new_stock"], 25)

        # Cannot reduce below zero
        with self.assertRaises(ValidationError):
            inventory.update_stock("P999", "SALE", 100, "Over-sale", db_path=self.db_path)

        # Edit product metadata
        edited = inventory.update_product(
            product_id="P999",
            product_name="Test Organic Honey 500g (Updated)",
            category="Grocery",
            unit="jar",
            selling_price=340.0,
            cost_price=270.0,
            reorder_point=10,
            target_stock=35,
            db_path=self.db_path,
        )
        self.assertEqual(edited["selling_price"], 340.0)

    def test_inr_formatting(self) -> None:
        self.assertEqual(format_inr(125450.5), "₹1,25,450.50")
        self.assertEqual(format_inr(850), "₹850.00")


if __name__ == "__main__":
    unittest.main()
