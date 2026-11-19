# Stock Sentry

**AI-Agent-Powered Inventory Monitoring and Procurement Assistant for Small Indian Retailers**

---

## 1. Project Introduction
**Stock Sentry** is an open-source, offline-capable inventory control room built with Python, SQLite, Plotly, and Streamlit. It uses a modular six-agent architecture to monitor stock health, estimate short-term product demand, flag unusual inventory activity, compare wholesale suppliers transparently, recommend purchase orders, and enforce strict human-in-the-loop approval with an immutable audit trail.

> **Transparency Disclosure:** Initial products, sales, suppliers, and stock movements are **synthetic demonstration datasets** created for an Indian kirana store. Stock Sentry does **not** connect to live POS systems or live external supplier APIs, never executes automatic real-world purchases, and never treats statistical anomalies as proof of theft or fraud.

---

## 2. Problem Statement
Small Indian retailers and kirana store owners frequently track inventory in notebooks or disconnected billing tools. Without structured stock monitoring and demand visibility, stores face:
- Unexpected stockouts on fast-moving staples (milk, atta, cooking oil, biscuits).
- Overstocking and unnecessary working-capital blockage.
- Poor demand planning and reactive purchasing.
- Unnoticed inventory discrepancies between recorded counts and physical shelves.
- Difficulty comparing wholesale suppliers across price, delivery time, and reliability.

---

## 3. Proposed Solution
Stock Sentry brings six coordinated Python agents into one approachable control-room dashboard:
1. Tracks stock levels, INR valuations, and every stock movement in SQLite.
2. Estimates upcoming demand and days remaining using transparent statistical baselines.
3. Flags statistically unusual sales spikes, sudden reductions, and stock variances with clear suggested physical checks.
4. Ranks suppliers transparently across price, speed, reliability, and availability.
5. Calculates validated purchase-order recommendations with budget and MOQ checks.
6. Requires explicit human approval (`Pending Approval` → `Modified` / `Approved` / `Rejected`) backed by an immutable audit log.

---

## 4. Target Users
- Small Indian retailers and kirana store owners
- Neighborhood grocery shops and mini-marts
- Small wholesalers and local distributors
- Student builders and hackathon teams extending retail analytics tools

---

## 5. Features
- **Smart Inventory Monitoring:** Catalog table, INR valuation (cost & retail), low-stock and out-of-stock indicators, search/filter, add/edit products, and validated stock movement logging (`RESTOCK`, `SALE`, `ADJUSTMENT`, `DAMAGE`, `RETURN`).
- **Demand Forecasting Agent:** Historical sales analysis, Average Daily Sales, Exponential Smoothing (EMA) & Simple Moving Average (SMA) baselines, Days Remaining, calculated Reorder Point, stockout risk classification, and interactive Plotly charts.
- **Anomaly Detection Agent:** Detects unusually high sales spikes, unexpected stock reductions, repeated downward adjustment patterns, and recorded-vs-expected stock variances — with non-accusatory explanations and verification actions.
- **Supplier Comparison Agent:** Multi-criteria supplier comparison, transparent weighted scoring (0–100), recommendation explanations, and add/edit supplier management.
- **Procurement Agent:** Calculates suggested order quantities (`Target Stock - Current Stock - Incoming Stock`), validates MOQ, supplier availability, capacity, and optional INR budget constraints.
- **Human Approval & Audit System:** Mandatory human approval workflow (`Pending Approval`, `Modified`, `Approved`, `Rejected`) with full immutable audit trail.
- **Local Rule-Based AI Inventory Assistant:** Chat interface routed through the Supervisor Agent answering natural-language store queries directly from live SQLite data without requiring paid API keys.

---

## 6. Agent Architecture

