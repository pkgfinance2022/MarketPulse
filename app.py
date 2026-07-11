"""
MarketPulse v2 dashboard shell.

The app coordinates services and widgets. Analysis and business rules live in
the engines and services, while widgets only render already-prepared data.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.services.chart_service import ChartService
from dashboard.services.dashboard_loader import DashboardLoader
from dashboard.services.dashboard_stats import DashboardStats
from dashboard.widgets.charts import Charts
from dashboard.widgets.header import Header
from dashboard.widgets.market_status import MarketStatus
from dashboard.widgets.metrics import Metrics
from dashboard.widgets.scanner import Scanner
from dashboard.widgets.sidebar import Sidebar
from dashboard.widgets.stock_details import StockDetails
from dashboard.widgets.top_opportunities import TopOpportunities


st.set_page_config(
    page_title="MarketPulse",
    page_icon="MP",
    layout="wide",
)


def init_state():

    defaults = {
        "market": None,
        "selected_ticker": None,
        "chart": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def load_market(filters):

    with st.spinner("Loading market intelligence..."):
        df, success, failed = DashboardLoader.load(
            {
                "country": filters["country"],
                "sector": filters["sector"],
                "search": filters["search"],
                "portfolio_only": filters["portfolio_only"],
                "watchlist_only": filters["watchlist_only"],
                "priority": filters["priority"],
            }
        )

    st.session_state.market = {
        "df": df,
        "success": success,
        "failed": failed,
    }

    if not df.empty:
        st.session_state.selected_ticker = df.iloc[0]["Ticker"]
        st.session_state.chart = None


def selected_stock(df):

    if df.empty:
        return None

    tickers = df["Ticker"].tolist()
    selected = st.session_state.selected_ticker

    if selected not in tickers:
        selected = tickers[0]

    ticker = st.selectbox(
        "Selected asset",
        tickers,
        index=tickers.index(selected),
    )

    st.session_state.selected_ticker = ticker

    return df[df["Ticker"] == ticker].iloc[0].to_dict()


def load_chart(ticker):

    if not ticker:
        return None

    cached = st.session_state.chart

    if cached and cached["ticker"] == ticker:
        return cached["df"]

    with st.spinner("Loading chart..."):
        chart_df = ChartService.history(ticker)

    st.session_state.chart = {
        "ticker": ticker,
        "df": chart_df,
    }

    return chart_df


def render_opportunity_center(df):

    st.subheader("Best Opportunities")
    TopOpportunities.render(df)


def render_workbench(df):

    clicked_ticker = Scanner.render(df)

    if clicked_ticker:
        st.session_state.selected_ticker = clicked_ticker

    st.divider()

    stock = selected_stock(df)

    StockDetails.render(stock)

    st.divider()

    ticker = stock["Ticker"] if stock else None

    Charts.render(
        load_chart(ticker)
    )


def render_loaded_dashboard(market):

    df = market["df"]
    stats = DashboardStats.summary(df)

    loaded, failed, displayed = st.columns(3)
    loaded.metric("Loaded", market["success"])
    failed.metric("Failed", market["failed"])
    displayed.metric("Displayed", len(df))

    Metrics.render(stats)
    render_opportunity_center(df)
    render_workbench(df)


def main():

    init_state()

    meta = DashboardLoader.metadata()

    Header.render()
    MarketStatus.render()

    filters = Sidebar.render(meta)

    if filters["refresh"]:
        st.cache_data.clear()
        st.session_state.chart = None
        st.success("Cache cleared.")

    if filters["load"] or st.session_state.market is None:
        load_market(filters)

    market = st.session_state.market

    if market is None:

        load_market(filters)

        market = st.session_state.market

    render_loaded_dashboard(market)


if __name__ == "__main__":
    main()