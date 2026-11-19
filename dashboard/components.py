"""Reusable UI styling, badges, and Plotly charts for the Stock Sentry dashboard."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import SYNTHETIC_DATA_NOTICE
from utils.helpers import format_inr, status_color


def inject_custom_css() -> None:
    """Apply clean retail inventory control-room styling."""
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.35rem;
            padding-bottom: 2rem;
        }
        .sentry-header {
            background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
            color: #F8FAFC;
            padding: 1.05rem 1.4rem;
            border-radius: 12px;
            margin-bottom: 1rem;
            border-left: 5px solid #38BDF8;
        }
        .sentry-header h2 {
            margin: 0;
            font-size: 1.5rem;
            color: #F8FAFC;
        }
        .sentry-header p {
            margin: 0.3rem 0 0 0;
            color: #CBD5E1;
            font-size: 0.91rem;
        }
        .status-pill {
            display: inline-block;
            padding: 0.18rem 0.65rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
            color: #FFFFFF;
        }
        .anomaly-card {
            border: 1px solid #E2E8F0;
            border-radius: 10px;
            padding: 0.95rem 1.1rem;
            margin-bottom: 0.75rem;
            background-color: #FFFFFF;
        }
        .demo-banner {
            background-color: #EFF6FF;
            border: 1px solid #BFDBFE;
            color: #1E3A8A;
            padding: 0.65rem 1rem;
            border-radius: 8px;
            font-size: 0.86rem;
            margin-bottom: 1rem;
        }
        .uploaded-banner {
            background-color: #ECFDF5;
            border: 1px solid #A7F3D0;
            color: #065F46;
            padding: 0.65rem 1rem;
            border-radius: 8px;
            font-size: 0.86rem;
            margin-bottom: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, subtitle: str) -> None:
    """Render a consistent control-room header banner."""
    st.markdown(
        f"""
        <div class="sentry-header">
            <h2>{title}</h2>
            <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_demo_notice() -> None:
    """Display transparent disclosure about synthetic demo data."""
    st.markdown(
        f'<div class="demo-banner">ℹ️ <strong>Demo Mode Notice:</strong> {SYNTHETIC_DATA_NOTICE}</div>',
        unsafe_allow_html=True,
    )


def render_uploaded_data_banner(file_name: str, file_size: str, products_count: int, loaded_at: str) -> None:
    """Display active session banner confirming analytics are computed from user-uploaded data."""
    st.markdown(
        f'<div class="uploaded-banner">✅ <strong>Active User Dataset:</strong> <code>{file_name}</code> '
        f'({file_size}) — {products_count} unique product(s) isolated in this browser session '
        f'(loaded {loaded_at}). Original project files are untouched.</div>',
        unsafe_allow_html=True,
    )


def status_pill_html(status: str) -> str:
    """Return HTML pill badge colored by status."""
    color = status_color(status)
    return f'<span class="status-pill" style="background-color: {color};">{status}</span>'


