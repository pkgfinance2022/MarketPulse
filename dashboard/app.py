"""
Market Pulse Dashboard
"""

import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from core.loader import AssetLoader
from services.market_service import MarketService
from services.summary_service import SummaryService
from services.indicator_service import IndicatorService


# ----------------------------------------------------
# Page
# ----------------------------------------------------

st.set_page_config(
    page_title="Market Pulse",
    page_icon="📈",
    layout="wide",
)

# ----------------------------------------------------
# Load Data
# ----------------------------------------------------

@st.cache_data(ttl=300)
def load_market():

    loader = AssetLoader()
    assets = loader.all_assets()

    service = MarketService()
    repo = service.load_market(assets)

    rows = []

    for asset in repo.all():

        SummaryService.build(asset)
        IndicatorService.build(asset)

        # ---------- Trend Icons ----------

        def trend_icon(trend):

            if trend == "Bullish":
                return "🟢 Bullish"

            if trend == "Bearish":
                return "🔴 Bearish"

            return "🟡 Neutral"

        # ---------- RSI ----------

        def rsi_color(rsi):

            if rsi >= 70:
                return f"🔴 {rsi:.1f}"

            elif rsi >= 55:
                return f"🟢 {rsi:.1f}"

            elif rsi >= 45:
                return f"🟡 {rsi:.1f}"

            else:
                return f"🔵 {rsi:.1f}"

        rows.append(
            {
                "Category": asset.category.title(),
                "Asset": asset.name,
                "Price": round(asset.summary.price, 2),

                "15m RSI": rsi_color(asset.indicators.m15.rsi14),
                "1H RSI": rsi_color(asset.indicators.h1.rsi14),
                "1D RSI": rsi_color(asset.indicators.d1.rsi14),

                "15m Trend": trend_icon(asset.indicators.m15.trend),
                "1H Trend": trend_icon(asset.indicators.h1.trend),
                "1D Trend": trend_icon(asset.indicators.d1.trend),
            }
        )

    return pd.DataFrame(rows)


df = load_market()

# ----------------------------------------------------
# Header
# ----------------------------------------------------

left, right = st.columns([4, 1])

with left:
    st.title("📈 Market Pulse")

with right:
    st.metric(
        "Last Refresh",
        datetime.now().strftime("%H:%M:%S"),
    )

st.divider()

# ----------------------------------------------------
# Sidebar
# ----------------------------------------------------

st.sidebar.title("Market Pulse")

category = st.sidebar.selectbox(
    "Category",
    [
        "All",
        "Indices",
        "Commodities",
        "Crypto",
        "US",
    ],
)

search = st.sidebar.text_input(
    "Search Asset",
)

# ----------------------------------------------------
# Filtering
# ----------------------------------------------------

filtered = df.copy()

if category != "All":

    filtered = filtered[
        filtered["Category"].str.lower() == category.lower()
    ]

if search:

    filtered = filtered[
        filtered["Asset"].str.contains(
            search,
            case=False,
        )
    ]

# ----------------------------------------------------
# Cards
# ----------------------------------------------------

c1, c2, c3, c4 = st.columns(4)

with c1:
    indices = len(df[df["Category"] == "Indices"])
    us = len(df[df["Category"] == "US"])
    crypto = len(df[df["Category"] == "Crypto"])
    commodities = len(df[df["Category"] == "Commodities"])

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("🇮🇳 Indices", indices)
    c2.metric("🇺🇸 US", us)
    c3.metric("₿ Crypto", crypto)
    c4.metric("🥇 Commodities", commodities)

with c2:
    st.metric("🇺🇸 US", "4 Assets")

with c3:
    st.metric("₿ Crypto", "3 Assets")

with c4:
    st.metric("🥇 Commodities", "3 Assets")

st.divider()

# ----------------------------------------------------
# Table
# ----------------------------------------------------

st.dataframe(
    filtered,
    use_container_width=True,
    hide_index=True,
)

st.caption(f"{len(filtered)} Assets")