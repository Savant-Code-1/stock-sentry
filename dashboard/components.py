"""Accessible, XSS-safe UI components, colorblind-safe Plotly charts, and pagination for Stock Sentry."""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DEFAULT_TABLE_PAGE_SIZE, SYNTHETIC_DATA_NOTICE
from utils.helpers import escape_html, format_inr, status_color, status_prefix

# Okabe-Ito colorblind-safe high-contrast palette (distinguishable under protanopia/deuteranopia/tritanopia)
COLORBLIND_SAFE = {
    "blue": "#0072B2",
    "dark_navy": "#0F172A",
    "vermillion": "#C2410C",
    "green": "#166534",
    "amber": "#B45309",
    "purple": "#6D28D9",
    "red": "#B91C1C",
    "slate": "#475569",
}


def inject_custom_css() -> None:
    """Apply WCAG 2.2 AA accessible, responsive retail inventory control-room styling."""
    st.markdown(
        """
        <style>
        /* Skip-to-content link for keyboard & screen-reader users (WCAG 2.4.1) */
        .skip-link {
            position: absolute;
            left: -9999px;
            top: 0;
            background: #0F172A;
            color: #FFFFFF !important;
            padding: 0.65rem 1rem;
            border-radius: 6px;
            font-weight: 700;
            z-index: 99999;
            text-decoration: none;
        }
        .skip-link:focus {
            left: 1rem;
            top: 0.75rem;
            outline: 3px solid #38BDF8;
            outline-offset: 2px;
        }

        .block-container {
            padding-top: 1.35rem;
            padding-bottom: 2.25rem;
            max-width: 100%;
        }

        /* High-contrast visible keyboard focus indicators (WCAG 2.4.7 & 2.4.11) */
        button:focus-visible,
        a:focus-visible,
        input:focus-visible,
        select:focus-visible,
        textarea:focus-visible,
        [role="tab"]:focus-visible,
        [role="radio"]:focus-visible,
        [data-baseweb="select"] *:focus-visible {
            outline: 3px solid #0369A1 !important;
            outline-offset: 2px !important;
            box-shadow: 0 0 0 2px #FFFFFF !important;
        }

        /* Accessible touch target sizing (WCAG 2.5.8) */
        .stButton > button,
        .stDownloadButton > button {
            min-height: 44px;
            font-weight: 600;
        }

        /* Semantic header banner with AAA contrast (17.06:1 title, 9.85:1 subtitle) */
        .sentry-header {
            background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
            color: #F8FAFC;
            padding: 1.15rem 1.5rem;
            border-radius: 12px;
            margin-bottom: 1rem;
            border-left: 6px solid #38BDF8;
        }
        .sentry-header h1 {
            margin: 0;
            font-size: 1.65rem;
            line-height: 1.3;
            color: #F8FAFC;
            font-weight: 700;
        }
        .sentry-header p {
            margin: 0.35rem 0 0 0;
            color: #E2E8F0;
            font-size: 0.96rem;
            line-height: 1.45;
        }

        /* WCAG AA+ status pill (>= 5.9:1 contrast + symbolic text prefix) */
        .status-pill {
            display: inline-block;
            padding: 0.22rem 0.72rem;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.01em;
            color: #FFFFFF;
            white-space: nowrap;
        }

        .anomaly-card {
            border: 1px solid #CBD5E1;
            border-left: 5px solid #475569;
            border-radius: 10px;
            padding: 1rem 1.15rem;
            margin-bottom: 0.85rem;
            background-color: #FFFFFF;
            color: #0F172A;
            line-height: 1.5;
        }
        .anomaly-card-header {
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 0.5rem;
        }
        .anomaly-meta {
            margin-top: 0.5rem;
            font-size: 0.92rem;
            color: #1E293B;
        }
        .anomaly-action {
            margin-top: 0.45rem;
            font-size: 0.92rem;
            color: #0F5132;
            font-weight: 500;
        }

        .demo-banner {
            background-color: #EFF6FF;
            border: 1px solid #93C5FD;
            border-left: 5px solid #1D4ED8;
            color: #1E3A8A;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            font-size: 0.92rem;
            line-height: 1.45;
            margin-bottom: 1rem;
        }
        .uploaded-banner {
            background-color: #ECFDF5;
            border: 1px solid #6EE7B7;
            border-left: 5px solid #047857;
            color: #064E3B;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            font-size: 0.92rem;
            line-height: 1.45;
            margin-bottom: 1rem;
            word-break: break-word;
        }
        .chart-summary-box {
            background-color: #F8FAFC;
            border: 1px solid #CBD5E1;
            border-radius: 8px;
            padding: 0.6rem 0.9rem;
            font-size: 0.9rem;
            color: #1E293B;
            margin-top: -0.35rem;
            margin-bottom: 0.75rem;
        }

        /* Responsive adjustments for tablets & mobile screens (WCAG 1.4.10 Reflow) */
        @media (max-width: 768px) {
            .block-container {
                padding-left: 0.75rem;
                padding-right: 0.75rem;
            }
            .sentry-header {
                padding: 0.9rem 1rem;
            }
            .sentry-header h1 {
                font-size: 1.32rem;
            }
            .anomaly-card {
                padding: 0.85rem;
            }
        }
        /* Respect user OS accessibility preferences (WCAG 2.3.3 & High Contrast) */
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                animation-duration: 0.01ms !important;
                animation-iteration-count: 1 !important;
                transition-duration: 0.01ms !important;
                scroll-behavior: auto !important;
            }
        }
        @media (prefers-contrast: more) {
            .anomaly-card, .chart-summary-box, .demo-banner, .uploaded-banner {
                border-width: 2px !important;
                border-color: #0F172A !important;
            }
        }
        </style>
        <a href="#main-content" class="skip-link">Skip to main content</a>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, subtitle: str) -> None:
    """Render a semantic, XSS-safe H1 control-room header landmark."""
    safe_title = escape_html(title)
    safe_subtitle = escape_html(subtitle)
    st.markdown(
        f"""
        <header id="main-content" role="banner" class="sentry-header">
            <h1>{safe_title}</h1>
            <p>{safe_subtitle}</p>
        </header>
        """,
        unsafe_allow_html=True,
    )


def render_demo_notice() -> None:
    """Display transparent, accessible disclosure about synthetic demo data."""
    safe_notice = escape_html(SYNTHETIC_DATA_NOTICE)
    st.markdown(
        f'<div role="status" aria-live="polite" class="demo-banner">'
        f'<strong>[DEMO MODE] Transparency Notice:</strong> {safe_notice}</div>',
        unsafe_allow_html=True,
    )


def render_uploaded_data_banner(file_name: str, file_size: str, products_count: int, loaded_at: str) -> None:
    """Display XSS-safe active session banner confirming user-uploaded data is active."""
    st.markdown(
        f'<div role="status" aria-live="polite" class="uploaded-banner">'
        f'<strong>[ACTIVE USER DATASET]</strong> <code>{escape_html(file_name)}</code> '
        f'({escape_html(file_size)}) — {int(products_count)} unique product(s) isolated in this browser session '
        f'(loaded {escape_html(loaded_at)}). Original project files are untouched.</div>',
        unsafe_allow_html=True,
    )


def status_pill_html(status: str) -> str:
    """Return WCAG 2.2 AA+ high-contrast HTML badge with non-color symbol & ARIA label."""
    color = status_color(status)
    prefix = status_prefix(status)
    safe_status = escape_html(status)
    return (
        f'<span role="status" aria-label="Status: {safe_status}" class="status-pill" '
        f'style="background-color: {color};">{prefix} {safe_status}</span>'
    )


def render_anomaly_card(anomaly: dict) -> None:
    """Render an accessible, XSS-escaped semantic anomaly article card."""
    sev = str(anomaly.get("severity", "Low"))
    border_color = status_color(sev)
    product_name = escape_html(anomaly.get("product_name", ""))
    product_id = escape_html(anomaly.get("product_id", ""))
    event_name = escape_html(anomaly.get("detected_event", ""))
    expected = escape_html(anomaly.get("expected_value", ""))
    actual = escape_html(anomaly.get("actual_value", ""))
    det_time = escape_html(anomaly.get("detection_time", ""))
    explanation = escape_html(anomaly.get("explanation", ""))
    action = escape_html(anomaly.get("suggested_action", ""))

    st.markdown(
        f"""
        <article role="region" aria-label="Anomaly alert for {product_name}: {event_name}"
                 class="anomaly-card" style="border-left-color: {border_color};">
            <div class="anomaly-card-header">
                <div><strong>{product_name}</strong> (<code>{product_id}</code>) — <em>{event_name}</em></div>
                <div>{status_pill_html(sev)}</div>
            </div>
            <div class="anomaly-meta">
                <strong>Expected:</strong> {expected} &nbsp;|&nbsp;
                <strong>Actual:</strong> {actual} &nbsp;|&nbsp;
                <strong>Detected:</strong> {det_time}
            </div>
            <div style="margin-top:0.45rem; font-size:0.93rem;"><strong>Explanation:</strong> {explanation}</div>
            <div class="anomaly-action"><strong>Suggested Action:</strong> {action}</div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_accessible_chart(
    fig: go.Figure,
    summary_text: str,
    table_df: Optional[pd.DataFrame] = None,
    table_label: str = "View accessible data table for this chart",
) -> None:
    """Render a Plotly chart paired with a plain-text summary and tabular alternative (WCAG 1.1.1)."""
    st.plotly_chart(fig, use_container_width=True)
    st.markdown(
        f'<div role="note" class="chart-summary-box"><strong>Chart Summary:</strong> {escape_html(summary_text)}</div>',
        unsafe_allow_html=True,
    )
    if table_df is not None and not table_df.empty:
        with st.expander(table_label, expanded=False):
            st.dataframe(table_df, use_container_width=True, hide_index=True)


def render_paginated_dataframe(
    df: pd.DataFrame,
    key_prefix: str,
    page_size: int = DEFAULT_TABLE_PAGE_SIZE,
    caption_prefix: str = "",
) -> None:
    """Render large tables with lightweight pagination so browsers stay responsive."""
    if df is None or df.empty:
        st.info("No rows to display.")
        return

    total_rows = len(df)
    if total_rows <= page_size:
        if caption_prefix:
            st.caption(f"{caption_prefix} ({total_rows} row(s))")
        st.dataframe(df, use_container_width=True, hide_index=True)
        return

    total_pages = max(1, math.ceil(total_rows / page_size))
    c1, c2, c3 = st.columns([2.2, 1, 1])
    page_num = c2.number_input(
        f"Page (1–{total_pages})",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
        key=f"page_{key_prefix}",
        help="Navigate table pages to keep rendering fast on large datasets.",
    )
    chosen_size = c3.selectbox(
        "Rows per page",
        options=[25, 50, 100, 250],
        index=0 if page_size <= 25 else 1,
        key=f"psize_{key_prefix}",
    )
    total_pages = max(1, math.ceil(total_rows / chosen_size))
    safe_page = min(int(page_num), total_pages)
    start_idx = (safe_page - 1) * chosen_size
    end_idx = min(total_rows, start_idx + chosen_size)

    prefix_txt = f"{caption_prefix} | " if caption_prefix else ""
    c1.caption(f"{prefix_txt}Showing rows {start_idx + 1}–{end_idx} of {total_rows:,} (Page {safe_page} of {total_pages})")
    st.dataframe(df.iloc[start_idx:end_idx], use_container_width=True, hide_index=True)


def summarize_inventory_health(summary: dict) -> str:
    """Generate a concise plain-text description of the inventory health chart."""
    total = summary.get("total_products", 0)
    healthy = summary.get("healthy_count", 0)
    low = summary.get("low_stock_count", 0)
    oos = summary.get("out_of_stock_count", 0)
    no_rop = max(0, total - healthy - low - oos)
    parts = [
        f"Out of {total} tracked products: {healthy} Healthy",
        f"{low} Low Stock",
        f"{oos} Out of Stock",
    ]
    if no_rop > 0:
        parts.append(f"{no_rop} In Stock without Reorder Point")
    return ", ".join(parts) + "."


def plot_inventory_health_donut(summary: dict) -> go.Figure:
    """Colorblind-safe donut chart showing distribution of Healthy, Low Stock, and Out of Stock SKUs."""
    healthy = summary.get("healthy_count", 0)
    low = summary.get("low_stock_count", 0)
    oos = summary.get("out_of_stock_count", 0)
    total = summary.get("total_products", 0)
    no_rop = max(0, total - healthy - low - oos)

    labels = ["✓ Healthy", "▲ Low Stock", "✕ Out of Stock"]
    values = [healthy, low, oos]
    colors = [COLORBLIND_SAFE["green"], COLORBLIND_SAFE["amber"], COLORBLIND_SAFE["red"]]
    if no_rop > 0:
        labels.append("● In Stock (No ROP)")
        values.append(no_rop)
        colors.append(COLORBLIND_SAFE["blue"])

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.52,
                marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2)),
                textinfo="label+value+percent",
                textfont=dict(size=13),
            )
        ]
    )
    fig.update_layout(
        title=dict(text="Inventory Health Status (SKU Count)", font=dict(size=16)),
        font=dict(size=13, color="#0F172A"),
        margin=dict(t=50, b=20, l=10, r=10),
        height=330,
        legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5, font=dict(size=13)),
    )
    return fig


