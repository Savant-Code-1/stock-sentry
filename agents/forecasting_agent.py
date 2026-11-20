"""Forecasting Agent: analyzes historical sales and estimates stockout risk."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from backend import forecasting as fc_backend
from config import DEFAULT_FORECAST_HORIZON_DAYS, DEFAULT_FORECAST_WINDOW_DAYS

DbPath = Optional[Union[str, Path]]


class ForecastingAgent:
    """Specialized agent responsible for statistical baseline demand forecasting."""

    name = "Forecasting Agent"

    def __init__(self, db_path: DbPath = None) -> None:
        self.db_path = db_path

    def check_readiness(self) -> dict:
        res = fc_backend.check_forecasting_readiness(db_path=self.db_path)
        res["agent"] = self.name
        return res

    def forecast_sku(
        self,
        product_id: str,
        horizon_days: int = DEFAULT_FORECAST_HORIZON_DAYS,
        window_days: int = DEFAULT_FORECAST_WINDOW_DAYS,
        method: str = "ema",
        lead_time_days: Optional[int] = None,
    ) -> dict:
        result = fc_backend.forecast_product(
            product_id=product_id,
            horizon_days=horizon_days,
            window_days=window_days,
            method=method,
            lead_time_days=lead_time_days,
            db_path=self.db_path,
        )
        result["agent"] = self.name
        return result

    def forecast_portfolio(
        self,
        horizon_days: int = DEFAULT_FORECAST_HORIZON_DAYS,
        window_days: int = DEFAULT_FORECAST_WINDOW_DAYS,
        method: str = "ema",
    ) -> pd.DataFrame:
        return fc_backend.forecast_all_products(
            horizon_days=horizon_days,
            window_days=window_days,
            method=method,
            db_path=self.db_path,
        )

    def get_stockout_risk_products(self, within_days: int = 5) -> dict:
        readiness = self.check_readiness()
        if not readiness["ready"]:
            return {
                "agent": self.name,
                "ready": False,
                "limitation": readiness["reason"],
                "within_days": within_days,
                "count": 0,
                "products": pd.DataFrame(),
            }

        df = self.forecast_portfolio(horizon_days=max(within_days, 7))
        if df.empty:
            return {
                "agent": self.name,
                "ready": True,
                "limitation": None,
                "within_days": within_days,
                "count": 0,
                "products": df,
            }

        at_risk = df[
            (df["current_stock"] <= 0)
            | (df["days_remaining"].notna() & (df["days_remaining"] <= float(within_days)))
        ].reset_index(drop=True)
        return {
            "agent": self.name,
            "ready": True,
            "limitation": None,
            "within_days": within_days,
            "count": int(len(at_risk)),
            "products": at_risk,
            "portfolio_df": df,
        }
