# Stock Sentry — Data Flow & Internal API Reference

## End-to-End Data Flow

```text
[1] Sample CSV Files (data/*.csv)
        │  (First run only — guarded by app_metadata.initial_seed_completed)
        ▼
[2] SQLite Database (database/stock_sentry.db)
    Tables: products | sales | suppliers | stock_movements | purchase_orders | audit_logs
        │
        ▼
[3] Backend Modules (backend/*.py)
    - inventory.py         -> valuations, stock status, CRUD, movement updates
    - forecasting.py       -> daily sales resampling, SMA/EMA, days remaining, ROP
    - anomaly_detection.py -> z-score sales spikes, drop rules, pattern & variance checks
    - supplier.py          -> min-max normalized weighted scoring (0-100)
    - procurement.py       -> order qty formula, MOQ/budget checks, PO state machine + audit
        │
        ▼
[4] Specialized Agents (agents/*.py)
    InventoryAgent | ForecastingAgent | AnomalyAgent | SupplierAgent | ProcurementAgent
        │
        ▼
[5] Supervisor / Orchestrator Agent (agents/supervisor_agent.py)
    - Aggregates store health checks
    - Routes natural-language questions to the right agent(s)
        │
        ▼
[6] Streamlit Dashboard (app.py + dashboard/components.py)
    - Renders interactive tables, Plotly charts, forms, and chat responses
    - Human actions (Add/Edit/Stock Update/Approve/Modify/Reject) write back to SQLite [2]
```

---

## Key Internal Function Signatures (Ready for Future FastAPI / REST Wrapping)

### Inventory (`backend/inventory.py`)
- `get_all_products(db_path=None, search=None, category=None, status_filter=None) -> pd.DataFrame`
- `get_inventory_summary(db_path=None) -> dict`
- `add_product(...) -> dict`
- `update_product(...) -> dict`
- `update_stock(product_id, movement_type, quantity, remarks="", db_path=None) -> dict`

### Forecasting (`backend/forecasting.py`)
- `calculate_average_daily_sales(total_units_sold, number_of_days) -> float`
- `calculate_days_remaining(current_stock, avg_daily_sales) -> Optional[float]`
- `calculate_reorder_point(avg_daily_demand, supplier_lead_time_days, safety_stock) -> int`
- `forecast_product(product_id, horizon_days=7, window_days=14, method="ema", db_path=None) -> dict`
- `forecast_all_products(...) -> pd.DataFrame`

### Anomaly Detection (`backend/anomaly_detection.py`)
- `detect_anomalies(db_path=None) -> list[dict]`

### Supplier Comparison (`backend/supplier.py`)
- `rank_suppliers_for_product(product_id, required_qty=1, weights=None, db_path=None) -> pd.DataFrame`
- `add_or_update_supplier_offer(...) -> dict`

### Procurement & Audit (`backend/procurement.py`)
- `calculate_suggested_order_quantity(target_stock, current_stock, incoming_stock=0, min_order_qty=1, available_quantity=None, max_capacity=None) -> tuple[int, list[str]]`
- `generate_purchase_recommendations(budget_limit=..., horizon_days=7, db_path=None) -> list[dict]`
- `create_purchase_order(...) -> dict`
- `modify_purchase_order(...) -> dict`
- `approve_purchase_order(...) -> dict`
- `reject_purchase_order(...) -> dict`
- `get_audit_logs(order_id=None, limit=200, db_path=None) -> pd.DataFrame`

### Orchestration (`agents/supervisor_agent.py`)
- `SupervisorAgent.run_store_health_check() -> dict`
- `SupervisorAgent.answer_query(user_query: str) -> dict`