def summarize_category_chart(category_df: pd.DataFrame, has_cost: bool, has_retail: bool) -> str:
    """Generate a plain-text description of the category valuation/units bar chart."""
    if category_df is None or category_df.empty:
        return "No category data available."
    top = category_df.iloc[0]
    if has_cost and pd.notna(top.get("cost_value")):
        top_by_cost = category_df.sort_values("cost_value", ascending=False).iloc[0]
        return (
            f"Across {len(category_df)} categories, '{top_by_cost['category']}' holds the highest cost valuation "
            f"at {format_inr(top_by_cost['cost_value'])} ({int(top_by_cost['products'])} SKUs, {int(top_by_cost['total_units'])} units)."
        )
    if has_retail and pd.notna(top.get("retail_value")):
        top_by_ret = category_df.sort_values("retail_value", ascending=False).iloc[0]
        return (
            f"Across {len(category_df)} categories, '{top_by_ret['category']}' holds the highest retail valuation "
            f"at {format_inr(top_by_ret['retail_value'])}."
        )
    return (
        f"Pricing is not mapped. Across {len(category_df)} categories, '{top['category']}' has the most units on hand "
        f"({int(top['total_units'])} units across {int(top['products'])} SKUs)."
    )


def plot_category_value_bar(category_df: pd.DataFrame, has_cost: bool = True, has_retail: bool = True) -> go.Figure:
    """Colorblind-safe bar chart comparing inventory valuation (or units) per category."""
    if category_df is None or category_df.empty:
        return go.Figure()

    fig = go.Figure()
    if has_cost or has_retail:
        if has_cost:
            fig.add_trace(
                go.Bar(
                    name="Cost Value (₹)",
                    x=category_df["category"],
                    y=category_df["cost_value"].fillna(0),
                    marker_color=COLORBLIND_SAFE["blue"],
                    hovertemplate="%{x}<br>Cost Value: ₹%{y:,.2f}<extra></extra>",
                )
            )
        if has_retail:
            fig.add_trace(
                go.Bar(
                    name="Retail Value (₹)",
                    x=category_df["category"],
                    y=category_df["retail_value"].fillna(0),
                    marker_color=COLORBLIND_SAFE["dark_navy"],
                    hovertemplate="%{x}<br>Retail Value: ₹%{y:,.2f}<extra></extra>",
                )
            )
        title = "Inventory Valuation by Category (INR)"
        yaxis = "Value (₹)"
    else:
        fig.add_trace(
            go.Bar(
                name="Available Units",
                x=category_df["category"],
                y=category_df["total_units"],
                marker_color=COLORBLIND_SAFE["blue"],
            )
        )
        title = "Available Inventory Units by Category (Pricing Not Mapped)"
        yaxis = "Units"

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        font=dict(size=13, color="#0F172A"),
        barmode="group",
        xaxis_title="Product Category",
        yaxis_title=yaxis,
        margin=dict(t=50, b=40, l=15, r=15),
        height=330,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=13)),
    )
    return fig


