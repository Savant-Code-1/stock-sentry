"""Stock Sentry — Dynamic Multi-Agent Retail Inventory Intelligence Platform."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from agents.supervisor_agent import SupervisorAgent
from backend import data_ingestion as ingest
from backend import inventory as inv_backend
from backend import reports as report_backend
from backend import supplier as sup_backend
from config import (
    ANOMALY_DISCLAIMER,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    CACHE_TTL_SECONDS,
    DEFAULT_BUDGET_LIMIT,
    DEFAULT_FORECAST_HORIZON_DAYS,
    DEFAULT_FORECAST_WINDOW_DAYS,
    MAX_UPLOAD_FILE_SIZE_MB,
    PROCUREMENT_DISCLAIMER,
    SUPPLIER_SCORE_WEIGHTS,
)
from dashboard.components import (
    format_currency_columns,
    inject_custom_css,
    plot_category_value_bar,
    plot_daily_sales_trend,
    plot_inventory_health_donut,
    plot_missing_values_bar,
    plot_projected_stock_burndown,
    plot_sales_and_forecast,
    plot_supplier_scores,
    render_accessible_chart,
    render_anomaly_card,
    render_demo_notice,
    render_page_header,
    render_paginated_dataframe,
    render_uploaded_data_banner,
    status_pill_html,
    summarize_category_chart,
    summarize_inventory_health,
)
from database.seed import seed_database_if_empty
from utils.helpers import escape_html, format_inr
from utils.validators import ValidationError

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title=f"{APP_NAME} — Dynamic Inventory Intelligence",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

NAV_PAGES = [
    "Overview",
    "Upload Data",
    "Data Quality",
    "Anomaly Detection",
    "Demand Forecasting",
    "Inventory Intelligence",
    "Procurement",
    "Supplier Analysis",
    "Reports",
    "AI Assistant",
]


# ==============================================================================
# CACHED ANALYTICS LAYER (Eliminates redundant recomputation on Streamlit reruns)
# Keyed strictly by (db_path, analysis_version) so every mutation or new upload
# invalidates stale results immediately while isolating multi-user sessions.
# ==============================================================================
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_store_health_check(db_path: str, version: int) -> dict:
    _ = version
    return SupervisorAgent(db_path=db_path).run_store_health_check()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_sales_analytics(db_path: str, version: int) -> dict:
    _ = version
    return SupervisorAgent(db_path=db_path).inventory_agent.get_sales_analytics()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_anomaly_scan(db_path: str, version: int) -> dict:
    _ = version
    return SupervisorAgent(db_path=db_path).anomaly_agent.scan_anomalies()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_forecast_portfolio(
    db_path: str, version: int, horizon_days: int, window_days: int, method: str
) -> pd.DataFrame:
    _ = version
    return SupervisorAgent(db_path=db_path).forecasting_agent.forecast_portfolio(
        horizon_days=horizon_days, window_days=window_days, method=method
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_recommendations(db_path: str, version: int, budget_limit: Optional[float]) -> dict:
    _ = version
    return SupervisorAgent(db_path=db_path).procurement_agent.recommend_orders(
        budget_limit=budget_limit
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def cached_supplier_overview(db_path: str, version: int) -> pd.DataFrame:
    _ = version
    return SupervisorAgent(db_path=db_path).supplier_agent.get_supplier_overview()


def bump_analysis_version() -> None:
    """Invalidate cached agent results for the current session after any data write."""
    st.session_state["analysis_version"] = st.session_state.get("analysis_version", 0) + 1


def init_session_state() -> None:
    """Initialize per-user isolated Streamlit session state."""
    defaults = {
        "active_mode": "none",  # 'none' | 'uploaded' | 'demo'
        "session_db_path": None,
        "session_temp_dir": None,
        "dataset_label": None,
        "upload_file_signature": None,
        "pending_upload_bundle": None,
        "active_upload_meta": None,
        "raw_df": None,
        "cleaned_df": None,
        "quality_report": None,
        "column_mapping": {},
        "capabilities": None,
        "ingestion_warnings": [],
        "load_summary": None,
        "analysis_version": 0,
        "chat_history": [],
        "nav_page": "Overview",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def clear_active_session_data() -> None:
    """Remove isolated temp SQLite database and clear all previous agent results."""
    if st.session_state.get("session_temp_dir"):
        ingest.cleanup_session_temp_dir(st.session_state["session_temp_dir"])
    st.session_state["active_mode"] = "none"
    st.session_state["session_db_path"] = None
    st.session_state["session_temp_dir"] = None
    st.session_state["dataset_label"] = None
    st.session_state["active_upload_meta"] = None
    st.session_state["raw_df"] = None
    st.session_state["cleaned_df"] = None
    st.session_state["quality_report"] = None
    st.session_state["column_mapping"] = {}
    st.session_state["capabilities"] = None
    st.session_state["ingestion_warnings"] = []
    st.session_state["load_summary"] = None
    st.session_state["chat_history"] = []
    bump_analysis_version()


def activate_demo_dataset() -> None:
    """Explicitly load the synthetic demonstration kirana dataset into an isolated session DB."""
    clear_active_session_data()
    temp_dir = Path(tempfile.mkdtemp(prefix="stock_sentry_user_"))
    try:
        os.chmod(temp_dir, 0o700)
    except OSError:
        pass
    demo_db = temp_dir / "demo_session.db"
    seed_database_if_empty(db_path=demo_db)

    st.session_state["active_mode"] = "demo"
    st.session_state["session_temp_dir"] = str(temp_dir)
    st.session_state["session_db_path"] = str(demo_db)
    st.session_state["dataset_label"] = "Synthetic Kirana Demo Dataset (55 SKUs, 35 Days)"
    st.session_state["capabilities"] = {
        "inventory_monitoring": {"available": True, "reason": "Active on synthetic demo dataset."},
        "inventory_valuation": {"available": True, "has_cost_price": True, "has_selling_price": True, "reason": "Active on synthetic demo dataset."},
        "low_stock_alerts": {"available": True, "out_of_stock_available": True, "reason": "Active on synthetic demo dataset."},
        "overstock_detection": {"available": True, "reason": "Active on synthetic demo dataset."},
        "sales_analytics": {"available": True, "has_revenue": True, "has_dates": True, "reason": "Active on synthetic demo dataset."},
        "demand_forecasting": {"available": True, "distinct_dates": 35, "reason": "Active on 35 days of synthetic sales history."},
        "anomaly_detection": {"available": True, "reason": "Active on synthetic sales and stock movement logs."},
        "supplier_analysis": {"available": True, "reason": "Active on 12 synthetic wholesale suppliers."},
        "procurement": {"available": True, "has_supplier": True, "has_pricing": True, "reason": "Active on synthetic demo dataset."},
    }
    bump_analysis_version()


def get_active_supervisor() -> Optional[SupervisorAgent]:
    """Return SupervisorAgent bound strictly to the current session's isolated SQLite DB."""
    if st.session_state["active_mode"] == "none" or not st.session_state.get("session_db_path"):
        return None
    return SupervisorAgent(db_path=st.session_state["session_db_path"])


def render_active_source_banner() -> None:
    """Show transparent status banner indicating whether uploaded user data or demo mode is active."""
    mode = st.session_state["active_mode"]
    if mode == "uploaded":
        meta = st.session_state["active_upload_meta"]
        ls = st.session_state["load_summary"]
        render_uploaded_data_banner(
            file_name=meta["file_name"],
            file_size=meta["file_size_label"],
            products_count=ls["products_loaded"],
            loaded_at=ls["loaded_at"],
        )
    elif mode == "demo":
        render_demo_notice()


def render_no_data_gate(section_name: str) -> None:
    """Accessible empty state when no dataset is active — never displays fake numbers."""
    st.info(
        f"No dataset is loaded yet for {section_name}. Upload your own CSV or Excel (.xlsx / .xls) "
        "inventory file on the Upload Data page so all five Stock Sentry agents can analyze your business records."
    )
    c1, c2 = st.columns([1.2, 1.4])
    if c1.button("Open Upload Data Page", type="primary", use_container_width=True):
        st.session_state["nav_page"] = "Upload Data"
        st.rerun()
    if c2.button("Load Synthetic Demo Dataset Instead", use_container_width=True):
        activate_demo_dataset()
        st.rerun()