def plot_inventory_health_donut(summary: dict) -> go.Figure:
    """Donut chart showing distribution of Healthy, Low Stock, Out of Stock, and No-ROP SKUs."""
    healthy = summary.get("healthy_count", 0)
    low = summary.get("low_stock_count", 0)
    oos = summary.get("out_of_stock_count", 0)
    total = summary.get("total_products", 0)
    no_rop = max(0, total - healthy - low - oos)

    labels = ["Healthy", "Low Stock", "Out of Stock"]
    values = [healthy, low, oos]
    colors = ["#16A34A", "#D97706", "#DC2626"]
    if no_rop > 0:
        labels.append("In Stock (No ROP)")
        values.append(no_rop)
        colors.append("#0284C7")

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors),
                textinfo="label+value",
            )
        ]
    )
    fig.update_layout(
        title="Inventory Health Status (SKU Count)",
        margin=dict(t=45, b=15, l=10, r=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
    )
    return fig


def plot_category_value_bar(category_df: pd.DataFrame, has_cost: bool = True, has_retail: bool = True) -> go.Figure:
    """Bar chart comparing inventory valuation (or units when pricing is absent) per category."""
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
                    marker_color="#0EA5E9",
                    hovertemplate="%{x}<br>Cost Value: ₹%{y:,.2f}<extra></extra>",
                )
            )
        if has_retail:
            fig.add_trace(
                go.Bar(
                    name="Retail Value (₹)",
                    x=category_df["category"],
                    y=category_df["retail_value"].fillna(0),
                    marker_color="#1E293B",
                    hovertemplate="%{x}<br>Retail Value: ₹%{y:,.2f}<extra></extra>",
                )
            )
        title = "Inventory Valuation by Category"
        yaxis = "Value (₹)"
    else:
        fig.add_trace(
            go.Bar(
                name="Available Units",
                x=category_df["category"],
                y=category_df["total_units"],
                marker_color="#0284C7",
            )
        )
        title = "Available Inventory Units by Category (Pricing Not Mapped)"
        yaxis = "Units"

    fig.update_layout(
        title=title,
        barmode="group",
        xaxis_title="Category",
        yaxis_title=yaxis,
        margin=dict(t=45, b=35, l=15, r=15),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_missing_values_bar(missing_df: pd.DataFrame) -> go.Figure:
    """Horizontal bar chart of missing values by column."""
    if missing_df is None or missing_df.empty:
        return go.Figure()
    fig = px.bar(
        missing_df.sort_values("Missing (%)", ascending=True),
        x="Missing (%)",
        y="Original Column",
        orientation="h",
        text="Missing",
        color="Missing (%)",
        color_continuous_scale="OrRd",
        title="Missing Values by Column (%)",
    )
    fig.update_traces(texttemplate="%{text} cells", textposition="outside")
    fig.update_layout(
        coloraxis_showscale=False,
        height=max(240, min(420, 45 * len(missing_df) + 90)),
        margin=dict(t=45, b=25, l=15, r=45),
    )
    return fig


def plot_daily_sales_trend(daily_df: pd.DataFrame) -> go.Figure:
    """Line chart of total daily units sold."""
    if daily_df is None or daily_df.empty:
        return go.Figure()
    fig = px.line(
        daily_df,
        x="date",
        y="units_sold",
        markers=True,
        title="Daily Units Sold Trend",
        labels={"date": "Date", "units_sold": "Units Sold"},
    )
    fig.update_traces(line=dict(color="#0284C7", width=2.5))
    fig.update_layout(height=300, margin=dict(t=45, b=30, l=15, r=15))
    return fig


def plot_sales_and_forecast(forecast_result: dict) -> go.Figure:
    """Plot historical daily sales alongside the baseline statistical forecast."""
    hist_df = forecast_result["history_df"]
    fc_df = forecast_result["forecast_df"]

    fig = go.Figure()
    if not hist_df.empty:
        fig.add_trace(
            go.Scatter(
                x=hist_df["date"],
                y=hist_df["quantity_sold"],
                mode="lines+markers",
                name="Historical Daily Sales",
                line=dict(color="#0284C7", width=2),
                marker=dict(size=5),
            )
        )
        rolling = hist_df["quantity_sold"].rolling(window=7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=hist_df["date"],
                y=rolling,
                mode="lines",
                name="7-Day Moving Trend",
                line=dict(color="#64748B", width=2, dash="dot"),
            )
        )

    if not fc_df.empty:
        fig.add_trace(
            go.Scatter(
                x=fc_df["date"],
                y=fc_df["predicted_daily_demand"],
                mode="lines+markers",
                name="Baseline Forecast (Daily)",
                line=dict(color="#EA580C", width=3, dash="dash"),
                marker=dict(size=7, symbol="diamond"),
            )
        )

    fig.update_layout(
        title=f"Daily Sales History & Demand Forecast — {forecast_result['product_name']}",
        xaxis_title="Date",
        yaxis_title=f"Units ({forecast_result['unit']})",
        height=360,
        margin=dict(t=50, b=35, l=15, r=15),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_projected_stock_burndown(forecast_result: dict) -> go.Figure:
    """Plot projected remaining stock over forecast horizon vs reorder point."""
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
            line=dict(color="#2563EB", width=2.5),
            fillcolor="rgba(37, 99, 235, 0.14)",
        )
    )
    stored_rop = forecast_result.get("stored_reorder_point") or 0
    calc_rop = forecast_result.get("calculated_reorder_point") or 0
    rop = max(stored_rop, calc_rop)
    if rop > 0:
        fig.add_hline(
            y=rop,
            line_dash="dash",
            line_color="#DC2626",
            annotation_text=f"Reorder Point ({rop} units)",
            annotation_position="top right",
        )
    fig.update_layout(
        title="Projected Stock Burn-Down (Assuming No New Deliveries)",
        xaxis_title="Date",
        yaxis_title="Projected Units on Hand",
        height=300,
        margin=dict(t=45, b=35, l=15, r=15),
    )
    return fig


def plot_supplier_scores(ranked_df: pd.DataFrame, product_name: str) -> go.Figure:
    """Horizontal bar chart of weighted supplier scores."""
    if ranked_df is None or ranked_df.empty or ranked_df["total_score"].isna().all():
        return go.Figure()
    plot_df = ranked_df.dropna(subset=["total_score"]).sort_values("total_score", ascending=True)
    fig = px.bar(
        plot_df,
        x="total_score",
        y="supplier_name",
        orientation="h",
        text="total_score",
        color="total_score",
        color_continuous_scale="Tealgrn",
        title=f"Supplier Score Comparison — {product_name}",
        labels={"total_score": "Weighted Score (0–100)", "supplier_name": "Supplier"},
    )
    fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
    fig.update_layout(
        xaxis=dict(range=[0, 112]),
        coloraxis_showscale=False,
        height=280,
        margin=dict(t=45, b=25, l=15, r=25),
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
