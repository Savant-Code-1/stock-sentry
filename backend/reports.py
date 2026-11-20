"""Dynamic downloadable CSV and Markdown executive report generation with CSV injection defense."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from agents.supervisor_agent import SupervisorAgent
from utils.helpers import format_inr, sanitize_csv_cell, utc_now_iso


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Encode a DataFrame as UTF-8 with BOM CSV bytes while neutralizing formula injection."""
    if df is None or df.empty:
        return b""
    safe_df = df.copy()
    for col in safe_df.select_dtypes(include=["object", "string"]).columns:
        safe_df[col] = safe_df[col].map(sanitize_csv_cell)
    return safe_df.to_csv(index=False).encode("utf-8-sig")


def build_executive_markdown_report(
    supervisor: SupervisorAgent,
    dataset_label: str,
    quality_report: Optional[dict[str, Any]] = None,
    capabilities: Optional[dict[str, Any]] = None,
    precomputed_health: Optional[dict[str, Any]] = None,
    precomputed_sales: Optional[dict[str, Any]] = None,
) -> str:
    """Generate a comprehensive Markdown executive report in a single multi-agent pass."""
    health = precomputed_health if precomputed_health is not None else supervisor.run_store_health_check()
    inv = health["inventory_summary"]
    sales = precomputed_sales if precomputed_sales is not None else supervisor.inventory_agent.get_sales_analytics()
    anoms = health.get("_anomalies_detail") or supervisor.anomaly_agent.scan_anomalies()
    recs = health.get("_recommendations_detail") or supervisor.procurement_agent.recommend_orders()
    sup_overview = health.get("_supplier_overview")
    if sup_overview is None:
        sup_overview = supervisor.supplier_agent.get_supplier_overview()

    lines = [
        "# Stock Sentry — Dynamic Inventory Intelligence Report",
        f"- **Generated At:** {utc_now_iso()}",
        f"- **Active Data Source:** {dataset_label}",
        "",
        "---",
        "## 1. Inventory Overview",
        f"- **Total Products:** {inv['total_products']}",
        f"- **Total Available Units:** {inv['total_units']:,}",
        f"- **Inventory Valuation (Cost):** {format_inr(inv['total_cost_value']) if inv['has_cost_data'] else 'Unavailable (no cost column mapped)'}",
        f"- **Inventory Valuation (Retail):** {format_inr(inv['total_retail_value']) if inv['has_retail_data'] else 'Unavailable (no selling price column mapped)'}",
        f"- **Out-of-Stock Products:** {inv['out_of_stock_count']}",
        f"- **Low-Stock Products:** {inv['low_stock_count'] if inv['has_reorder_points'] else 'Unavailable (no reorder point column mapped)'}",
        f"- **Overstock Products:** {inv['overstock_count'] if inv['has_target_stock'] else 'Unavailable (no target stock column mapped)'}",
        "",
    ]

    if quality_report:
        lines.extend(
            [
                "## 2. Data Quality Summary",
                f"- **Uploaded Rows × Columns:** {quality_report['total_rows']} × {quality_report['total_columns']}",
                f"- **Completeness:** {quality_report['completeness_pct']}%",
                f"- **Missing Cells:** {quality_report['total_missing_cells']}",
                f"- **Duplicate Rows Detected:** {quality_report['duplicate_rows_count']}",
                "",
            ]
        )

    lines.append("## 3. Sales Analytics")
    if sales["available"]:
        lines.append(f"- **Total Sales Transactions:** {sales['total_records']}")
        lines.append(f"- **Total Units Sold:** {sales['total_units_sold']:,}")
        lines.append(
            f"- **Total Sales Revenue:** {format_inr(sales['total_revenue']) if sales['has_revenue'] else 'Unavailable (no selling price mapped)'}"
        )
        lines.append(f"- **Distinct Sales Dates:** {sales['distinct_dates']}")
    else:
        lines.append("- Sales analytics unavailable (no `Units Sold / Sales` column mapped).")
    lines.append("")

    lines.append("## 4. Demand Forecasting")
    if health["forecast_ready"]:
        lines.append(f"- **Products at Risk of Stockout (<= 5 days):** {health['stockout_risk_5d_count']}")
    else:
        lines.append(f"- Demand forecasting unavailable: {health['forecast_limitation']}")
    lines.append("")

    lines.append("## 5. Anomaly Detection")
    lines.append(f"- **Total Flagged Unusual Events:** {anoms['total']}")
    lines.append(
        f"- **Severity Breakdown:** Critical={anoms['severity_counts'].get('Critical', 0)}, "
        f"High={anoms['severity_counts'].get('High', 0)}, "
        f"Medium={anoms['severity_counts'].get('Medium', 0)}, "
        f"Low={anoms['severity_counts'].get('Low', 0)}"
    )
    lines.append(f"- **Disclaimer:** {anoms['disclaimer']}")
    for a in anoms["anomalies"][:10]:
        lines.append(f"  - [{a['severity']}] {a['product_name']} — {a['detected_event']}: {a['actual_value']} (Expected: {a['expected_value']})")
    lines.append("")

    lines.append("## 6. Procurement Recommendations (Human Approval Required)")
    lines.append(f"- **Recommended Reorder Count:** {recs['count']}")
    if recs["total_estimated_cost"] is not None:
        lines.append(f"- **Estimated Procurement Cost:** {format_inr(recs['total_estimated_cost'])}")
    else:
        lines.append("- **Estimated Procurement Cost:** Unavailable (no unit cost / supplier price mapped)")
    for r in recs["recommendations"][:10]:
        cost_txt = format_inr(r["estimated_total_cost"]) if r["estimated_total_cost"] is not None else "Price N/A"
        lines.append(
            f"  - {r['product_name']}: Order {r['suggested_quantity']} {r['unit']}(s) via {r['supplier_name']} ({cost_txt})"
        )
    lines.append("")

    lines.append("## 7. Supplier Analysis")
    if not sup_overview.empty:
        lines.append(f"- **Unique Suppliers Analyzed:** {len(sup_overview)}")
        for sr in sup_overview.head(10).itertuples(index=False):
            lines.append(f"  - {sr.supplier_name} ({sr.products_supplied} SKU(s))")
    else:
        lines.append("- Supplier analysis unavailable (no `Supplier / Vendor` column mapped).")
    lines.append("")

    if capabilities:
        lines.append("## 8. Feature Capability Disclosures")
        for feat, info in capabilities.items():
            status = "Active" if info.get("available") else "Limited / Unavailable"
            lines.append(f"- **{feat.replace('_', ' ').title()}:** {status} — {info.get('reason', '')}")

    return "\n".join(lines) + "\n"
