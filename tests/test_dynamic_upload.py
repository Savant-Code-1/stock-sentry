"""Comprehensive tests for dynamic CSV/Excel upload, column mapping, data quality,
all 5 agents on user data, missing-column honesty, dataset replacement, and reports.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

import pandas as pd

from agents.supervisor_agent import SupervisorAgent
from backend import data_ingestion as ingest
from backend import reports as report_backend


class TestDynamicUploadPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dirs: list[str] = []

    def tearDown(self) -> None:
        for td in self._temp_dirs:
            ingest.cleanup_session_temp_dir(td)

    def _build_rich_business_df(self) -> pd.DataFrame:
        """Custom business table with non-standard column names, multi-day sales,
        anomalies, overstock, low stock, missing values, and an exact duplicate row.
        """
        rows = []
        dates = pd.date_range("2026-10-01", periods=10, freq="D")
        for idx, dt in enumerate(dates):
            # SKU-A: Low stock, steady sales + huge spike on last day, 2 competing vendors
            rows.append(
                {
                    "Item Code": "ITEM-101",
                    "Item Name": "Premium Basmati 10kg",
                    "Department": "Staples",
                    "Available Units": 8,
                    "UOM": "bag",
                    "Purchase Price": "₹920",
                    "MRP": "₹1,080",
                    "Min Stock": 15,
                    "Max Stock": 50,
                    "Sales": 40 if idx == 9 else 4,
                    "Order Date": dt.strftime("%Y-%m-%d"),
                    "Vendor": "Alpha Agro" if idx % 2 == 0 else "Beta Wholesale",
                    "Vendor Price": 900 if idx % 2 == 0 else 940,
                    "Lead Time Days": 2 if idx % 2 == 0 else 4,
                    "Vendor Rating": 0.94 if idx % 2 == 0 else 0.85,
                    "Vendor Stock": 120,
                    "MOQ": 5,
                }
            )
            # SKU-B: Extreme overstock (120 vs Max Stock 50), steady sales
            rows.append(
                {
                    "Item Code": "ITEM-102",
                    "Item Name": "Cold Pressed Groundnut Oil 1L",
                    "Department": "Oils",
                    "Available Units": 120,
                    "UOM": "bottle",
                    "Purchase Price": "₹240",
                    "MRP": "₹290",
                    "Min Stock": 12,
                    "Max Stock": 50,
                    "Sales": 3,
                    "Order Date": dt.strftime("%Y-%m-%d"),
                    "Vendor": "Alpha Agro",
                    "Vendor Price": 235,
                    "Lead Time Days": 2,
                    "Vendor Rating": 0.94,
                    "Vendor Stock": 80,
                    "MOQ": 6,
                }
            )
        # Add an exact duplicate row + a row with missing numeric cell
        rows.append(dict(rows[0]))
        rows.append(
            {
                "Item Code": "ITEM-103",
                "Item Name": "Organic Jaggery 1kg",
                "Department": "Staples",
                "Available Units": 0,
                "UOM": "pack",
                "Purchase Price": None,
                "MRP": "₹110",
                "Min Stock": 10,
                "Max Stock": 30,
                "Sales": 2,
                "Order Date": "2026-10-10",
                "Vendor": "Gamma Foods",
                "Vendor Price": 85,
                "Lead Time Days": 3,
                "Vendor Rating": 0.90,
                "Vendor Stock": 50,
                "MOQ": 5,
            }
        )
        return pd.DataFrame(rows)

    def test_csv_upload_column_mapping_and_all_five_agents(self) -> None:
        df = self._build_rich_business_df()
        csv_bytes = df.to_csv(index=False).encode("utf-8")

        bundle = ingest.read_uploaded_file(csv_bytes, filename="my_shop_inventory.csv")
        self.assertEqual(bundle["file_type"], "CSV")
        self.assertEqual(len(bundle["raw_df"]), len(df))

        quality = ingest.analyze_dataframe_quality(bundle["raw_df"])
        self.assertEqual(quality["duplicate_rows_count"], 1)
        self.assertGreater(quality["total_missing_cells"], 0)

        # Auto-detect mapping across non-standard business headers
        auto_map = ingest.suggest_column_mapping(list(bundle["raw_df"].columns))
        self.assertEqual(auto_map["product_id"], "Item Code")
        self.assertEqual(auto_map["product_name"], "Item Name")
        self.assertEqual(auto_map["current_stock"], "Available Units")
        self.assertEqual(auto_map["units_sold"], "Sales")
        self.assertEqual(auto_map["cost_price"], "Purchase Price")
        self.assertEqual(auto_map["supplier_name"], "Vendor")
        self.assertEqual(auto_map["transaction_date"], "Order Date")

        val = ingest.validate_and_preprocess_dataset(bundle["raw_df"], auto_map)
        self.assertTrue(val["valid"])
        self.assertTrue(val["capabilities"]["demand_forecasting"]["available"])
        self.assertTrue(val["capabilities"]["supplier_analysis"]["available"])
        self.assertTrue(val["capabilities"]["overstock_detection"]["available"])

        load_res = ingest.create_isolated_session_db(
            cleaned_df=val["cleaned_df"],
            capabilities=val["capabilities"],
            source_filename=bundle["file_name"],
        )
        self._temp_dirs.append(load_res["temp_dir"])
        self.assertEqual(load_res["products_loaded"], 3)

        # Connect ALL FIVE existing agents via SupervisorAgent on isolated user DB
        sup = SupervisorAgent(db_path=load_res["db_path"])

        # 1. Inventory Agent (incl. low-stock, out-of-stock, overstock, sales analytics)
        inv_ov = sup.inventory_agent.get_overview()["summary"]
        self.assertEqual(inv_ov["total_products"], 3)
        self.assertEqual(inv_ov["out_of_stock_count"], 1)  # ITEM-103
        self.assertEqual(inv_ov["low_stock_count"], 1)     # ITEM-101
        self.assertEqual(inv_ov["overstock_count"], 1)     # ITEM-102
        sales_an = sup.inventory_agent.get_sales_analytics()
        self.assertTrue(sales_an["available"])
        self.assertGreater(sales_an["total_units_sold"], 0)

        # 2. Demand Forecasting Agent
        self.assertTrue(sup.forecasting_agent.check_readiness()["ready"])
        fc = sup.forecasting_agent.forecast_sku("ITEM-101", horizon_days=7)
        self.assertTrue(fc["forecast_available"])
        self.assertGreater(fc["forecast_daily_demand"], 0)

        # 3. Anomaly Detection Agent (detects sales spike on ITEM-101 & extreme overstock on ITEM-102)
        anom_scan = sup.anomaly_agent.scan_anomalies()
        events = {a["detected_event"] for a in anom_scan["anomalies"]}
        self.assertIn("Unusually High Sales Spike", events)
        self.assertIn("Extreme Overstock Deviation", events)

        # 4. Supplier Analysis Agent (Alpha Agro beats Beta Wholesale on price, speed, and rating)
        comp = sup.supplier_agent.compare_suppliers("ITEM-101", required_qty=30)
        self.assertEqual(comp["top_supplier"]["supplier_name"], "Alpha Agro")

        # 5. Procurement Agent (recommends restocking ITEM-101 and ITEM-103, NOT overstocked ITEM-102)
        recs = sup.procurement_agent.recommend_orders()
        rec_ids = {r["product_id"] for r in recs["recommendations"]}
        self.assertIn("ITEM-101", rec_ids)
        self.assertIn("ITEM-103", rec_ids)
        self.assertNotIn("ITEM-102", rec_ids)

        # Downloadable Executive Report & CSV generation
        md_report = report_backend.build_executive_markdown_report(
            sup, dataset_label="my_shop_inventory.csv", quality_report=quality, capabilities=val["capabilities"]
        )
        self.assertIn("my_shop_inventory.csv", md_report)
        self.assertIn("Overstock Products:** 1", md_report)
        csv_export = report_backend.dataframe_to_csv_bytes(sup.inventory_agent.list_products())
        self.assertGreater(len(csv_export), 50)

    def test_excel_xlsx_multisheet_upload(self) -> None:
        df1 = pd.DataFrame({"Notice": ["Summary sheet only"]})
        df2 = pd.DataFrame(
            {
                "SKU": ["X1", "X2"],
                "Product": ["Steel Bottle 1L", "Lunch Box"],
                "Stock": [5, 40],
                "Unit Cost": [180.0, 220.0],
                "Min Stock": [10, 15],
                "Max Stock": [30, 35],
            }
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df1.to_excel(writer, sheet_name="Readme", index=False)
            df2.to_excel(writer, sheet_name="Inventory", index=False)
        xlsx_bytes = buf.getvalue()

        sheets = ingest.list_excel_sheets(xlsx_bytes, filename="warehouse.xlsx")
        self.assertEqual(sheets, ["Readme", "Inventory"])

        bundle = ingest.read_uploaded_file(xlsx_bytes, filename="warehouse.xlsx", sheet_name="Inventory")
        self.assertEqual(bundle["file_type"], "XLSX")
        self.assertEqual(bundle["selected_sheet"], "Inventory")
        self.assertEqual(len(bundle["raw_df"]), 2)

        mapping = ingest.suggest_column_mapping(list(bundle["raw_df"].columns))
        val = ingest.validate_and_preprocess_dataset(bundle["raw_df"], mapping)
        self.assertTrue(val["valid"])
        load_res = ingest.create_isolated_session_db(val["cleaned_df"], val["capabilities"], "warehouse.xlsx")
        self._temp_dirs.append(load_res["temp_dir"])

        sup = SupervisorAgent(db_path=load_res["db_path"])
        inv = sup.inventory_agent.get_overview()["summary"]
        self.assertEqual(inv["total_products"], 2)
        self.assertEqual(inv["low_stock_count"], 1)   # X1
        self.assertEqual(inv["overstock_count"], 1)   # X2 (40 > 35)

    def test_missing_columns_never_fabricate_business_metrics(self) -> None:
        # Minimal CSV with ONLY item name and stock quantity (no prices, no dates, no sales, no suppliers)
        minimal_csv = b"Item Name,Inventory\nNotebook A4,25\nGel Pen Blue,0\n"
        bundle = ingest.read_uploaded_file(minimal_csv, filename="minimal.csv")
        auto_map = ingest.suggest_column_mapping(list(bundle["raw_df"].columns))
        val = ingest.validate_and_preprocess_dataset(bundle["raw_df"], auto_map)
        self.assertTrue(val["valid"])
        self.assertFalse(val["capabilities"]["inventory_valuation"]["available"])
        self.assertFalse(val["capabilities"]["demand_forecasting"]["available"])
        self.assertFalse(val["capabilities"]["supplier_analysis"]["available"])

        load_res = ingest.create_isolated_session_db(val["cleaned_df"], val["capabilities"], "minimal.csv")
        self._temp_dirs.append(load_res["temp_dir"])
        sup = SupervisorAgent(db_path=load_res["db_path"])

        inv = sup.inventory_agent.get_overview()["summary"]
        self.assertFalse(inv["has_cost_data"])
        self.assertIsNone(inv["total_cost_value"])
        self.assertFalse(inv["has_retail_data"])
        self.assertIsNone(inv["total_retail_value"])
        self.assertEqual(inv["out_of_stock_count"], 1)

        fc_ready = sup.forecasting_agent.check_readiness()
        self.assertFalse(fc_ready["ready"])
        self.assertTrue(sup.forecasting_agent.forecast_portfolio().empty)

        self.assertTrue(sup.supplier_agent.list_suppliers().empty)
        self.assertIn("unavailable", sup.answer_query("Which supplier is best for notebook?")["answer"].lower())
        self.assertIn("unavailable", sup.answer_query("Which products may run out in the next 5 days?")["answer"].lower())

    def test_invalid_and_empty_files_rejected_cleanly(self) -> None:
        with self.assertRaises(ingest.DataIngestionError):
            ingest.read_uploaded_file(b"hello world", filename="notes.txt")

        with self.assertRaises(ingest.DataIngestionError):
            ingest.read_uploaded_file(b"", filename="empty.csv")

        with self.assertRaises(ingest.DataIngestionError):
            ingest.read_uploaded_file(b"SKU,Name,Stock\n", filename="headers_only.csv")

        with self.assertRaises(ingest.DataIngestionError):
            ingest.read_uploaded_file(b"not-a-real-xlsx", filename="broken.xlsx")

        # Missing required identifier mapping returns valid=False with clear error
        df = pd.DataFrame({"ColA": [1, 2], "ColB": [3, 4]})
        res = ingest.validate_and_preprocess_dataset(df, {"current_stock": "ColA"})
        self.assertFalse(res["valid"])
        self.assertGreater(len(res["errors"]), 0)

    def test_dataset_replacement_isolates_and_clears_previous_analysis(self) -> None:
        csv_a = b"SKU,Product Name,Stock,Unit Cost\nA1,Coffee Beans,10,500\n"
        b_a = ingest.read_uploaded_file(csv_a, filename="store_a.csv")
        v_a = ingest.validate_and_preprocess_dataset(b_a["raw_df"], ingest.suggest_column_mapping(list(b_a["raw_df"].columns)))
        load_a = ingest.create_isolated_session_db(v_a["cleaned_df"], v_a["capabilities"], "store_a.csv")
        self._temp_dirs.append(load_a["temp_dir"])

        sup_a = SupervisorAgent(db_path=load_a["db_path"])
        self.assertEqual(list(sup_a.inventory_agent.list_products()["product_name"]), ["Coffee Beans"])

        # Replace with Dataset B, passing previous_temp_dir for cleanup
        csv_b = b"SKU,Product Name,Stock,Unit Cost\nB1,Green Tea,40,150\nB2,Black Pepper,15,200\n"
        b_b = ingest.read_uploaded_file(csv_b, filename="store_b.csv")
        v_b = ingest.validate_and_preprocess_dataset(b_b["raw_df"], ingest.suggest_column_mapping(list(b_b["raw_df"].columns)))
        load_b = ingest.create_isolated_session_db(
            v_b["cleaned_df"], v_b["capabilities"], "store_b.csv", previous_temp_dir=load_a["temp_dir"]
        )
        self._temp_dirs.append(load_b["temp_dir"])

        # Verify previous session temp directory was removed & zero Dataset A records leak into Dataset B
        self.assertFalse(Path(load_a["temp_dir"]).exists())
        sup_b = SupervisorAgent(db_path=load_b["db_path"])
        names_b = set(sup_b.inventory_agent.list_products()["product_name"])
        self.assertEqual(names_b, {"Green Tea", "Black Pepper"})
        self.assertNotIn("Coffee Beans", names_b)


    def test_partial_supplier_metrics_and_no_fake_scores(self) -> None:
        # Suppliers with ONLY supplier name and cost/vendor price (no delivery days, no reliability, no vendor stock)
        csv_data = (
            b"SKU,Product Name,Stock,Unit Cost,Supplier\n"
            b"S1,Widget Alpha,5,100,Vendor Cheap\n"
            b"S1,Widget Alpha,5,140,Vendor Costly\n"
        )
        bundle = ingest.read_uploaded_file(csv_data, filename="partial_sup.csv")
        mapping = ingest.suggest_column_mapping(list(bundle["raw_df"].columns))
        val = ingest.validate_and_preprocess_dataset(bundle["raw_df"], mapping)
        self.assertTrue(val["valid"])
        load_res = ingest.create_isolated_session_db(val["cleaned_df"], val["capabilities"], "partial_sup.csv")
        self._temp_dirs.append(load_res["temp_dir"])

        sup = SupervisorAgent(db_path=load_res["db_path"])
        comp = sup.supplier_agent.compare_suppliers("S1", required_qty=10)
        self.assertEqual(comp["scored_criteria"], "price")
        self.assertEqual(comp["top_supplier"]["supplier_name"], "Vendor Cheap")
        self.assertTrue(pd.isna(comp["top_supplier"]["delivery_days"]))
        self.assertTrue(pd.isna(comp["top_supplier"]["reliability_score"]))


if __name__ == "__main__":
    unittest.main()
