"""
Market Pulse Dashboard
"""

import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.services.dashboard_loader import DashboardLoader

# --------------------------------------------------
# Page
# --------------------------------------------------

st.set_page_config(
    page_title="Market Pulse",
    page_icon="📈",
    layout="wide",
)

# --------------------------------------------------
# Metadata
# --------------------------------------------------

meta = DashboardLoader.metadata()


countries = ["All"] + sorted(meta["country"].dropna().unique().tolist())

sectors = ["All"] + sorted(meta["sector"].dropna().unique().tolist())

# --------------------------------------------------
# Header
# --------------------------------------------------

left, right = st.columns([4, 1])

with left:
    st.title("📈 Market Pulse")

with right:
    st.metric(
        "Time",
        datetime.now().strftime("%H:%M:%S"),
    )

st.divider()

# --------------------------------------------------
# Sidebar
# --------------------------------------------------

st.sidebar.header("Filters")

country = st.sidebar.selectbox(
    "Market",
    [
        "All",
        "India",
        "USA",
        "Crypto",
        "Global",
    ],
)

if country == "Global":

    sectors = [
        "All",
        "Indian Indices",
        "US Indices",
        "European Indices",
        "Asian Indices",
        "Currencies",
        "Commodities",
        "Bonds",
    ]

elif country == "India":

    sectors = ["All"] + sorted(
        meta[meta["country"] == "India"]["sector"].unique().tolist()
    )

elif country == "USA":

    sectors = ["All"] + sorted(
        meta[meta["country"] == "USA"]["sector"].unique().tolist()
    )

elif country == "Crypto":

    sectors = ["All"] + sorted(
        meta[meta["country"] == "Crypto"]["sector"].unique().tolist()
    )

else:

    sectors = ["All"] + sorted(meta["sector"].unique().tolist())

sector = st.sidebar.selectbox(
    "Category",
    sectors,
)

search = st.sidebar.text_input(
    "Search",
)

st.sidebar.divider()

assets_found = len(meta)

st.sidebar.metric(
    "Database Assets",
    assets_found,
)

load = st.sidebar.button(
    "🚀 Load Selected",
    width="stretch",
)

refresh = st.sidebar.button(
    "🔄 Clear Cache",
    width="stretch",
)

if refresh:

    st.cache_data.clear()

    st.success("Cache Cleared")

# --------------------------------------------------
# Session
# --------------------------------------------------

if "market" not in st.session_state:
    st.session_state.market = None

# --------------------------------------------------
# Load
# --------------------------------------------------

if load:

    with st.spinner("Loading selected assets..."):

        df, success, failed = DashboardLoader.load(
            {
                "country": country,
                "sector": sector,
                "search": search,
            }
        )

    st.session_state.market = {
        "df": df,
        "success": success,
        "failed": failed,
    }

# --------------------------------------------------
# Show
# --------------------------------------------------

market = st.session_state.market

if market is None:

    st.info(
        "Choose filters and click **Load Selected**."
    )

    st.stop()

# --------------------------------------------------
# Metrics
# --------------------------------------------------

m1, m2, m3 = st.columns(3)

m1.metric(
    "Loaded",
    market["success"],
)

m2.metric(
    "Failed",
    market["failed"],
)

m3.metric(
    "Displayed",
    len(market["df"]),
)

st.divider()

# --------------------------------------------------
# Table
# --------------------------------------------------

st.dataframe(
    market["df"],
    width="stretch",
    hide_index=True,
)