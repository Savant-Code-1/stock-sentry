# Stock Sentry — System & Multi-Agent Architecture

## Design Philosophy
Stock Sentry separates **UI (`app.py`, `dashboard/`)**, **Agent Coordination (`agents/`)**, **Business Logic (`backend/`)**, **Persistence (`database/`)**, and **Validation/Utilities (`utils/`)**. This keeps every layer testable without launching Streamlit and enables a beginner student to extend features safely—or split ownership across a 4-person hackathon team.

---

## High-Level Component Diagram

```text
+-----------------------------------------------------------------------+
|                       PRESENTATION LAYER (UI)                         |
|         app.py (7 Dashboard Pages)  +  dashboard/components.py        |
+-----------------------------------+-----------------------------------+
                                    | calls
                                    v
+-----------------------------------------------------------------------+
|                    ORCHESTRATION LAYER (agents/)                      |
|                    SupervisorAgent (supervisor_agent.py)              |
|  - Routes natural-language questions (Local Rule-Based Assistant)     |
|  - Coordinates store-wide health checks across specialized agents     |
|  - Ready for optional external LLM tool-calling extension             |
+-------+-------------+-------------+-------------+-------------+-------+
        |             |             |             |             |
        v             v             v             v             v
+-------------+ +-----------+ +-----------+ +-----------+ +-------------+
| Inventory   | |Forecasting| | Anomaly   | | Supplier  | | Procurement |
| Agent       | | Agent     | | Agent     | | Agent     | | Agent       |
+------+------+ +-----+-----+ +-----+-----+ +-----+-----+ +------+------+
       |              |             |             |              |
       v              v             v             v              v
+-----------------------------------------------------------------------+
|                  DOMAIN LOGIC LAYER (backend/)                        |
|  inventory.py | forecasting.py | anomaly_detection.py | supplier.py   |
|  procurement.py                                                       |
+-----------------------------------+-----------------------------------+
                                    | uses
                                    v
+-----------------------------------------------------------------------+
|                VALIDATION & PERSISTENCE LAYER                         |
|     utils/validators.py + utils/helpers.py + database/db.py (SQLite)  |
|             Initial first-run seed from data/*.csv                    |
+-----------------------------------------------------------------------+
```

---

## The Six Agents & Their Responsibilities

| # | Agent File | Primary Responsibility | Underlying Backend Module |
|---|---|---|---|
| 1 | `agents/inventory_agent.py` | Monitors inventory catalog, INR valuations, low/out-of-stock alerts, SKU CRUD, and movement ledger. | `backend/inventory.py` |
| 2 | `agents/forecasting_agent.py` | Analyzes historical daily sales, runs SMA/EMA baselines, computes days remaining & reorder point. | `backend/forecasting.py` |
| 3 | `agents/anomaly_agent.py` | Scans sales and stock movements for statistical spikes, large drops, repeated adjustments, and variances. | `backend/anomaly_detection.py` |
| 4 | `agents/supplier_agent.py` | Maintains wholesale offers, runs weighted supplier scoring, and matches products by keyword. | `backend/supplier.py` |
| 5 | `agents/procurement_agent.py` | Computes order quantities, validates MOQ/capacity/budget, manages draft POs, approval states & audit logs. | `backend/procurement.py` |
| 6 | `agents/supervisor_agent.py` | Orchestrates multi-agent workflows and answers user queries via transparent local rule-based routing. | Coordinates Agents 1–5 |

---

## Agent Communication Flow Example
**User asks:** *"Which products should I reorder today?"*
1. `app.py` sends the prompt to `SupervisorAgent.answer_query()`.
2. `SupervisorAgent` identifies the procurement intent and calls `ProcurementAgent.recommend_orders()`.
3. Inside `backend/procurement.py`, the Procurement engine invokes:
   - `forecast_all_products()` (Forecasting logic) to compute demand and stockout timelines.
   - `rank_suppliers_for_product()` (Supplier logic) to pick the highest-ranked available wholesaler.
   - `get_incoming_stock_by_product()` to subtract already-approved incoming POs.
4. `ProcurementAgent` returns structured recommendations with INR cost estimates and budget flags.
5. `SupervisorAgent` formats a readable explanation, records `agents_used`, attaches the supporting DataFrame, and returns the payload to Streamlit.

---

## Optional Future LLM Integration Point
`SupervisorAgent` reads `LLM_PROVIDER` and `LLM_API_KEY` from `config.py`. By default, `LLM_PROVIDER="local_rule_based"` so the app works offline with zero cost. To plug in an LLM later (Gemini, OpenAI, or local Ollama), add a tool-calling adapter inside `agents/supervisor_agent.py` that maps LLM function calls directly to the existing five agent methods while keeping all calculations inside deterministic Python functions.