def plot_missing_values_bar(missing_df: pd.DataFrame) -> go.Figure:
    """Accessible horizontal bar chart of missing values by column."""
    if missing_df is None or missing_df.empty:
        return go.Figure()
    fig = px.bar(
        missing_df.sort_values("Missing (%)", ascending=True),
        x="Missing (%)",
        y="Original Column",
        orientation="h",
        text="Missing",
        color_discrete_sequence=[COLORBLIND_SAFE["vermillion"]],
        title="Missing Values by Column (%)",
        labels={"Missing (%)": "Missing Percentage (%)", "Original Column": "Column Name"},
    )
    fig.update_traces(texttemplate="%{text} cells", textposition="outside")
    fig.update_layout(
        font=dict(size=13, color="#0F172A"),
        height=max(250, min(420, 46 * len(missing_df) + 95)),
        margin=dict(t=50, b=30, l=15, r=55),
    )
    return fig


def plot_daily_sales_trend(daily_df: pd.DataFrame) -> go.Figure:
    """Accessible line chart of total daily units sold."""
    if daily_df is None or daily_df.empty:
        return go.Figure()
    fig = px.line(
        daily_df,
        x="date",
        y="units_sold",
        markers=True,
        title="Daily Units Sold Trend",
        labels={"date": "Transaction Date", "units_sold": "Total Units Sold"},
    )
    fig.update_traces(line=dict(color=COLORBLIND_SAFE["blue"], width=2.8), marker=dict(size=7))
    fig.update_layout(
        font=dict(size=13, color="#0F172A"),
        height=310,
        margin=dict(t=50, b=35, l=15, r=15),
    )
    return fig


