# Stock Sentry — Shop Owner User Guide

Welcome to **Stock Sentry**, your retail inventory control room. This guide explains how to use each section of the dashboard in day-to-day shop operations.

---

## 1. Overview Dashboard
Your store's instant health snapshot:
- **Top KPIs:** Total tracked products, total inventory value in Indian Rupees (both cost price and retail value), low-stock items, out-of-stock items, and pending purchase orders.
- **Inventory Health Chart:** Quickly see how many SKUs are Healthy (green), Low Stock (amber), or Out of Stock (red).
- **Category Valuation Chart:** See where your working capital is invested (Grocery, Dairy, Beverages, etc.).
- **Priority Attention Queue:** Lists urgent low/out-of-stock SKUs needing restock first.

## 2. Inventory Management
Manage your complete product list:
- **Product Catalog:** Search by product name, ID, or category, and filter by stock status.
- **Update Stock:** Record `RESTOCK` (incoming delivery), `SALE` (counter sales — automatically added to sales history too!), `ADJUSTMENT` (recount correction), `DAMAGE`, or `RETURN`.
- **Add Product:** Register a new SKU with opening stock, cost/selling price, reorder point, and target stock.
- **Edit Product:** Update prices, category, reorder point, or target stock at any time.
- **Stock Movements Log:** View chronological history of every stock change.

## 3. Demand Forecasting
Plan purchases before items run out:
- Choose any product and toggle between **Exponential Smoothing (EMA)** and **Simple Moving Average (SMA)**.
- Adjust the forecast horizon (3 to 30 days) to inspect projected daily demand and projected stock burn-down.
- Review **Days Remaining**, **Calculated Reorder Point**, and **Stockout Risk** (`Critical`, `High`, `Medium`, `Low`).
- Expand **Forecast Formulas & Assumptions** to see exact numbers behind every estimate.

## 4. Anomaly Center
Spot unusual inventory events early:
- Reviews sales spikes, large negative adjustments/damage entries, repeated downward adjustments, and recorded-vs-expected stock gaps.
- Every alert clearly shows **Expected Value**, **Actual Value**, **Severity**, **Explanation**, and a practical **Suggested Action**.
- **Remember:** An anomaly is only a flag to check shelves or bills — it is never proof of theft or fraud.

## 5. Supplier Management
Pick the best wholesaler transparently:
- **Compare & Rank Suppliers:** Select a product and quantity needed to view ranked suppliers with a transparent 0–100 score based on Price (35%), Delivery Speed (25%), Reliability (25%), and Availability (15%).
- **Supplier Catalog:** Browse all wholesalers and prices.
- **Add / Edit Supplier Offer:** Add new wholesale quotes or update prices/delivery days anytime.

## 6. Purchase Orders (Human Approval & Audit)
Generate and approve restock orders safely:
- **Recommended Orders:** Set an optional INR budget limit, review Procurement Agent recommendations, and click **Create Draft Purchase Order**.
- **Approval Queue:** Review every `Pending Approval` or `Modified` order. You can **Modify** quantity or supplier, **Approve**, or **Reject**. Stock Sentry never places external orders automatically.
- **Order History & Audit Trail:** Track every past order and inspect the immutable audit log showing who changed what and when.

## 7. AI Inventory Assistant
Ask store questions in plain English using the chat box or one-click suggested prompts (e.g., *"Which products may run out in the next five days?"* or *"Which supplier is best for cooking oil?"*). Every answer is calculated live from your store's SQLite database and shows which agents were consulted.