```text
                      +-----------------------------------+
                      |    Streamlit Control Room (UI)    |
                      |   Dashboard + Chat Assistant UI   |
                      +-----------------+-----------------+
                                        |
                                        v
                      +-----------------------------------+
                      |   6. Supervisor / Orchestrator    |
                      |   Routes requests & combines data |
                      +--+----+------+------+------+------+
                         |    |      |      |      |
         +---------------+    |      |      |      +----------------+
         v                    v      v      v                       v
+-----------------+  +------------+ +------------+ +------------+ +-----------------+
| 1. Inventory    |  | 2.Forecast | | 3. Anomaly | | 4.Supplier | | 5. Procurement  |
|    Agent        |  |    Agent   | |    Agent   | |    Agent   | |    Agent        |
+--------+--------+  +-----+------+ +-----+------+ +-----+------+ +--------+--------+
         |                 |              |              |                 |
         +-----------------+--------------+--------------+-----------------+
                                          |
                                          v
                      +-----------------------------------+
                      |  Backend Logic Layer (backend/)   |
                      |  Pandas + NumPy + Validators      |
                      +-----------------+-----------------+
                                        |
                                        v
                      +-----------------------------------+
                      |  SQLite Database (database/)      |
                      |  Auto-initialized from data/*.csv |
                      +-----------------------------------+
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deep technical details.

---

## 7. Technology Stack
- **Language:** Python 3.10+
- **Analytics:** Pandas, NumPy
- **Frontend UI:** Streamlit
- **Charts:** Plotly
- **Database:** SQLite (Python built-in `sqlite3`)
- **Initial Datasets:** Synthetic CSV datasets (`data/`)
- **Testing:** `unittest` / `pytest`
- **Offline Ready:** Runs locally with zero paid APIs once Python dependencies are installed.

---

## 8. Folder Structure
```text
stock-sentry/
├── app.py                     # Main Streamlit dashboard entry point
├── config.py                  # Centralized configuration & thresholds
├── requirements.txt           # Python package dependencies
├── README.md                  # Complete project overview
├── .gitignore                 # Excludes secrets, venv, caches, local DB
├── .env.example               # Optional environment configuration template
├── LICENSE                    # MIT License
├── agents/                    # 6 modular AI agents + Supervisor orchestrator
├── backend/                   # Core Python business logic & calculations
├── database/                  # SQLite schema, connection helpers & safe seeder
├── dashboard/                 # Reusable Streamlit styling & Plotly charts
├── data/                      # Synthetic Indian kirana store CSV datasets
├── utils/                     # INR formatting helpers & input validators
├── tests/                     # Unit & integration test suite
├── docs/                      # Architecture, User, Developer, Data Flow & Roadmap docs
└── assets/                    # Store logos / presentation screenshots
```

---

## 9. Installation Instructions
```powershell
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 10. How to Run the Application
```powershell
streamlit run app.py
```
Streamlit will start locally and open (or display) `http://localhost:8501`. On first launch, Stock Sentry automatically creates `database/stock_sentry.db` and seeds the synthetic demonstration dataset safely.

---

## 11. Dataset Explanation
All four files in `data/` contain **synthetic demonstration data** tailored to an Indian kirana store:
- `products.csv`: 55 products across 6 categories (Grocery, Dairy, Beverages, Personal Care, Snacks, Household Essentials) with realistic INR pricing, including 3 out-of-stock and 14 low-stock SKUs.
- `sales.csv`: 1,402 daily sales records spanning 35 historical days, including realistic weekend demand lift and deliberate recent sales spikes for anomaly testing.
- `suppliers.csv`: 165 supplier-product offers across 12 Indian wholesale suppliers (2–3 competing suppliers per SKU) with unit price, delivery days, available stock, reliability score, and minimum order quantity (MOQ).
- `stock_movements.csv`: 128 stock movement logs (`RESTOCK`, `SALE`, `ADJUSTMENT`, `DAMAGE`, `RETURN`) including deliberate discrepancy patterns for Anomaly Center demonstrations.

---

## 12. Database Explanation
Stock Sentry uses SQLite (`database/stock_sentry.db`), created automatically on first run:
- Tables: `products`, `sales`, `suppliers`, `stock_movements`, `purchase_orders`, `audit_logs`, `app_metadata`.
- Uses parameterized SQL queries (`?` placeholders) exclusively to prevent SQL injection.
- Safe idempotent seeding via `app_metadata` ensures restarting the app never duplicates sample data or deletes user modifications, purchase orders, or audit logs.

---

## 13. How Forecasting Works
Stock Sentry uses transparent **statistical baselines** (not deep ML):
- `Average Daily Sales = Total Units Sold / Number of Days`
- `Days Remaining = Current Stock / Forecast Daily Demand`
- `Reorder Point = (Average Daily Demand × Supplier Lead Time) + Safety Stock`
- Users can switch between **Single Exponential Smoothing (EMA, alpha=0.35)** and **Simple Moving Average (SMA, configurable window)** in the UI, with all assumptions explicitly listed.

---