def plot_sales_and_forecast(forecast_result: dict) -> go.Figure:
    """Colorblind-safe chart with distinct line styles and markers for historical sales vs forecast."""
    hist_df = forecast_result["history_df"]
    fc_df = forecast_result["forecast_df"]

    fig = go.Figure()
    if not hist_df.empty:
        fig.add_trace(
            go.Scatter(
                x=hist_df["date"],
                y=hist_df["quantity_sold"],
                mode="lines+markers",
                name="Historical Daily Sales (Solid)",
                line=dict(color=COLORBLIND_SAFE["blue"], width=2.5),
                marker=dict(size=6, symbol="circle"),
            )
        )
        rolling = hist_df["quantity_sold"].rolling(window=7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=hist_df["date"],
                y=rolling,
                mode="lines",
                name="7-Day Moving Trend (Dotted)",
                line=dict(color=COLORBLIND_SAFE["slate"], width=2.2, dash="dot"),
            )
        )

    if not fc_df.empty:
        fig.add_trace(
            go.Scatter(
                x=fc_df["date"],
                y=fc_df["predicted_daily_demand"],
                mode="lines+markers",
                name="Baseline Forecast (Dashed)",
                line=dict(color=COLORBLIND_SAFE["vermillion"], width=3, dash="dash"),
                marker=dict(size=8, symbol="diamond"),
            )
        )

    fig.update_layout(
        title=dict(text=f"Daily Sales History & Demand Forecast — {forecast_result['product_name']}", font=dict(size=16)),
        font=dict(size=13, color="#0F172A"),
        xaxis_title="Date",
        yaxis_title=f"Units ({forecast_result['unit']})",
        height=370,
        margin=dict(t=55, b=40, l=15, r=15),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=12)),
    )
    return fig