# ==============================================================================
# 1. OVERVIEW
# ==============================================================================
def render_overview(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Overview",
        "Dynamic multi-agent retail inventory control room calculated strictly from your active dataset.",
    )
    if supervisor is None:
        render_no_data_gate("Overview")
        return

    render_active_source_banner()
    db_str = str(supervisor.db_path)
    ver = st.session_state["analysis_version"]

    health = cached_store_health_check(db_str, ver)
    inv = health["inventory_summary"]
    sales = cached_sales_analytics(db_str, ver)

    # Primary Dynamic KPI Row
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Products", f"{inv['total_products']} SKUs", f"{inv['total_units']:,} available units")
    if inv["has_cost_data"]:
        retail_sub = f"Retail: {format_inr(inv['total_retail_value'])}" if inv["has_retail_data"] else "Retail price not mapped"
        c2.metric("Inventory Value (Cost)", format_inr(inv["total_cost_value"]), retail_sub)
    elif inv["has_retail_data"]:
        c2.metric("Inventory Value (Retail)", format_inr(inv["total_retail_value"]), "Unit cost not mapped")
    else:
        c2.metric("Inventory Valuation", "Not Mapped", "Map Unit Cost or Selling Price")

    c3.metric(
        "Low-Stock / Out-of-Stock",
        f"{inv['low_stock_count']} / {inv['out_of_stock_count']}",
        "Low-stock alerts active" if inv["has_reorder_points"] else "Out-of-stock only (no ROP)",
        delta_color="inverse",
    )
    c4.metric(
        "Overstock Items",
        inv["overstock_count"] if inv["has_target_stock"] else "N/A",
        "Above Target Stock" if inv["has_target_stock"] else "Target Stock not mapped",
    )
    c5.metric(
        "Anomalies / Pending POs",
        f"{health['anomaly_count']} / {health['pending_purchase_orders']}",
        f"{health['recommended_orders_count']} reorder(s) suggested",
    )

    if sales["available"]:
        st.subheader("Sales Snapshot (From Active Dataset)")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Sales Records", f"{sales['total_records']:,}")
        s2.metric("Total Units Sold", f"{sales['total_units_sold']:,}")
        s3.metric("Total Revenue", format_inr(sales["total_revenue"]) if sales["has_revenue"] else "Not Mapped")
        s4.metric("Sales Date Coverage", f"{sales['distinct_dates']} day(s)" if sales["has_dates"] else "Undated sales")

    # Accessible Charts Row (with plain-text descriptions & tabular alternatives)
    left, right = st.columns([1, 1.35])
    with left:
        health_table = pd.DataFrame(
            [
                {"Status": "Healthy", "Product Count": inv["healthy_count"]},
                {"Status": "Low Stock", "Product Count": inv["low_stock_count"]},
                {"Status": "Out of Stock", "Product Count": inv["out_of_stock_count"]},
                {"Status": "Overstock", "Product Count": inv["overstock_count"]},
            ]
        )
        render_accessible_chart(
            plot_inventory_health_donut(inv),
            summary_text=summarize_inventory_health(inv),
            table_df=health_table,
            table_label="View Inventory Health table (accessible alternative)",
        )
    with right:
        cat_tbl = format_currency_columns(inv["category_breakdown"], ["cost_value", "retail_value"])
        render_accessible_chart(
            plot_category_value_bar(
                inv["category_breakdown"],
                has_cost=inv["has_cost_data"],
                has_retail=inv["has_retail_data"],
            ),
            summary_text=summarize_category_chart(
                inv["category_breakdown"], inv["has_cost_data"], inv["has_retail_data"]
            ),
            table_df=cat_tbl,
            table_label="View Category Valuation table (accessible alternative)",
        )

    st.subheader("Live Status of All Five Connected Agents")
    rec_cost = format_inr(health["recommended_orders_cost"]) if health["recommended_orders_cost"] is not None else "Cost N/A"
    agents_df = pd.DataFrame(
        [
            {
                "Agent": "1. Inventory Management Agent",
                "Status": "Active",
                "Live Output From Active Dataset": f"{inv['total_products']} products, {inv['total_units']:,} units | {inv['out_of_stock_count']} OOS, {inv['low_stock_count']} Low, {inv['overstock_count']} Overstock",
            },
            {
                "Agent": "2. Demand Forecasting Agent",
                "Status": "Active" if health["forecast_ready"] else "Limited (Missing Dated Sales)",
                "Live Output From Active Dataset": f"{health['stockout_risk_5d_count']} product(s) projected to stock out within 5 days" if health["forecast_ready"] else health["forecast_limitation"],
            },
            {
                "Agent": "3. Anomaly Detection Agent",
                "Status": "Active",
                "Live Output From Active Dataset": f"{health['anomaly_count']} unusual event(s) flagged on active records",
            },
            {
                "Agent": "4. Supplier Analysis Agent",
                "Status": "Active" if health["supplier_count"] > 0 else "Unavailable (No Supplier Mapped)",
                "Live Output From Active Dataset": f"{health['supplier_count']} unique supplier(s) compared" if health["supplier_count"] > 0 else "Map a Supplier / Vendor column to rank suppliers",
            },
            {
                "Agent": "5. Procurement Agent",
                "Status": "Active" if health["recommended_orders_count"] > 0 or inv["has_target_stock"] or inv["has_reorder_points"] or health["forecast_ready"] else "Limited",
                "Live Output From Active Dataset": f"{health['recommended_orders_count']} reorder recommendation(s) ({rec_cost}) — Human approval mandatory",
            },
        ]
    )
    st.dataframe(agents_df, use_container_width=True, hide_index=True)


