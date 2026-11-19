"""Tests for demand forecasting formulas, SMA/EMA baselines, and stockout risk."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents.forecasting_agent import ForecastingAgent
from backend import forecasting
from database.seed import seed_database_if_empty


class TestForecasting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_fc.db"
        seed_database_if_empty(db_path=self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_core_formulas(self) -> None:
        # Average Daily Sales = Total Units Sold / Number of Days
        self.assertEqual(forecasting.calculate_average_daily_sales(140, 14), 10.0)
        self.assertEqual(forecasting.calculate_average_daily_sales(100, 0), 0.0)

        # Days Remaining = Current Stock / Average Daily Sales
        self.assertEqual(forecasting.calculate_days_remaining(50, 10.0), 5.0)
        self.assertEqual(forecasting.calculate_days_remaining(0, 10.0), 0.0)
        self.assertIsNone(forecasting.calculate_days_remaining(20, 0.0))

        # Reorder Point = Average Daily Demand * Supplier Lead Time + Safety Stock
        self.assertEqual(forecasting.calculate_reorder_point(5.0, 3, 6.0), 21)
        self.assertEqual(forecasting.calculate_reorder_point(4.2, 2, 3.0), 12)

    def test_sma_and_ema_baselines(self) -> None:
        s = pd.Series([4, 6, 8, 10, 12], dtype=float)
        self.assertEqual(forecasting.compute_moving_average(s, window=3), 10.0)
        ema_val = forecasting.compute_exponential_smoothing(s, alpha=0.5)
        self.assertGreater(ema_val, 8.0)
        self.assertLessEqual(ema_val, 12.0)

    def test_forecast_product_and_agent(self) -> None:
        agent = ForecastingAgent(db_path=self.db_path)
        fc = agent.forecast_sku("P002", horizon_days=7, window_days=14, method="ema")
        self.assertEqual(fc["product_id"], "P002")
        self.assertGreater(fc["avg_daily_sales"], 0)
        self.assertEqual(len(fc["forecast_df"]), 7)
        self.assertGreater(len(fc["assumptions"]), 0)

        at_risk = agent.get_stockout_risk_products(within_days=5)
        self.assertGreater(at_risk["count"], 0)


if __name__ == "__main__":
    unittest.main()
