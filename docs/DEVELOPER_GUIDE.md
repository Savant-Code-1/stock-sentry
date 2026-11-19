# Stock Sentry — Developer & Team Contribution Guide

This guide helps solo beginners and student hackathon teams understand how modules connect and where to make changes cleanly.

---

## 1. Module Responsibilities
- `config.py`: Thresholds, weights, file paths, and optional environment variables. Change default tuning parameters here first.
- `database/`: SQLite schema (`models.py`), connection & parameterized query helpers (`db.py`), and safe CSV first-run seeder (`seed.py`).
- `backend/`: Pure Python + Pandas + NumPy business logic. **Never import Streamlit inside `backend/` or `agents/`** — keeping them UI-free makes testing fast and allows adding a REST API or WhatsApp bot later!
- `agents/`: Thin, well-structured agent classes wrapping backend operations, coordinated by `SupervisorAgent`.
- `dashboard/components.py`: Reusable CSS and Plotly chart builders.
- `app.py`: Streamlit pages and forms calling `SupervisorAgent`.
- `utils/`: INR formatting (`helpers.py`) and strict input validation (`validators.py`).
- `tests/`: Automated tests using isolated temporary SQLite databases (never modifies your real store DB).

---

## 2. Suggested Team Role Split (4-Person Hackathon Team)
- **Developer 1 (Data & Inventory):** `data/*.csv`, `database/`, `backend/inventory.py`, `agents/inventory_agent.py`
- **Developer 2 (Forecasting & Anomalies):** `backend/forecasting.py`, `backend/anomaly_detection.py`, `agents/forecasting_agent.py`, `agents/anomaly_agent.py`
- **Developer 3 (Suppliers, Procurement & Audit):** `backend/supplier.py`, `backend/procurement.py`, `agents/supplier_agent.py`, `agents/procurement_agent.py`
- **Developer 4 (UI, Charts & Assistant):** `dashboard/components.py`, `app.py`, `agents/supervisor_agent.py`

---

## 3. How to Add a New Feature Safely (Step-by-Step)
Example: Adding a **Product Expiry Date** field:
1. **Schema:** Add `expiry_date TEXT` column in `database/models.py` (and a migration helper in `database/db.py` for existing DBs).
2. **Validation:** Validate date format in `utils/validators.py`.
3. **Business Logic:** Add `get_expiring_soon_products(days=7)` in `backend/inventory.py`.
4. **Agent:** Expose `get_expiry_alerts()` in `agents/inventory_agent.py` and add an intent rule in `agents/supervisor_agent.py`.
5. **UI:** Add expiry date input in `app.py` (Inventory Management) and an alert card in Overview Dashboard.
6. **Tests:** Add unit test coverage in `tests/test_inventory.py` and run `python -m unittest discover -s tests -v`.

---

## 4. Coding & Safety Conventions
- Always pass SQL values via `?` parameterized queries (`fetch_all`, `fetch_one`, `execute_write`).
- Always validate user inputs through `utils/validators.py` before writing to SQLite.
- Never hardcode API keys or secrets; always use `os.getenv(...)` via `config.py`.
- Keep tests fast and isolated by passing a temporary `db_path` (every backend/agent function accepts `db_path`).