# ==============================================================================
# 2. UPLOAD DATA
# ==============================================================================
def render_upload_data() -> None:
    render_page_header(
        "Upload Data",
        "Upload your own CSV or Excel (.xlsx / .xls) business file, inspect quality, map columns, and run all five agents dynamically.",
    )

    if st.session_state["active_mode"] != "none":
        render_active_source_banner()
        if st.button("Clear Active Dataset and Reset Session Analysis"):
            clear_active_session_data()
            st.session_state["pending_upload_bundle"] = None
            st.session_state["upload_file_signature"] = None
            st.success("Previous dataset and all session analysis cleared cleanly.")
            st.rerun()

    st.subheader("Step 1: Upload Your Inventory, Sales, or Supplier File")
    uploaded = st.file_uploader(
        f"Drag and drop a CSV or Excel file here (Max {MAX_UPLOAD_FILE_SIZE_MB:.0f} MB)",
        type=["csv", "xlsx", "xls"],
        help="Supported formats: CSV (.csv), Excel (.xlsx), and legacy Excel (.xls). Files are processed strictly inside your current session and never overwrite original project CSVs.",
    )

    if uploaded is not None:
        raw_bytes = uploaded.getvalue()
        file_sig = f"{uploaded.name}:{len(raw_bytes)}"

        selected_sheet = 0
        ext = Path(uploaded.name).suffix.lower()
        if ext in {".xlsx", ".xls"}:
            try:
                sheets = ingest.list_excel_sheets(raw_bytes, filename=uploaded.name)
                if len(sheets) > 1:
                    selected_sheet = st.selectbox(
                        "Select Excel Worksheet *",
                        sheets,
                        key="excel_sheet_picker",
                        help="Choose which tab in your Excel workbook contains the inventory/sales table.",
                    )
                elif len(sheets) == 1:
                    selected_sheet = sheets[0]
            except ingest.DataIngestionError as exc:
                st.error(f"Upload failed: {exc}")
                return

        bundle_sig = f"{file_sig}:{selected_sheet}"
        if st.session_state.get("upload_file_signature") != bundle_sig:
            try:
                bundle = ingest.read_uploaded_file(raw_bytes, filename=uploaded.name, sheet_name=selected_sheet)
                quality = ingest.analyze_dataframe_quality(bundle["raw_df"])
                auto_map = ingest.suggest_column_mapping(list(bundle["raw_df"].columns))
                st.session_state["pending_upload_bundle"] = {
                    "meta": bundle,
                    "quality": quality,
                    "auto_mapping": auto_map,
                }
                st.session_state["upload_file_signature"] = bundle_sig
            except ingest.DataIngestionError as exc:
                st.error(f"Validation Error: {exc}")
                return

        pending = st.session_state["pending_upload_bundle"]
        meta = pending["meta"]
        quality = pending["quality"]
        raw_df = meta["raw_df"]

        st.success(f"Upload Status: Ready for Column Mapping — {meta['file_name']} ({meta['file_size_label']}, {meta['file_type']})")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("File Size", meta["file_size_label"])
        m2.metric("Total Rows", f"{quality['total_rows']:,}")
        m3.metric("Total Columns", quality["total_columns"])
        m4.metric("Missing Cells", f"{quality['total_missing_cells']:,}", f"{quality['completeness_pct']}% complete")
        m5.metric("Duplicate Rows", quality["duplicate_rows_count"])

        for w in meta["read_warnings"] + quality["warnings"]:
            st.warning(f"Warning: {w}")

        with st.expander("Preview Uploaded Data (First 20 Unmodified Rows)", expanded=True):
            st.dataframe(raw_df.head(20), use_container_width=True)

        with st.expander("Column Information and Missing Values Breakdown", expanded=False):
            st.dataframe(quality["column_info_df"], use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Step 2: Intelligent Column Mapping")
        st.caption(
            "Fields marked with * are required (at least Product Name or Product ID / SKU). "
            "Leave unavailable columns as '— Not Available —'; missing business data is never fabricated."
        )

        not_avail = "— Not Available —"
        col_options = [not_avail] + list(raw_df.columns)
        auto_map = pending["auto_mapping"]
        user_mapping: dict[str, Optional[str]] = {}

        groups = []
        for f in ingest.CANONICAL_FIELDS:
            if f["group"] not in groups:
                groups.append(f["group"])

        for grp in groups:
            st.markdown(f"### {grp}")
            grp_fields = [f for f in ingest.CANONICAL_FIELDS if f["group"] == grp]
            cols_ui = st.columns(3)
            for idx, field in enumerate(grp_fields):
                suggested = auto_map.get(field["key"])
                default_idx = col_options.index(suggested) if suggested in col_options else 0
                req_star = " *" if field["key"] in {"product_name", "current_stock"} else ""
                choice = cols_ui[idx % 3].selectbox(
                    f"{field['label']}{req_star}",
                    options=col_options,
                    index=default_idx,
                    help=field["required_hint"],
                    key=f"map_{bundle_sig}_{field['key']}",
                )
                user_mapping[field["key"]] = None if choice == not_avail else choice

        validation = ingest.validate_and_preprocess_dataset(raw_df, user_mapping)

        st.divider()
        st.subheader("Step 3: Validation and Feature Capability Preview")
        if not validation["valid"]:
            for err in validation["errors"]:
                st.error(f"Validation Error: {err}")
        else:
            for warn in validation["warnings"]:
                st.warning(f"Preprocessing Notice: {warn}")

            cap_rows = []
            for feat_key, info in validation["capabilities"].items():
                cap_rows.append(
                    {
                        "Stock Sentry Module": feat_key.replace("_", " ").title(),
                        "Status": "Active" if info["available"] else "Limited / Unavailable",
                        "Explanation": info["reason"],
                    }
                )
            st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

            if st.button("Apply Column Mapping and Run All 5 Agents", type="primary", use_container_width=True):
                prev_dir = st.session_state.get("session_temp_dir")
                try:
                    load_res = ingest.create_isolated_session_db(
                        cleaned_df=validation["cleaned_df"],
                        capabilities=validation["capabilities"],
                        source_filename=meta["file_name"],
                        previous_temp_dir=prev_dir,
                    )
                except Exception:
                    logger.exception("Failed to create isolated session database")
                    st.error("Unable to initialize the session database for this file. Please verify the file contents and try again.")
                    return

                st.session_state["active_mode"] = "uploaded"
                st.session_state["session_temp_dir"] = load_res["temp_dir"]
                st.session_state["session_db_path"] = load_res["db_path"]
                st.session_state["dataset_label"] = f"{meta['file_name']} ({meta['file_size_label']})"
                st.session_state["active_upload_meta"] = meta
                st.session_state["raw_df"] = raw_df.copy()
                st.session_state["cleaned_df"] = validation["cleaned_df"].copy()
                st.session_state["quality_report"] = quality
                st.session_state["column_mapping"] = validation["active_mapping"]
                st.session_state["capabilities"] = validation["capabilities"]
                st.session_state["ingestion_warnings"] = meta["read_warnings"] + quality["warnings"] + validation["warnings"]
                st.session_state["load_summary"] = load_res
                st.session_state["chat_history"] = []
                bump_analysis_version()
                st.success(
                    f"Loaded {load_res['products_loaded']} products, {load_res['sales_loaded']} sales rows, "
                    f"{load_res['suppliers_loaded']} supplier offers, and {load_res['movements_loaded']} stock movements "
                    "into your isolated session workspace. All 5 agents are now analyzing your data."
                )

    st.divider()
    with st.expander("Optional: Load Built-In Synthetic Kirana Demo Dataset"):
        st.caption(
            "Want to explore Stock Sentry before uploading your own CSV/Excel file? "
            "Load the synthetic demonstration kirana dataset into an isolated demo session. "
            "Uploading your own file later completely replaces it."
        )
        if st.button("Load Synthetic Demo Dataset"):
            activate_demo_dataset()
            st.success("Synthetic demo dataset loaded into an isolated session.")
            st.rerun()


# ==============================================================================
# 3. DATA QUALITY
# ==============================================================================
def render_data_quality(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Data Quality",
        "Inspect schema profile, missing values, duplicate rows, preprocessing warnings, and active column mappings.",
    )
    if supervisor is None:
        render_no_data_gate("Data Quality")
        return

    render_active_source_banner()

    if st.session_state["active_mode"] == "demo":
        prod_df = supervisor.inventory_agent.list_products()
        quality = ingest.analyze_dataframe_quality(prod_df)
        st.caption("Showing Data Quality profile for the active Synthetic Demo products table.")
        raw_df = prod_df
        cleaned_df = prod_df
        warnings_list = quality["warnings"]
    else:
        quality = st.session_state["quality_report"]
        raw_df = st.session_state["raw_df"]
        cleaned_df = st.session_state["cleaned_df"]
        warnings_list = st.session_state.get("ingestion_warnings", [])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Rows", f"{quality['total_rows']:,}")
    c2.metric("Total Columns", quality["total_columns"])
    c3.metric("Completeness", f"{quality['completeness_pct']}%", f"{quality['total_missing_cells']:,} missing cells")
    c4.metric("Exact Duplicate Rows", quality["duplicate_rows_count"])

    if warnings_list:
        st.subheader("Validation Warnings and Disclosures")
        for w in warnings_list:
            st.warning(f"Notice: {w}")

    tab_cols, tab_missing, tab_dups, tab_preview = st.tabs(
        ["Column Information", "Missing Values Report", "Duplicate Detection", "Original vs Cleaned Preview"]
    )

    with tab_cols:
        st.dataframe(quality["column_info_df"], use_container_width=True, hide_index=True)
        if st.session_state.get("column_mapping"):
            st.subheader("Active Column Mapping")
            map_df = pd.DataFrame(
                [
                    {"Stock Sentry Field": ingest.CANONICAL_FIELD_MAP[k]["label"], "Mapped User Column": v}
                    for k, v in st.session_state["column_mapping"].items()
                ]
            )
            st.dataframe(map_df, use_container_width=True, hide_index=True)

    with tab_missing:
        miss_df = quality["missing_summary_df"]
        if miss_df.empty:
            st.success("Zero missing cells detected across all columns.")
        else:
            top_m = miss_df.iloc[0]
            render_accessible_chart(
                plot_missing_values_bar(miss_df),
                summary_text=(
                    f"{len(miss_df)} column(s) contain missing values. Highest missing column is "
                    f"'{top_m['Original Column']}' with {int(top_m['Missing'])} missing cells ({top_m['Missing (%)']}%)."
                ),
                table_df=miss_df,
                table_label="View Missing Values table",
            )

    with tab_dups:
        if quality["duplicate_rows_count"] == 0:
            st.success("No exact duplicate rows detected.")
        else:
            st.warning(
                f"Found {quality['duplicate_rows_count']} exact duplicate row(s). "
                "Duplicates are deduplicated before loading into the session database while preserving your original uploaded preview."
            )
            render_paginated_dataframe(quality["duplicate_preview_df"], key_prefix="dup_rows")

    with tab_preview:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("### Original Uploaded Data (First 25 Rows)")
            st.dataframe(raw_df.head(25), use_container_width=True)
        with col_b:
            st.markdown("### Cleaned and Normalized Data (First 25 Rows)")
            st.dataframe(cleaned_df.head(25), use_container_width=True)


# ==============================================================================
# 4. ANOMALY DETECTION
# ==============================================================================
def render_anomaly_detection(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Anomaly Detection",
        "Statistical and rule-based anomaly analysis powered by Stock Sentry's Anomaly Detection Agent.",
    )
    if supervisor is None:
        render_no_data_gate("Anomaly Detection")
        return

    render_active_source_banner()
    st.warning(f"Important Disclaimer: {ANOMALY_DISCLAIMER}")

    scan = cached_anomaly_scan(str(supervisor.db_path), st.session_state["analysis_version"])
    anoms = scan["anomalies"]
    counts = scan["severity_counts"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Flagged Events", scan["total"])
    c2.metric("Critical Severity", counts.get("Critical", 0))
    c3.metric("High Severity", counts.get("High", 0))
    c4.metric("Medium / Low Severity", counts.get("Medium", 0) + counts.get("Low", 0))

    with st.expander("Checks Executed vs Skipped on Active Dataset", expanded=bool(scan["checks_skipped"])):
        if scan["checks_executed"]:
            st.markdown("**Executed Checks:**")
            for chk in scan["checks_executed"]:
                st.markdown(f"- [Active] {chk}")
        if scan["checks_skipped"]:
            st.markdown("**Skipped Checks (Due to Missing Columns in Uploaded Data):**")
            for skp in scan["checks_skipped"]:
                st.markdown(f"- [Skipped] {skp}")

    if not anoms:
        st.success("No unusual inventory, pricing, or sales anomalies detected across the checks that ran on your active dataset.")
        return

    sev_filter = st.selectbox(
        "Filter Anomalies by Severity",
        ["All", "Critical", "High", "Medium", "Low"],
        help="Show all flagged events or filter to a specific severity level.",
    )
    filtered = [a for a in anoms if sev_filter == "All" or a["severity"] == sev_filter]

    if not filtered:
        st.info(f"No anomalies with severity '{sev_filter}'.")
        return

    # Paginate card list if many anomalies exist on large datasets
    max_cards = 20
    if len(filtered) > max_cards:
        st.caption(f"Displaying top {max_cards} of {len(filtered)} anomalies as cards. Use the paginated table below to inspect all records.")
    for a in filtered[:max_cards]:
        render_anomaly_card(a)

    with st.expander("Accessible Tabular Anomaly Report (All Flagged Events)", expanded=len(filtered) > max_cards):
        anom_tbl = pd.DataFrame(filtered)[
            ["product_id", "product_name", "detected_event", "expected_value", "actual_value", "severity", "detection_time", "suggested_action"]
        ]
        render_paginated_dataframe(anom_tbl, key_prefix="anom_tbl")


# ==============================================================================
# 5. DEMAND FORECASTING
# ==============================================================================
def render_demand_forecasting(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Demand Forecasting",
        "Statistical baseline forecasting (Exponential Smoothing & Moving Average) powered by the Demand Forecasting Agent.",
    )
    if supervisor is None:
        render_no_data_gate("Demand Forecasting")
        return

    render_active_source_banner()

    readiness = supervisor.forecasting_agent.check_readiness()
    if not readiness["ready"]:
        st.warning(f"Demand Forecasting Cannot Operate on This Dataset: {readiness['reason']}")
        st.info(
            "Stock Sentry does not fabricate forecasts. Upload a dataset containing both "
            "Units Sold / Sales and Order / Transaction Date across at least 2 distinct dates."
        )
        return

    st.info(
        f"Forecast Transparency: {readiness['reason']} Uses transparent statistical baselines "
        "(Exponential Smoothing or Simple Moving Average) — not deep machine-learning predictions."
    )

    all_df = supervisor.inventory_agent.list_products()
    if all_df.empty:
        st.warning("No products available for forecasting.")
        return

    prod_map = {f"{r['product_id']} — {r['product_name']} ({r['category']})": r["product_id"] for _, r in all_df.iterrows()}

    c1, c2, c3, c4 = st.columns([2.2, 1.3, 1, 1])
    selected = c1.selectbox("Select Product to Forecast *", list(prod_map.keys()), help="Pick a product to view its historical sales curve and demand baseline.")
    method_label = c2.selectbox("Baseline Forecast Method", ["Exponential Smoothing (EMA)", "Simple Moving Average (SMA)"])
    method = "ema" if "EMA" in method_label else "sma"
    horizon = c3.slider("Forecast Horizon (Days)", min_value=3, max_value=30, value=DEFAULT_FORECAST_HORIZON_DAYS)
    window = c4.slider("Moving Average Window (Days)", min_value=3, max_value=30, value=DEFAULT_FORECAST_WINDOW_DAYS)

    fc = supervisor.forecasting_agent.forecast_sku(
        product_id=prod_map[selected],
        horizon_days=horizon,
        window_days=window,
        method=method,
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Current Stock", f"{fc['current_stock']} {fc['unit']}")
    m2.metric("Average Daily Sales", f"{fc['avg_daily_sales']:.2f} / day")
    m3.metric("Forecast Daily Demand", f"{fc['forecast_daily_demand']:.2f} / day", f"{horizon}d total: {fc['forecast_total_demand']}")
    days_txt = "N/A" if fc["days_remaining"] is None else f"{fc['days_remaining']} days"
    m4.metric("Est. Days Remaining", days_txt)
    m5.metric("Calculated Reorder Point", f"{fc['calculated_reorder_point']} {fc['unit']}", f"Risk: {fc['stockout_risk']}")

    st.markdown(
        f"<strong>Stockout Risk:</strong> {status_pill_html(fc['stockout_risk'])} &nbsp;|&nbsp; "
        f"<strong>Reorder Timing:</strong> {escape_html(fc['reorder_timing'])}",
        unsafe_allow_html=True,
    )

    ch1, ch2 = st.columns([1.45, 1])
    with ch1:
        render_accessible_chart(
            plot_sales_and_forecast(fc),
            summary_text=(
                f"{fc['product_name']} averaged {fc['avg_daily_sales']:.2f} units/day over {fc['history_days']} days. "
                f"Using {fc['method_label']}, projected demand is {fc['forecast_daily_demand']:.2f} units/day "
                f"({fc['forecast_total_demand']} units over the next {horizon} days)."
            ),
            table_df=fc["forecast_df"],
            table_label="View Forecast Values table (accessible alternative)",
        )
    with ch2:
        render_accessible_chart(
            plot_projected_stock_burndown(fc),
            summary_text=(
                f"Starting from {fc['current_stock']} units on hand, stockout risk is rated {fc['stockout_risk']} "
                f"(estimated days remaining: {days_txt}; calculated reorder threshold: {fc['calculated_reorder_point']} units)."
            ),
            table_df=fc["forecast_df"][["date", "projected_stock"]],
            table_label="View Projected Burn-Down table (accessible alternative)",
        )

    with st.expander("Forecast Formulas and Assumptions", expanded=False):
        st.markdown(
            """
            - **Average Daily Sales** = `Total Units Sold / Number of Days`
            - **Days Remaining** = `Current Stock / Forecast Daily Demand`
            - **Reorder Point** = `(Average Daily Demand × Supplier Lead Time) + Safety Stock`
            """
        )
        for item in fc["assumptions"]:
            st.markdown(f"- {item}")

    st.subheader("Portfolio Stockout Risk Watchlist")
    portfolio_df = cached_forecast_portfolio(
        str(supervisor.db_path), st.session_state["analysis_version"], horizon, window, method
    )
    render_paginated_dataframe(portfolio_df, key_prefix="fc_portfolio", caption_prefix="All forecasted SKUs")


# ==============================================================================
# 6. INVENTORY INTELLIGENCE
# ==============================================================================
def render_inventory_intelligence(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Inventory Intelligence",
        "Explore catalog, low-stock alerts, overstock detection, sales analytics, SKU edits, and stock movements.",
    )
    if supervisor is None:
        render_no_data_gate("Inventory Intelligence")
        return

    render_active_source_banner()
    db_path = supervisor.db_path
    db_str = str(db_path)
    ver = st.session_state["analysis_version"]
    summary = cached_store_health_check(db_str, ver)["inventory_summary"]

    tab_cat, tab_alerts, tab_sales, tab_stock, tab_add_edit, tab_moves = st.tabs(
        [
            "Product Catalog",
            "Low-Stock & Overstock",
            "Sales Analytics",
            "Update Stock",
            "Add / Edit Product",
            "Stock Movements",
        ]
    )

    with tab_cat:
        f1, f2, f3 = st.columns([2, 1, 1])
        search = f1.text_input(
            "Search products by name, ID, or category",
            placeholder="Type product name, SKU, or category...",
            help="Case-insensitive substring search across your active dataset.",
        )
        categories = ["All"] + inv_backend.get_categories(db_path=db_path)
        cat_filter = f2.selectbox("Filter by Category", categories)
        status_filter = f3.selectbox(
            "Filter by Stock Status",
            ["All", "Out of Stock", "Low Stock", "Overstock", "Healthy", "In Stock (No ROP)"],
        )

        df = supervisor.inventory_agent.list_products(
            search=search, category=cat_filter, status_filter=status_filter
        )
        if df.empty:
            st.info("No products match the selected filters.")
        else:
            val_caption = (
                f"Filtered Cost Value: {format_inr(df['inventory_value_cost'].dropna().sum())}"
                if df["inventory_value_cost"].notna().any()
                else "Unit cost not mapped"
            )
            view = df[
                [
                    "product_id", "product_name", "category", "current_stock", "unit",
                    "cost_price", "selling_price", "reorder_point", "target_stock",
                    "inventory_value_cost", "stock_status", "is_overstock",
                ]
            ].rename(
                columns={
                    "product_id": "ID",
                    "product_name": "Product Name",
                    "category": "Category",
                    "current_stock": "Stock",
                    "unit": "Unit",
                    "cost_price": "Cost Price",
                    "selling_price": "Selling Price",
                    "reorder_point": "Reorder Pt",
                    "target_stock": "Target Stock",
                    "inventory_value_cost": "Cost Value",
                    "stock_status": "Status",
                    "is_overstock": "Overstock?",
                }
            )
            view = format_currency_columns(view, ["Cost Price", "Selling Price", "Cost Value"])
            render_paginated_dataframe(view, key_prefix="inv_catalog", caption_prefix=val_caption)

    with tab_alerts:
        c_left, c_right = st.columns(2)
        with c_left:
            st.subheader("Low-Stock and Out-of-Stock Alerts")
            if not summary["has_reorder_points"]:
                st.info("Reorder Point was not mapped in your upload — showing Out-of-Stock (0 units) products only.")
            low_df = supervisor.inventory_agent.get_low_stock_alerts(include_out_of_stock=True)["products"]
            if low_df.empty:
                st.success("No low-stock or out-of-stock products detected.")
            else:
                render_paginated_dataframe(
                    low_df[["product_id", "product_name", "current_stock", "reorder_point", "stock_status"]],
                    key_prefix="low_alerts",
                )
        with c_right:
            st.subheader("Overstock Detection")
            if not summary["has_target_stock"]:
                st.warning("Target Stock / Max Stock is not mapped in this dataset. Overstock thresholds are never guessed.")
            else:
                ov_df = supervisor.inventory_agent.get_overstock_alerts()["products"]
                if ov_df.empty:
                    st.success("No overstocked items detected (all products are at or below Target Stock).")
                else:
                    render_paginated_dataframe(
                        ov_df[["product_id", "product_name", "current_stock", "target_stock", "excess_units"]],
                        key_prefix="ov_alerts",
                    )

    with tab_sales:
        sales = cached_sales_analytics(db_str, ver)
        if not sales["available"]:
            st.warning("Sales analytics unavailable: no Units Sold / Sales column was mapped in your uploaded file.")
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Sales Records", f"{sales['total_records']:,}")
            k2.metric("Total Units Sold", f"{sales['total_units_sold']:,}")
            k3.metric("Total Revenue", format_inr(sales["total_revenue"]) if sales["has_revenue"] else "Selling Price Not Mapped")
            k4.metric("Distinct Sales Dates", sales["distinct_dates"] if sales["has_dates"] else "Undated")

            if sales["has_dates"] and not sales["daily_trend"].empty:
                dt_df = sales["daily_trend"]
                peak_row = dt_df.sort_values("units_sold", ascending=False).iloc[0]
                render_accessible_chart(
                    plot_daily_sales_trend(dt_df),
                    summary_text=(
                        f"Total of {sales['total_units_sold']:,} units sold across {sales['distinct_dates']} day(s). "
                        f"Peak daily sales occurred on {peak_row['date']} with {int(peak_row['units_sold'])} units sold."
                    ),
                    table_df=format_currency_columns(dt_df, ["revenue"]),
                    table_label="View Daily Sales table (accessible alternative)",
                )

            st.subheader("Top-Selling Products")
            tp = format_currency_columns(sales["top_products"], ["revenue"])
            render_paginated_dataframe(tp, key_prefix="top_sales")

    with tab_stock:
        st.subheader("Record Stock Movement (Session Isolated)")
        st.caption("Fields marked with * are required.")
        all_df = supervisor.inventory_agent.list_products()
        if all_df.empty:
            st.warning("No products loaded.")
        else:
            options = {
                f"{r['product_id']} — {r['product_name']} (Current: {r['current_stock']} {r['unit']})": r["product_id"]
                for _, r in all_df.iterrows()
            }
            with st.form("stock_update_form"):
                selected_label = st.selectbox("Select Product *", list(options.keys()))
                c1, c2 = st.columns(2)
                mtype = c1.selectbox(
                    "Movement Type *",
                    ["RESTOCK", "SALE", "ADJUSTMENT", "DAMAGE", "RETURN"],
                    help="RESTOCK/RETURN increase stock; SALE/DAMAGE reduce stock; ADJUSTMENT accepts positive or negative deltas.",
                )
                qty = c2.number_input("Quantity * (negative allowed only for ADJUSTMENT)", value=5, step=1)
                remarks = st.text_input("Remarks (Optional)", placeholder="e.g., Supplier receipt / Counter sale / Recount")
                if st.form_submit_button("Apply Stock Movement", type="primary"):
                    try:
                        res = supervisor.inventory_agent.record_stock_change(
                            product_id=options[selected_label],
                            movement_type=mtype,
                            quantity=int(qty),
                            remarks=remarks,
                        )
                        bump_analysis_version()
                        st.success(f"Updated {res['product_name']}: {res['previous_stock']} → {res['new_stock']} ({res['delta']:+d}).")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")

    with tab_add_edit:
        sub_add, sub_edit = st.columns(2)
        with sub_add:
            st.subheader("Add New Product")
            st.caption("Fields marked with * are required.")
            next_pid = inv_backend.generate_next_product_id(db_path=db_path)
            with st.form("add_product_form"):
                pid = st.text_input("Product ID *", value=next_pid)
                pname = st.text_input("Product Name *", placeholder="e.g., Organic Almonds 500g")
                category = st.text_input("Category *", value="Grocery")
                unit = st.text_input("Unit *", value="pack")
                cost_price = st.number_input("Cost Price (₹) *", min_value=1.0, value=100.0, step=1.0)
                selling_price = st.number_input("Selling Price (₹) *", min_value=1.0, value=120.0, step=1.0)
                cur_stock = st.number_input("Opening Stock *", min_value=0, value=20, step=1)
                rop = st.number_input("Reorder Point *", min_value=0, value=10, step=1)
                target = st.number_input("Target Stock *", min_value=1, value=40, step=1)
                if st.form_submit_button("Create Product", type="primary"):
                    try:
                        created = supervisor.inventory_agent.add_product(
                            product_id=pid, product_name=pname, category=category,
                            current_stock=int(cur_stock), unit=unit,
                            selling_price=float(selling_price), cost_price=float(cost_price),
                            reorder_point=int(rop), target_stock=int(target),
                        )
                        bump_analysis_version()
                        st.success(f"Added {created['product_name']} ({created['product_id']}).")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")

        with sub_edit:
            st.subheader("Edit Existing Product")
            all_df = supervisor.inventory_agent.list_products()
            if all_df.empty:
                st.info("No products available.")
            else:
                edit_map = {f"{r['product_id']} — {r['product_name']}": r for _, r in all_df.iterrows()}
                pick_key = st.selectbox("Select Product to Edit *", list(edit_map.keys()))
                row = edit_map[pick_key]
                with st.form("edit_product_form"):
                    e_name = st.text_input("Product Name *", value=str(row["product_name"]))
                    e_cat = st.text_input("Category *", value=str(row["category"]))
                    e_unit = st.text_input("Unit *", value=str(row["unit"]))
                    e_cost = st.number_input("Cost Price (₹) *", min_value=1.0, value=float(row["cost_price"]) if pd.notna(row["cost_price"]) else 100.0, step=1.0)
                    e_sell = st.number_input("Selling Price (₹) *", min_value=1.0, value=float(row["selling_price"]) if pd.notna(row["selling_price"]) else 120.0, step=1.0)
                    e_rop = st.number_input("Reorder Point *", min_value=0, value=int(row["reorder_point"]) if pd.notna(row["reorder_point"]) else 10, step=1)
                    e_target = st.number_input("Target Stock *", min_value=1, value=int(row["target_stock"]) if pd.notna(row["target_stock"]) else max(20, int(e_rop)), step=1)
                    if st.form_submit_button("Save Product Changes", type="primary"):
                        try:
                            updated = supervisor.inventory_agent.edit_product(
                                product_id=row["product_id"], product_name=e_name, category=e_cat,
                                unit=e_unit, selling_price=float(e_sell), cost_price=float(e_cost),
                                reorder_point=int(e_rop), target_stock=int(e_target),
                            )
                            bump_analysis_version()
                            st.success(f"Updated {updated['product_name']}.")
                            st.rerun()
                        except ValidationError as exc:
                            st.error(f"Validation Error: {exc}")

    with tab_moves:
        mov_df = supervisor.inventory_agent.get_movements(limit=500)
        if mov_df.empty:
            st.info("No stock movements recorded yet for this dataset.")
        else:
            render_paginated_dataframe(mov_df, key_prefix="inv_moves", caption_prefix="Stock movement ledger")


# ==============================================================================
# 7. PROCUREMENT
# ==============================================================================
def render_procurement(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Procurement",
        "Dynamic purchase-order recommendations, human approval queue, and immutable audit trail.",
    )
    if supervisor is None:
        render_no_data_gate("Procurement")
        return

    render_active_source_banner()
    st.info(f"Governance Policy: {PROCUREMENT_DISCLAIMER}")

    tab_recs, tab_approval, tab_history, tab_audit = st.tabs(
        ["Recommended Orders", "Approval Queue", "Order History", "Audit Trail"]
    )

    with tab_recs:
        c1, c2 = st.columns([1.2, 2])
        budget = c1.number_input(
            "Budget Limit (₹, enter 0 for No Limit)",
            min_value=0.0,
            value=float(DEFAULT_BUDGET_LIMIT),
            step=2500.0,
            help="Orders exceeding the remaining budget are flagged transparently.",
        )
        budget_arg = budget if budget > 0 else None
        rec_res = cached_recommendations(str(supervisor.db_path), st.session_state["analysis_version"], budget_arg)
        recs = rec_res["recommendations"]
        cost_label = format_inr(rec_res["total_estimated_cost"]) if rec_res["total_estimated_cost"] is not None else "Unit Cost Not Mapped"
        c2.metric("Recommended Reorders", f"{rec_res['count']} SKU(s)", f"Est. Total: {cost_label}")

        if not recs:
            st.info(
                "No purchase orders are currently recommended. Either all SKUs are stocked above thresholds, "
                "or your uploaded dataset does not include Target Stock, Reorder Point, or dated sales history."
            )
        else:
            rec_df = pd.DataFrame(recs)
            display = rec_df[
                [
                    "product_id", "product_name", "current_stock", "incoming_stock",
                    "suggested_quantity", "supplier_name", "unit_price",
                    "estimated_total_cost", "expected_delivery_days", "within_budget", "reason",
                ]
            ].rename(
                columns={
                    "product_id": "ID",
                    "product_name": "Product",
                    "current_stock": "Stock",
                    "incoming_stock": "Incoming",
                    "suggested_quantity": "Suggested Qty",
                    "supplier_name": "Recommended Supplier",
                    "unit_price": "Unit Price",
                    "estimated_total_cost": "Est. Cost",
                    "expected_delivery_days": "Delivery (d)",
                    "within_budget": "Within Budget",
                    "reason": "Reason",
                }
            )
            display = format_currency_columns(display, ["Unit Price", "Est. Cost"])
            render_paginated_dataframe(display, key_prefix="proc_recs")

            st.subheader("Generate Draft Purchase Order (Pending Approval)")
            rec_map = {
                f"{r['product_id']} — {r['product_name']} | Qty: {r['suggested_quantity']} via {r['supplier_name']} ({format_inr(r['estimated_total_cost']) if r['estimated_total_cost'] is not None else 'Price N/A'})": r
                for r in recs
            }
            chosen = st.selectbox("Choose Recommendation to Draft *", list(rec_map.keys()))
            rec = rec_map[chosen]
            col_a, col_b = st.columns([1, 2])
            draft_qty = col_a.number_input("Order Quantity *", min_value=1, value=int(rec["suggested_quantity"]), step=1)
            draft_note = col_b.text_input("Reason / Notes", value=rec["reason"])
            if st.button("Create Draft Purchase Order", type="primary"):
                try:
                    po = supervisor.procurement_agent.draft_purchase_order(
                        product_id=rec["product_id"],
                        supplier_id=rec["supplier_id"],
                        quantity=int(draft_qty),
                        reason=draft_note,
                        created_by="Procurement Agent (User Triggered)",
                        budget_limit=budget_arg,
                    )
                    bump_analysis_version()
                    st.success(f"Created draft PO {po['order_id']} — Status: {po['status']}.")
                    st.rerun()
                except ValidationError as exc:
                    st.error(f"Validation Error: {exc}")

    with tab_approval:
        orders_df = supervisor.procurement_agent.list_orders()
        actionable = orders_df[orders_df["status"].isin(["Pending Approval", "Modified"])] if not orders_df.empty else pd.DataFrame()

        if actionable.empty:
            st.info("No purchase orders awaiting human approval.")
        else:
            po_map = {
                f"{r['order_id']} | {r['product_name']} — {r['quantity']} units via {r['supplier_name']} [{r['status']}]": r
                for _, r in actionable.iterrows()
            }
            pick = st.selectbox("Select Purchase Order to Review *", list(po_map.keys()))
            po = po_map[pick]
            cost_disp = format_inr(po["total_cost"]) if pd.notna(po["total_cost"]) else "Price N/A"
            del_disp = f"{int(po['delivery_days'])} day(s)" if pd.notna(po["delivery_days"]) else "N/A"
            st.markdown(
                f"<strong>Order ID:</strong> <code>{escape_html(po['order_id'])}</code> | "
                f"<strong>Status:</strong> {status_pill_html(po['status'])} | "
                f"<strong>Total Cost:</strong> {escape_html(cost_disp)} | "
                f"<strong>Delivery:</strong> {escape_html(del_disp)}",
                unsafe_allow_html=True,
            )
            st.write(f"**Reason:** {po['reason']}")

            actor = st.text_input("Reviewer Name *", value="Store Owner", help="Recorded in the immutable audit trail.")
            review_note = st.text_input("Review Note (Optional)", placeholder="Optional note for audit trail")

            a1, a2 = st.columns(2)
            with a1:
                if st.button("Approve Purchase Order", type="primary", use_container_width=True):
                    try:
                        supervisor.procurement_agent.approve_order(po["order_id"], actor=actor or "Store Owner", note=review_note)
                        bump_analysis_version()
                        st.success(f"Order {po['order_id']} approved and logged.")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")
            with a2:
                if st.button("Reject Purchase Order", use_container_width=True):
                    try:
                        supervisor.procurement_agent.reject_order(po["order_id"], actor=actor or "Store Owner", note=review_note)
                        bump_analysis_version()
                        st.warning(f"Order {po['order_id']} rejected and logged.")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")

            st.divider()
            st.subheader("Modify Quantity or Supplier Before Approval")
            alt_sup = supervisor.supplier_agent.list_suppliers(product_id=po["product_id"])
            sup_opts = {"Keep Current Supplier": None}
            for _, sr in alt_sup.iterrows():
                p_txt = format_inr(sr["unit_price"]) if pd.notna(sr["unit_price"]) else "Price N/A"
                sup_opts[f"{sr['supplier_id']} — {sr['supplier_name']} ({p_txt})"] = sr["supplier_id"]

            with st.form("modify_po_form"):
                mc1, mc2 = st.columns(2)
                new_qty = mc1.number_input("New Quantity *", min_value=1, value=int(po["quantity"]), step=1)
                new_sup_lbl = mc2.selectbox("Supplier", list(sup_opts.keys()))
                if st.form_submit_button("Save Order Modification"):
                    try:
                        supervisor.procurement_agent.modify_order(
                            order_id=po["order_id"],
                            new_quantity=int(new_qty),
                            new_supplier_id=sup_opts[new_sup_lbl],
                            actor=actor or "Store Owner",
                            note=review_note,
                        )
                        bump_analysis_version()
                        st.success(f"Order {po['order_id']} modified and logged.")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")

    with tab_history:
        st_pick = st.selectbox("Filter Order History by Status", ["All", "Pending Approval", "Modified", "Approved", "Rejected"])
        hist_df = supervisor.procurement_agent.list_orders(status_filter=st_pick)
        if hist_df.empty:
            st.info("No purchase orders recorded yet.")
        else:
            render_paginated_dataframe(format_currency_columns(hist_df, ["unit_price", "total_cost"]), key_prefix="po_hist")

    with tab_audit:
        logs_df = supervisor.procurement_agent.list_audit_logs(limit=500)
        if logs_df.empty:
            st.info("No audit log records yet.")
        else:
            render_paginated_dataframe(logs_df, key_prefix="po_audit")


# ==============================================================================
# 8. SUPPLIER ANALYSIS
# ==============================================================================
def render_supplier_analysis(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Supplier Analysis",
        "Compare and rank suppliers from your active dataset using transparent scoring across ONLY available metrics.",
    )
    if supervisor is None:
        render_no_data_gate("Supplier Analysis")
        return

    render_active_source_banner()
    db_path = supervisor.db_path
    all_sup = supervisor.supplier_agent.list_suppliers()
    all_products = supervisor.inventory_agent.list_products()
    prod_map = {f"{r['product_id']} — {r['product_name']}": r["product_id"] for _, r in all_products.iterrows()}

    st.caption(
        f"Base Weights (re-normalized automatically if any column is omitted): "
        f"Price {int(SUPPLIER_SCORE_WEIGHTS['price']*100)}% | Delivery {int(SUPPLIER_SCORE_WEIGHTS['delivery']*100)}% | "
        f"Reliability {int(SUPPLIER_SCORE_WEIGHTS['reliability']*100)}% | Availability {int(SUPPLIER_SCORE_WEIGHTS['availability']*100)}%."
    )

    tab_overview, tab_compare, tab_catalog, tab_add = st.tabs(
        ["Supplier Overview", "Compare & Rank by Product", "Full Supplier Catalog", "Add Supplier Offer"]
    )

    with tab_overview:
        ov_df = cached_supplier_overview(str(db_path), st.session_state["analysis_version"])
        if ov_df.empty:
            st.warning(
                "No supplier records exist in your active dataset. Map a Supplier / Vendor Name column "
                "when uploading your file (or add an offer manually in the Add Supplier Offer tab)."
            )
        else:
            render_paginated_dataframe(format_currency_columns(ov_df, ["avg_unit_price"]), key_prefix="sup_ov")

    with tab_compare:
        if all_sup.empty:
            st.info("Upload supplier columns or add a supplier offer to compare vendors per product.")
        else:
            supplied_pids = set(all_sup["product_id"].unique())
            supplied_map = {k: v for k, v in prod_map.items() if v in supplied_pids}
            c1, c2 = st.columns([2.2, 1])
            chosen = c1.selectbox("Choose Product to Compare Suppliers *", list(supplied_map.keys()))
            req_qty = c2.number_input("Required Quantity *", min_value=1, value=20, step=5)

            comp = supervisor.supplier_agent.compare_suppliers(
                product_id=supplied_map[chosen], required_qty=int(req_qty)
            )
            ranked = comp["ranked_table"]
            if ranked.empty:
                st.info("No suppliers found for this product.")
            else:
                top = comp["top_supplier"]
                st.caption(f"Scored using available uploaded criteria: {comp['scored_criteria']}")
                score_s = f"{top['total_score']}/100" if pd.notna(top["total_score"]) else "Unscored (names only)"
                price_s = format_inr(top["unit_price"]) if pd.notna(top["unit_price"]) else "N/A"
                st.success(f"Top Ranked Supplier: {top['supplier_name']} (Score: {score_s}, Price: {price_s}) — {top['recommendation_reason']}")

                view = ranked[
                    [
                        "rank", "supplier_id", "supplier_name", "unit_price", "delivery_days",
                        "available_quantity", "min_order_qty", "reliability_score",
                        "total_score", "recommendation_reason",
                    ]
                ]
                view_fmt = format_currency_columns(view, ["unit_price"])
                if ranked["total_score"].notna().any():
                    render_accessible_chart(
                        plot_supplier_scores(ranked, top["product_name"]),
                        summary_text=(
                            f"Compared {len(ranked)} supplier(s) for {top['product_name']} using criteria: {comp['scored_criteria']}. "
                            f"Rank #1 is {top['supplier_name']} with a score of {score_s}."
                        ),
                        table_df=view_fmt,
                        table_label="View Ranked Supplier Comparison table",
                    )
                else:
                    st.dataframe(view_fmt, use_container_width=True, hide_index=True)

    with tab_catalog:
        if all_sup.empty:
            st.info("No supplier offers in the active dataset.")
        else:
            names = ["All"] + sup_backend.get_unique_supplier_names(db_path=db_path)
            sel = st.selectbox("Filter Catalog by Supplier", names)
            cat_df = supervisor.supplier_agent.list_suppliers(supplier_name=sel)
            render_paginated_dataframe(format_currency_columns(cat_df.drop(columns=["id"]), ["unit_price"]), key_prefix="sup_cat")

    with tab_add:
        if not prod_map:
            st.info("Load products first.")
        else:
            st.caption("Fields marked with * are required.")
            with st.form("add_sup_form"):
                c1, c2, c3 = st.columns(3)
                sid = c1.text_input("Supplier ID *", value="SUP-NEW")
                sname = c2.text_input("Supplier Name *", placeholder="e.g., Bharat Wholesale Hub")
                pchoice = c3.selectbox("Product *", list(prod_map.keys()))
                c4, c5, c6, c7, c8 = st.columns(5)
                uprice = c4.number_input("Unit Price (₹) *", min_value=1.0, value=100.0, step=1.0)
                ddays = c5.number_input("Delivery Days *", min_value=1, value=2, step=1)
                aqty = c6.number_input("Available Qty *", min_value=0, value=100, step=5)
                rel = c7.number_input("Reliability (0-100) *", min_value=0.0, max_value=100.0, value=90.0, step=0.5)
                moq = c8.number_input("MOQ *", min_value=1, value=5, step=1)
                if st.form_submit_button("Save Supplier Offer", type="primary"):
                    try:
                        res = supervisor.supplier_agent.upsert_supplier(
                            supplier_id=sid, supplier_name=sname, product_id=prod_map[pchoice],
                            unit_price=float(uprice), delivery_days=int(ddays),
                            available_quantity=int(aqty), reliability_score=float(rel), min_order_qty=int(moq),
                        )
                        bump_analysis_version()
                        st.success(f"Supplier offer {res['action']} for {res['supplier_name']}.")
                        st.rerun()
                    except ValidationError as exc:
                        st.error(f"Validation Error: {exc}")


# ==============================================================================
# 9. REPORTS
# ==============================================================================
def render_reports(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "Reports",
        "Download formula-safe CSV datasets, agent analysis tables, and an executive Markdown summary from your active data.",
    )
    if supervisor is None:
        render_no_data_gate("Reports")
        return

    render_active_source_banner()
    dataset_label = st.session_state.get("dataset_label") or "Active Session Dataset"
    db_str = str(supervisor.db_path)
    ver = st.session_state["analysis_version"]

    health_cached = cached_store_health_check(db_str, ver)
    sales_cached = cached_sales_analytics(db_str, ver)
    exec_md = report_backend.build_executive_markdown_report(
        supervisor=supervisor,
        dataset_label=dataset_label,
        quality_report=st.session_state.get("quality_report"),
        capabilities=st.session_state.get("capabilities"),
        precomputed_health=health_cached,
        precomputed_sales=sales_cached,
    )

    st.subheader("Executive Intelligence Summary")
    st.download_button(
        label="Download Executive Summary (Markdown)",
        data=exec_md.encode("utf-8"),
        file_name="stock_sentry_executive_report.md",
        mime="text/markdown",
        type="primary",
    )
    with st.expander("Preview Executive Report", expanded=True):
        st.markdown(exec_md)

    st.subheader("Downloadable Agent Reports (CSV — Formula-Injection Sanitized)")
    inv_df = supervisor.inventory_agent.list_products()
    low_df = supervisor.inventory_agent.get_low_stock_alerts(include_out_of_stock=True)["products"]
    ov_df = supervisor.inventory_agent.get_overstock_alerts()["products"]
    anoms = cached_anomaly_scan(db_str, ver)["anomalies"]
    anom_df = pd.DataFrame(anoms) if anoms else pd.DataFrame()
    fc_df = cached_forecast_portfolio(db_str, ver, DEFAULT_FORECAST_HORIZON_DAYS, DEFAULT_FORECAST_WINDOW_DAYS, "ema")
    recs = cached_recommendations(db_str, ver, DEFAULT_BUDGET_LIMIT)["recommendations"]
    rec_df = pd.DataFrame(recs) if recs else pd.DataFrame()
    sup_df = cached_supplier_overview(db_str, ver)
    audit_df = supervisor.procurement_agent.list_audit_logs()

    r1, r2, r3, r4 = st.columns(4)
    r1.download_button("Download Inventory Catalog (CSV)", report_backend.dataframe_to_csv_bytes(inv_df), "inventory_catalog.csv", "text/csv", disabled=inv_df.empty, use_container_width=True)
    r2.download_button("Download Low/OOS Alerts (CSV)", report_backend.dataframe_to_csv_bytes(low_df), "low_stock_alerts.csv", "text/csv", disabled=low_df.empty, use_container_width=True)
    r3.download_button("Download Overstock Report (CSV)", report_backend.dataframe_to_csv_bytes(ov_df), "overstock_report.csv", "text/csv", disabled=ov_df.empty, use_container_width=True)
    r4.download_button("Download Anomaly Report (CSV)", report_backend.dataframe_to_csv_bytes(anom_df), "anomaly_report.csv", "text/csv", disabled=anom_df.empty, use_container_width=True)

    r5, r6, r7, r8 = st.columns(4)
    r5.download_button("Download Demand Forecasts (CSV)", report_backend.dataframe_to_csv_bytes(fc_df), "demand_forecasts.csv", "text/csv", disabled=fc_df.empty, use_container_width=True)
    r6.download_button("Download Procurement Recs (CSV)", report_backend.dataframe_to_csv_bytes(rec_df), "procurement_recommendations.csv", "text/csv", disabled=rec_df.empty, use_container_width=True)
    r7.download_button("Download Supplier Summary (CSV)", report_backend.dataframe_to_csv_bytes(sup_df), "supplier_analysis.csv", "text/csv", disabled=sup_df.empty, use_container_width=True)
    r8.download_button("Download PO Audit Trail (CSV)", report_backend.dataframe_to_csv_bytes(audit_df), "po_audit_trail.csv", "text/csv", disabled=audit_df.empty, use_container_width=True)


# ==============================================================================
# 10. AI ASSISTANT
# ==============================================================================
def render_ai_assistant(supervisor: Optional[SupervisorAgent]) -> None:
    render_page_header(
        "AI Inventory Assistant",
        "Ask questions in plain English — routed through the Supervisor Agent over your active session dataset.",
    )
    if supervisor is None:
        render_no_data_gate("AI Assistant")
        return

    render_active_source_banner()
    st.info(f"Assistant Mode: {supervisor.assistant_mode_label}")

    if not st.session_state["chat_history"]:
        st.session_state["chat_history"] = [
            {
                "role": "assistant",
                "content": (
                    f"Hello! I am analyzing **{st.session_state.get('dataset_label', 'your active dataset')}** "
                    "using Stock Sentry's local rule-based Supervisor Agent. Ask a question or select a suggested prompt below."
                ),
                "agents_used": [supervisor.name],
                "table": None,
            }
        ]

    example_questions = [
        "Which products are running low?",
        "Which products are overstocked?",
        "Which products may run out in the next five days?",
        "Which products should I reorder today?",
        "Why was this anomaly detected?",
        "How much inventory value do I currently have?",
        "Generate a purchase recommendation.",
    ]

    st.caption("Suggested Questions (Click any prompt to run):")
    cols = st.columns(4)
    triggered = None
    for idx, q in enumerate(example_questions):
        if cols[idx % 4].button(q, key=f"qbtn_{st.session_state['analysis_version']}_{idx}", use_container_width=True):
            triggered = q

    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("agents_used"):
                st.caption("Agents consulted: " + " → ".join(msg["agents_used"]))
            if msg.get("table") is not None and not msg["table"].empty:
                with st.expander("View Supporting Data Table"):
                    st.dataframe(msg["table"], use_container_width=True, hide_index=True)

    user_input = st.chat_input("Ask about low stock, overstock, forecasts, anomalies, suppliers, or purchase orders...")
    active_query = triggered or user_input

    if active_query:
        st.session_state["chat_history"].append({"role": "user", "content": active_query})
        response = supervisor.answer_query(active_query)
        st.session_state["chat_history"].append(
            {
                "role": "assistant",
                "content": response["answer"],
                "agents_used": response["agents_used"],
                "table": response["supporting_table"],
            }
        )
        st.rerun()


def main() -> None:
    inject_custom_css()
    init_session_state()
    supervisor = get_active_supervisor()

    with st.sidebar:
        st.markdown(f"## {APP_NAME}")
        st.caption(f"{APP_TAGLINE} (v{APP_VERSION})")
        st.divider()

        current_idx = NAV_PAGES.index(st.session_state["nav_page"]) if st.session_state["nav_page"] in NAV_PAGES else 0
        selected_page = st.radio(
            "Primary Navigation",
            NAV_PAGES,
            index=current_idx,
            help="Use arrow keys or click to switch between Stock Sentry workspaces.",
        )
        st.session_state["nav_page"] = selected_page

        st.divider()
        st.caption("Active Session Workspace")
        if st.session_state["active_mode"] == "uploaded":
            st.success(f"Active File: {st.session_state['dataset_label']}")
        elif st.session_state["active_mode"] == "demo":
            st.info("Synthetic Demo Mode Active")
        else:
            st.warning("No dataset loaded yet")

        st.markdown("- **Isolation:** Per-session temporary SQLite")
        st.markdown("- **Auto-Buy:** Disabled (Human Approval Mandatory)")
        st.markdown("- **Accessibility:** WCAG 2.2 AA Contrast & Keyboard Focus")

    if selected_page == "Overview":
        render_overview(supervisor)
    elif selected_page == "Upload Data":
        render_upload_data()
    elif selected_page == "Data Quality":
        render_data_quality(supervisor)
    elif selected_page == "Anomaly Detection":
        render_anomaly_detection(supervisor)
    elif selected_page == "Demand Forecasting":
        render_demand_forecasting(supervisor)
    elif selected_page == "Inventory Intelligence":
        render_inventory_intelligence(supervisor)
    elif selected_page == "Procurement":
        render_procurement(supervisor)
    elif selected_page == "Supplier Analysis":
        render_supplier_analysis(supervisor)
    elif selected_page == "Reports":
        render_reports(supervisor)
    elif selected_page == "AI Assistant":
        render_ai_assistant(supervisor)


if __name__ == "__main__":
    main()
