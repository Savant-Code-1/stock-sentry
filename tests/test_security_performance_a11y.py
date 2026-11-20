"""Security, Performance Regression, Edge-Case, and WCAG 2.2 AA Accessibility tests."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from agents.supervisor_agent import SupervisorAgent
from backend import anomaly_detection, data_ingestion as ingest, forecasting, inventory, procurement, reports
from database import db as db_mod
from database.seed import seed_database_if_empty
from utils.helpers import (
    STATUS_COLOR_MAP,
    STATUS_TEXT_PREFIX,
    escape_html,
    format_inr,
    sanitize_csv_cell,
    status_color,
    status_prefix,
)
from utils.validators import ValidationError


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1, l2 = _relative_luminance(fg_hex), _relative_luminance(bg_hex)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class TestSecurityAndInputHardening(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "sec_test.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_html_xss_escaping(self) -> None:
        payload = '<img src=x onerror="alert(1)"><script>steal()</script>'
        escaped = escape_html(payload)
        self.assertNotIn("<script>", escaped)
        self.assertNotIn("<img", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&quot;", escaped)

    def test_csv_formula_injection_sanitization(self) -> None:
        df = pd.DataFrame(
            {
                "product_name": ["=CMD|' /C calc'!A0", "+2+3", "-10+20", "@SUM(A1:A5)", "Normal Rice"],
                "cost_price": [100.0, 200.0, 300.0, 400.0, 500.0],
            }
        )
        csv_text = reports.dataframe_to_csv_bytes(df).decode("utf-8-sig")
        self.assertIn("'=CMD|", csv_text)
        self.assertIn("'+2+3", csv_text)
        self.assertIn("'-10+20", csv_text)
        self.assertIn("'@SUM(A1:A5)", csv_text)
        self.assertIn("Normal Rice", csv_text)
        # Numeric values remain unquoted numbers
        self.assertEqual(sanitize_csv_cell(123.45), 123.45)

    def test_sql_injection_payloads_safely_parameterized(self) -> None:
        sqli = "'; DROP TABLE products; --"
        # Search should return 0 rows and NEVER drop table
        res_df = inventory.get_all_products(db_path=self.db_path, search=sqli)
        self.assertEqual(len(res_df), 0)
        self.assertEqual(len(inventory.get_all_products(db_path=self.db_path)), 55)

        # Adding a product with SQL-like name stores literally and safely
        created = inventory.add_product(
            product_id="P998",
            product_name="Rice ' OR '1'='1",
            category="Grocery",
            current_stock=10,
            unit="pack",
            selling_price=120.0,
            cost_price=100.0,
            reorder_point=5,
            target_stock=20,
            db_path=self.db_path,
        )
        self.assertEqual(created["product_name"], "Rice ' OR '1'='1")
        self.assertEqual(len(inventory.get_all_products(db_path=self.db_path)), 56)

    def test_upload_size_limit_and_path_traversal_filename_sanitization(self) -> None:
        # Filename with path traversal is stripped to basename only
        csv_bytes = b"SKU,Product Name,Stock\nA1,Milk,12\n"
        bundle = ingest.read_uploaded_file(csv_bytes, filename="../../etc/evil_shop.csv")
        self.assertEqual(bundle["file_name"], "evil_shop.csv")

        # Oversized file rejected cleanly
        orig_limit = ingest.MAX_UPLOAD_FILE_SIZE_BYTES
        try:
            ingest.MAX_UPLOAD_FILE_SIZE_BYTES = 20  # tiny 20-byte test cap
            with self.assertRaises(ingest.DataIngestionError):
                ingest.read_uploaded_file(b"SKU,Product Name,Stock\nA1,Very Long Product Name,12\n", filename="big.csv")
        finally:
            ingest.MAX_UPLOAD_FILE_SIZE_BYTES = orig_limit

    def test_zero_and_negative_validation_edge_cases(self) -> None:
        with self.assertRaises(ValidationError):
            inventory.update_stock("P001", "SALE", 0, "Zero sale", db_path=self.db_path)
        with self.assertRaises(ValidationError):
            inventory.update_stock("P001", "RESTOCK", -5, "Negative restock", db_path=self.db_path)
        with self.assertRaises(ValidationError):
            inventory.add_product("P997", "Bad Price", "Grocery", 5, "pack", -10.0, 50.0, 2, 10, db_path=self.db_path)
        with self.assertRaises(ValidationError):
            procurement.create_purchase_order("P001", "SUP01", 0, db_path=self.db_path)

    def test_format_inr_handles_nan_inf_and_invalid_strings(self) -> None:
        self.assertEqual(format_inr(float("nan")), "N/A")
        self.assertEqual(format_inr(float("inf")), "N/A")
        self.assertEqual(format_inr("not-a-number"), "N/A")
        self.assertEqual(format_inr(-12500.5), "-₹12,500.50")

    def test_database_failure_raises_clean_sqlite_error(self) -> None:
        # Attempting to open a directory path as a SQLite file must raise OperationalError cleanly
        with self.assertRaises(sqlite3.OperationalError):
            db_mod.init_db(db_path=Path(self._tmpdir.name))

    def test_negative_margin_anomaly_and_clean_inr_formatting(self) -> None:
        """Verify negative margin pricing inversion is detected with clean ₹ formatting (no mojibake)."""
        inventory.add_product(
            product_id="P991",
            product_name="Loss Leader Soap",
            category="Personal Care",
            current_stock=15,
            unit="bar",
            selling_price=40.0,
            cost_price=55.0,
            reorder_point=5,
            target_stock=25,
            db_path=self.db_path,
        )
        anoms = anomaly_detection.detect_anomalies(db_path=self.db_path)
        price_anoms = [a for a in anoms if a["product_id"] == "P991" and a["detected_event"] == "Selling Price Below Cost Price"]
        self.assertEqual(len(price_anoms), 1)
        self.assertIn("₹55.00", price_anoms[0]["expected_value"])
        self.assertIn("₹40.00", price_anoms[0]["actual_value"])
        self.assertNotIn("float(cp)", price_anoms[0]["explanation"])

    def test_zero_sales_velocity_forecast_edge_case(self) -> None:
        """Product with zero historical sales must not crash or divide by zero."""
        inventory.add_product(
            product_id="P992",
            product_name="Slow Moving Spice Jar",
            category="Grocery",
            current_stock=20,
            unit="jar",
            selling_price=150.0,
            cost_price=120.0,
            reorder_point=5,
            target_stock=25,
            db_path=self.db_path,
        )
        fc = forecasting.forecast_product("P992", horizon_days=7, db_path=self.db_path)
        self.assertTrue(fc["forecast_available"])
        self.assertEqual(fc["avg_daily_sales"], 0.0)
        self.assertIsNone(fc["days_remaining"])
        self.assertEqual(fc["stockout_risk"], "Low")


class TestPerformanceAndScalability(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "perf_test.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_batched_forecast_matches_single_sku_forecast_exactly(self) -> None:
        fc_all = forecasting.forecast_all_products(horizon_days=7, window_days=14, method="ema", db_path=self.db_path)
        for pid in ["P001", "P002", "P007", "P015", "P040"]:
            single = forecasting.forecast_product(pid, horizon_days=7, window_days=14, method="ema", db_path=self.db_path)
            row = fc_all[fc_all["product_id"] == pid].iloc[0]
            self.assertEqual(single["avg_daily_sales"], row["avg_daily_sales"])
            self.assertEqual(single["forecast_daily_demand"], row["forecast_daily_demand"])
            self.assertEqual(single["calculated_reorder_point"], row["calculated_reorder_point"])
            self.assertEqual(single["stockout_risk"], row["stockout_risk"])

    def test_sql_query_budget_regression_guard(self) -> None:
        """Ensure N+1 queries never return: full store health check must stay <= 20 SQL queries."""
        counts = {"queries": 0}
        orig_get = db_mod.get_connection

        @contextmanager
        def counting_conn(db_path=None):
            with orig_get(db_path) as conn:
                def _cb(sql):
                    if not sql.strip().upper().startswith("PRAGMA"):
                        counts["queries"] += 1
                conn.set_trace_callback(_cb)
                yield conn

        orig_refs = (forecasting.get_connection, anomaly_detection.get_connection, procurement.get_connection)
        try:
            db_mod.get_connection = counting_conn
            forecasting.get_connection = counting_conn
            anomaly_detection.get_connection = counting_conn
            procurement.get_connection = counting_conn

            sup = SupervisorAgent(db_path=self.db_path)
            health = sup.run_store_health_check()
            self.assertEqual(health["inventory_summary"]["total_products"], 55)
            # Previously 582 SQL queries; must remain <= 20!
            self.assertLessEqual(counts["queries"], 20, f"N+1 regression detected: {counts['queries']} SQL queries!")
        finally:
            db_mod.get_connection = orig_get
            forecasting.get_connection, anomaly_detection.get_connection, procurement.get_connection = orig_refs

    def test_large_dataset_500_skus_5000_rows_performance(self) -> None:
        """Stress-test ingestion + all 5 agents on a 500-SKU, 5,000-row dataset."""
        rng = np.random.default_rng(42)
        n_skus = 500
        dates = pd.date_range("2026-10-01", periods=10, freq="D")
        rows = []
        for d_idx, dt in enumerate(dates):
            dt_s = dt.strftime("%Y-%m-%d")
            for i in range(n_skus):
                rows.append(
                    {
                        "SKU": f"L-{i:04d}",
                        "Product Name": f"Retail Item {i}",
                        "Category": f"Cat-{i % 6}",
                        "Stock": int(i % 45),
                        "Unit Cost": 50.0 + (i % 20) * 5.0,
                        "Selling Price": 65.0 + (i % 20) * 6.0,
                        "Min Stock": 12,
                        "Max Stock": 35,
                        "Order Date": dt_s,
                        "Units Sold": int(rng.integers(1, 9)),
                        "Vendor": f"Vendor-{(i + d_idx) % 5}",
                        "Vendor Price": 48.0 + (i % 20) * 5.0,
                        "Lead Time Days": 2 + (d_idx % 3),
                        "Reliability": 88.0 + (d_idx % 10),
                    }
                )
        big_df = pd.DataFrame(rows)
        t0 = time.perf_counter()
        mapping = ingest.suggest_column_mapping(list(big_df.columns))
        val = ingest.validate_and_preprocess_dataset(big_df, mapping)
        self.assertTrue(val["valid"])
        load_res = ingest.create_isolated_session_db(val["cleaned_df"], val["capabilities"], "stress_500.csv")
        try:
            sup = SupervisorAgent(db_path=load_res["db_path"])
            health = sup.run_store_health_check()
            elapsed = time.perf_counter() - t0
            self.assertEqual(health["inventory_summary"]["total_products"], 500)
            self.assertTrue(health["forecast_ready"])
            # Must complete full 5,000-row ingest + all 5 agents comfortably under 15 seconds
            self.assertLess(elapsed, 15.0, f"500-SKU stress test took too long: {elapsed:.2f}s")
        finally:
            ingest.cleanup_session_temp_dir(load_res["temp_dir"])


class TestAccessibilityWCAGCompliance(unittest.TestCase):
    def test_all_status_colors_meet_wcag_22_aa_contrast(self) -> None:
        """Every status badge background must achieve >= 4.5:1 contrast with #FFFFFF text."""
        for status_name, bg_hex in STATUS_COLOR_MAP.items():
            ratio = _wcag_contrast_ratio("#FFFFFF", bg_hex)
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"WCAG 2.2 AA contrast failure for '{status_name}' ({bg_hex}): {ratio:.2f}:1 < 4.5:1",
            )

    def test_every_status_has_non_color_symbol_prefix(self) -> None:
        """Status must never rely on color alone (WCAG 1.4.1)."""
        for status_name in STATUS_COLOR_MAP:
            prefix = status_prefix(status_name)
            self.assertTrue(prefix.strip(), f"Missing non-color prefix for status '{status_name}'")
            self.assertIn(status_name, STATUS_TEXT_PREFIX)
            self.assertTrue(status_color(status_name).startswith("#"))

    def test_ui_components_include_semantic_landmarks_focus_and_xss_escaping(self) -> None:
        """Verify WCAG 2.2 AA landmarks, skip-link, focus-visible, reduced-motion, and XSS escaping."""
        base = Path(__file__).resolve().parent.parent
        comp_src = (base / "dashboard" / "components.py").read_text(encoding="utf-8")
        app_src = (base / "app.py").read_text(encoding="utf-8")
        self.assertIn("skip-link", comp_src)
        self.assertIn(":focus-visible", comp_src)
        self.assertIn("prefers-reduced-motion", comp_src)
        self.assertIn('role="banner"', comp_src)
        self.assertIn("<h1>", comp_src)
        self.assertIn('role="status"', comp_src)
        self.assertIn("escape_html(", comp_src)
        self.assertIn("escape_html(", app_src)
        self.assertIn("@st.cache_data", app_src)


if __name__ == "__main__":
    unittest.main()
