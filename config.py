"""Central configuration for Stock Sentry."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional; environment variables still work directly.
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_DIR = BASE_DIR / "database"
DEFAULT_DB_PATH = Path(
    os.getenv("STOCK_SENTRY_DB_PATH", str(DATABASE_DIR / "stock_sentry.db"))
)

# Application metadata
APP_NAME = "Stock Sentry"
APP_TAGLINE = "AI-Agent-Powered Inventory Monitoring & Procurement Assistant"
APP_VERSION = "1.2.0"
CURRENCY_SYMBOL = "₹"

# Security & upload guardrails (prevents memory exhaustion / DoS on untrusted files)
MAX_UPLOAD_FILE_SIZE_MB = float(os.getenv("MAX_UPLOAD_FILE_SIZE_MB", "25.0"))
MAX_UPLOAD_FILE_SIZE_BYTES = int(MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024)
MAX_UPLOAD_ROWS = int(os.getenv("MAX_UPLOAD_ROWS", "100000"))

# UI performance & pagination defaults
DEFAULT_TABLE_PAGE_SIZE = int(os.getenv("TABLE_PAGE_SIZE", "25"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))

# Forecasting defaults (statistical baseline)
DEFAULT_FORECAST_WINDOW_DAYS = int(os.getenv("FORECAST_WINDOW_DAYS", "14"))
DEFAULT_FORECAST_HORIZON_DAYS = int(os.getenv("DEFAULT_FORECAST_HORIZON_DAYS", "7"))
DEFAULT_LEAD_TIME_DAYS = int(os.getenv("DEFAULT_LEAD_TIME_DAYS", "3"))
DEFAULT_SAFETY_STOCK_DAYS = int(os.getenv("DEFAULT_SAFETY_STOCK_DAYS", "2"))
EMA_ALPHA = float(os.getenv("EMA_ALPHA", "0.35"))

# Anomaly detection thresholds (statistical & rule-based)
SALES_SPIKE_Z_THRESHOLD = float(os.getenv("SALES_SPIKE_Z_THRESHOLD", "2.2"))
SUDDEN_STOCK_DROP_PCT = float(os.getenv("SUDDEN_STOCK_DROP_PCT", "0.35"))
LARGE_ADJUSTMENT_UNITS = int(os.getenv("LARGE_ADJUSTMENT_UNITS", "15"))
LEDGER_MISMATCH_THRESHOLD = int(os.getenv("LEDGER_MISMATCH_THRESHOLD", "8"))

# Transparent Supplier Scoring Weights (must sum to 1.0)
SUPPLIER_SCORE_WEIGHTS = {
    "price": 0.35,
    "delivery": 0.25,
    "reliability": 0.25,
    "availability": 0.15,
}

# Procurement defaults
DEFAULT_BUDGET_LIMIT = float(os.getenv("DEFAULT_BUDGET_LIMIT", "50000.0"))
MAX_STOCK_CAPACITY_MULTIPLIER = float(os.getenv("MAX_STOCK_CAPACITY_MULTIPLIER", "1.5"))

# Optional future LLM settings (NOT required for default operation)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "local_rule_based")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# Mandatory transparency disclosures
SYNTHETIC_DATA_NOTICE = (
    "Demo Mode: Initial inventory, sales, supplier, and movement records are "
    "synthetic demonstration data created for an Indian kirana store. "
    "No live POS systems or external supplier APIs are connected."
)
ANOMALY_DISCLAIMER = (
    "Important: An anomaly is only a statistically unusual inventory event. "
    "It is NOT proof of theft, fraud, or wrongdoing. Always verify physically."
)
PROCUREMENT_DISCLAIMER = (
    "Human-in-the-Loop Policy: Stock Sentry never executes automatic purchases. "
    "Every purchase order requires explicit human approval."
)
