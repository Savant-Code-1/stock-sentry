# Stock Sentry — Future Roadmap & Extensibility Guide

Stock Sentry's layered architecture is designed so each future upgrade maps cleanly to specific files without rewriting the core app.

---

## 1. Real POS Integration (Tally / Vyapar / Marg / Custom Billing)
- **What to build:** Scheduled CSV/webhook sync or REST ingestion endpoint that imports daily sales bills automatically.
- **New files to create:** `integrations/pos_connector.py`, `api/server.py` (optional FastAPI webhook receiver).
- **Existing files to modify:** `config.py` (POS API credentials via env vars), `backend/inventory.py` (batch sale ingestion calling `update_stock`).

## 2. Barcode Scanning (Mobile Camera / USB Scanner)
- **What to build:** Store EAN-13 / UPC barcode per SKU and allow quick scan-to-restock or scan-to-sell in Streamlit.
- **New files to create:** `utils/barcode_helpers.py`.
- **Existing files to modify:** `database/models.py` (add `barcode` column to `products`), `app.py` (barcode input box in Inventory Management).

## 3. WhatsApp Notifications (Daily Low-Stock & Approval Alerts)
- **What to build:** Send morning WhatsApp digest of critical stockouts and pending purchase orders via WhatsApp Business Cloud API or Twilio.
- **New files to create:** `integrations/whatsapp_notifier.py`.
- **Existing files to modify:** `config.py` (WhatsApp webhook/token env vars), `agents/supervisor_agent.py` (trigger alert digest from `run_store_health_check()`).

## 4. Voice Assistant (Hindi / Hinglish / Regional Languages)
- **What to build:** Speech-to-text input (e.g., Whisper or Web Speech API) feeding directly into `SupervisorAgent.answer_query()`.
- **New files to create:** `integrations/voice_interface.py`.
- **Existing files to modify:** `app.py` (audio recorder widget in AI Assistant page), `agents/supervisor_agent.py` (Hindi/Hinglish keyword mapping or optional LLM translator).

## 5. Real Supplier APIs & B2B Catalogs
- **What to build:** Fetch live wholesale prices and stock availability from distributor APIs (e.g., Udaan, Reliance JioMart Partner, or local wholesaler portals) while keeping human approval mandatory.
- **New files to create:** `integrations/supplier_api_client.py`.
- **Existing files to modify:** `backend/supplier.py` (sync external quotes into `suppliers` table), `config.py`.

## 6. Advanced ML Forecasting
- **What to build:** Upgrade from statistical SMA/EMA baselines to Prophet, LightGBM, or scikit-learn regression with lag features once > 6 months of real sales history exist.
- **New files to create:** `backend/ml_forecasting.py`, `tests/test_ml_forecasting.py`.
- **Existing files to modify:** `backend/forecasting.py` (add `method="ml"` option), `app.py` (add selector option in Demand Forecasting), `requirements.txt`.

## 7. Multi-Store Support
- **What to build:** Add `store_id` scoping so one owner can monitor multiple kirana branches and transfer surplus stock between stores.
- **New files to create:** `backend/stores.py`.
- **Existing files to modify:** `database/models.py` (add `stores` table & `store_id` foreign keys), `database/db.py` (migrate to PostgreSQL for concurrent multi-store writes), `app.py` (store switcher in sidebar).

## 8. Expiry & Batch Tracking (FEFO — First Expired, First Out)
- **What to build:** Track batch numbers and expiry dates for dairy, snacks, and packaged food, alerting owners 7–14 days before expiry to discount slow-moving batches.
- **New files to create:** `backend/batches.py`, `tests/test_batches.py`.
- **Existing files to modify:** `database/models.py` (`product_batches` table), `agents/inventory_agent.py`, `app.py`.

## 9. Festival & Seasonal Demand Prediction (Diwali, Holi, Eid, Monsoon)
- **What to build:** Maintain an Indian retail festival calendar with category uplift multipliers (e.g., sweets/oil/ghee before Diwali, cold drinks in summer).
- **New files to create:** `data/indian_festivals.csv`, `backend/seasonality.py`.
- **Existing files to modify:** `backend/forecasting.py` (apply seasonal adjustment factors transparently with assumptions displayed).

## 10. Cloud Deployment
- **What to build:** Deploy to Streamlit Community Cloud, Render, Railway, or Docker with a managed PostgreSQL database (Supabase / Neon) and authentication.
- **New files to create:** `Dockerfile`, `docker-compose.yml`, `.streamlit/config.toml`.
- **Existing files to modify:** `database/db.py` (support `DATABASE_URL` for PostgreSQL alongside local SQLite fallback), `config.py`.