def plot_projected_stock_burndown(forecast_result: dict) -> go.Figure:
    """Accessible burn-down chart comparing projected stock against reorder threshold."""
    fc_df = forecast_result["forecast_df"]
    if fc_df.empty:
        return go.Figure()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=fc_df["date"],
            y=fc_df["projected_stock"],
            mode="lines+markers",
            fill="tozeroy",
            name="Projected Remaining Stock",
            line=dict(color=COLORBLIND_SAFE["blue"], width=2.8),
            marker=dict(size=7, symbol="square"),
            fillcolor="rgba(0, 114, 178, 0.14)",
        )
    )
    stored_rop = forecast_result.get("stored_reorder_point") or 0
    calc_rop = forecast_result.get("calculated_reorder_point") or 0
    rop = max(stored_rop, calc_rop)
    if rop > 0:
        fig.add_hline(
            y=rop,
            line_dash="dash",
            line_color=COLORBLIND_SAFE["red"],
            line_width=2.2,
            annotation_text=f"Reorder Threshold ({rop} units)",
            annotation_position="top right",
            annotation_font=dict(size=12, color=COLORBLIND_SAFE["red"]),
        )
    fig.update_layout(
        title=dict(text="Projected Stock Burn-Down (No New Deliveries)", font=dict(size=16)),
        font=dict(size=13, color="#0F172A"),
        xaxis_title="Projected Date",
        yaxis_title="Projected Units on Hand",
        height=320,
        margin=dict(t=50, b=40, l=15, r=15),
    )
    return fig


def plot_supplier_scores(ranked_df: pd.DataFrame, product_name: str) -> go.Figure:
    """Accessible horizontal bar chart of weighted supplier scores."""
    if ranked_df is None or ranked_df.empty or ranked_df["total_score"].isna().all():
        return go.Figure()
    plot_df = ranked_df.dropna(subset=["total_score"]).sort_values("total_score", ascending=True)
    fig = px.bar(
        plot_df,
        x="total_score",
        y="supplier_name",
        orientation="h",
        text="total_score",
        color_discrete_sequence=[COLORBLIND_SAFE["blue"]],
        title=f"Supplier Score Comparison — {product_name}",
        labels={"total_score": "Weighted Score (0–100)", "supplier_name": "Supplier Name"},
    )
    fig.update_traces(texttemplate="%{text:.1f} / 100", textposition="outside")
    fig.update_layout(
        font=dict(size=13, color="#0F172A"),
        xaxis=dict(range=[0, 118]),
        height=290,
        margin=dict(t=50, b=30, l=15, r=35),
    )
    return fig


def format_currency_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Return a copy of df with specified numeric columns formatted in INR (NaN -> 'N/A')."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = out[col].apply(
                lambda v: "N/A" if pd.isna(v) else format_inr(v)
            )
    return out