## 14. How Anomaly Detection Works
The Anomaly Detection Agent combines statistical z-score checks and transparent rules:
1. **Unusually High Sales Spike:** Recent daily sales exceeding `z >= 2.2` and `>= 2.2x` historical average.
2. **Unexpected Stock Reduction / Damage:** Single downward adjustment/damage `>= 15 units` or `>= 35%` of target stock.
3. **Repeated Downward Adjustment Pattern:** 3 or more negative adjustments on the same SKU within 7 days.
4. **Recorded Stock vs Expected Stock Variance:** Large recent restocks minus recent sales exceeding current recorded stock beyond threshold.

> **Important:** An anomaly is only an unusual statistical or operational event — never proof of theft, fraud, or wrongdoing.

---

## 15. How Supplier Ranking Works
Each supplier for a given product receives a transparent weighted score out of 100:
- **Unit Price (35%):** Lower unit price earns a higher normalized score.
- **Delivery Time (25%):** Fewer delivery days earns a higher normalized score.
- **Reliability Score (25%):** Supplier historical reliability percentage (0–100).
- **Availability (15%):** Full points when available stock meets required quantity; out-of-stock suppliers are penalized.

---

## 16. How Procurement Recommendations Work
The Procurement Agent flags items where stock + approved incoming stock falls at or below the effective reorder point (or stockout is projected near supplier lead time):
- `Suggested Order Quantity = Target Stock - Current Stock - Incoming Stock` (clamped at `>= 0`).
- Validates **MOQ**, **supplier availability**, **store capacity**, and optional **INR budget limit**.

---

## 17. Human Approval Workflow
No purchase order is ever sent or paid automatically.
- Draft POs start in **`Pending Approval`**.
- A human reviewer can **`Modify`** quantity or supplier, **`Approve`**, or **`Reject`**.
- Every transition writes an immutable record to `audit_logs` capturing `order_id`, `action`, `previous_status`, `new_status`, `timestamp`, `actor`, and exact field changes.

---

## 18. Testing Instructions
Run the test suite with either `unittest` or `pytest`:
```powershell
python -m unittest discover -s tests -v
```
or:
```powershell
pytest -v
```

---

## 19. Limitations
- Uses synthetic demonstration datasets; not connected to live POS billing terminals or distributor APIs.
- Forecasting uses baseline statistical methods (SMA/EMA) that do not model festival seasonality, promotions, or weather automatically.
- Single-store SQLite database is ideal for local demos and single-shop usage; multi-store production deployments should migrate to PostgreSQL.
- The default assistant is a transparent local rule-based router (no external LLM calls unless extended).

---

## 20. Future Scope
See [docs/FUTURE_ROADMAP.md](docs/FUTURE_ROADMAP.md) for detailed implementation guides covering POS integration, barcode scanning, WhatsApp alerts, voice queries, supplier APIs, ML forecasting, multi-store support, expiry tracking, festival demand prediction, and cloud deployment.

---

## Dynamic User File Upload & Multi-User Session Isolation (v1.1)
Stock Sentry operates as a **fully dynamic inventory intelligence platform**:
- **Upload Your Own Data (`Upload Data` page):** Drag and drop `.csv`, `.xlsx`, or `.xls` business files directly in Streamlit—no manual CSV replacement or Python editing required.
- **Intelligent Interactive Column Mapping:** Automatically recognizes common retail headers (`SKU / Item Code`, `Product Name`, `Available Units`, `Units Sold`, `Unit Cost`, `Supplier / Vendor`, `Order Date`, etc.) and lets you customize every mapping.
- **Zero Data Fabrication:** Missing business metrics (prices, reorder points, target stock, supplier ratings, or sales dates) are never invented. Every module clearly displays active capabilities and honest limitations.
- **Data Quality Audit (`Data Quality` page):** Displays rows/columns, completeness %, missing-value breakdown chart, duplicate row detection, validation warnings, and original vs cleaned data preview.
- **All Five Agents Connected Dynamically:** Inventory Intelligence (including Overstock & Sales Analytics), Demand Forecasting, Anomaly Detection, Supplier Analysis, and Procurement all analyze the user's uploaded dataset inside a **session-isolated temporary SQLite database**.
- **Clean Dataset Replacement:** Uploading a new file deletes the previous session's temporary DB and clears old agent caches automatically without ever modifying original project files.
- **Downloadable Reports (`Reports` page):** Export CSV tables for every agent plus a complete Markdown Executive Summary generated strictly from the active dataset.
