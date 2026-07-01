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

from services.market_clock import MarketClock


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
# --------------------------------------------------
# Market Status
# --------------------------------------------------

india = MarketClock.status("India")
usa = MarketClock.status("USA")
europe = MarketClock.status("Europe")
forex = MarketClock.status("Forex")
crypto = MarketClock.status("Crypto")


def status_card(col, flag, title, data):

    if data["status"] == "OPEN":
        icon = "🟢"
    else:
        icon = "🔴"

    with col:

        st.markdown(
            f"""
### {flag} {title}

**{icon} {data['status']}**

{data['time']}
""",
        )


st.markdown("---")

st.subheader("🌍 Live Market Status")

c1, c2, c3, c4, c5 = st.columns(5)

status_card(c1, "🇮🇳", "India", india)
status_card(c2, "🇺🇸", "USA", usa)
status_card(c3, "🇪🇺", "Europe", europe)
status_card(c4, "💱", "Forex", forex)
status_card(c5, "₿", "Crypto", crypto)

st.markdown("---")
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

market = st.sidebar.selectbox(
    "Market",
    [
        "All",
        "India",
        "Indian Indices",
        "USA",
        "Crypto",
        "Global Macro",
    ],
)

# --------------------------------------------------
# Category
# --------------------------------------------------

if market == "India":

    sectors = (
        ["All"]
        + sorted(
            meta[
                meta["country"].str.lower() == "india"
            ]["sector"]
            .dropna()
            .unique()
            .tolist()
        )
    )

elif market == "USA":

    sectors = (
        ["All"]
        + sorted(
            meta[
                meta["country"].str.lower() == "usa"
            ]["sector"]
            .dropna()
            .unique()
            .tolist()
        )
    )

elif market == "Crypto":

    sectors = (
        ["All"]
        + sorted(
            meta[
                meta["country"].str.lower() == "crypto"
            ]["sector"]
            .dropna()
            .unique()
            .tolist()
        )
    )

elif market == "Indian Indices":

    sectors = ["Indian Indices"]

elif market == "Global Macro":

    sectors = (
        meta[
            (meta["country"].str.lower() == "global")
            & (meta["sector"] != "Indian Indices")
        ]["sector"]
        .dropna()
        .unique()
        .tolist()
    )

    sectors = ["All"] + sorted(sectors)

else:

    sectors = (
        ["All"]
        + sorted(meta["sector"].dropna().unique().tolist())
    )

sector = st.sidebar.selectbox(
    "Category",
    sectors,
)

search = st.sidebar.text_input(
    "Search",
)

portfolio_only = st.sidebar.checkbox(
    "💼 Portfolio Only",
    value=False,
)

watchlist_only = st.sidebar.checkbox(
    "👀 Watchlist Only",
    value=False,
)

priority = st.sidebar.slider(
    "⭐ Minimum Priority",
    min_value=1,
    max_value=5,
    value=1,
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

        country = market

        if market == "Indian Indices":
            country = "Global"
            sector = "Indian Indices"

        elif market == "Global Macro":
            country = "Global"

        df, success, failed = DashboardLoader.load(
            {
                "country": country,
                "sector": sector,
                "search": search,
                "portfolio_only": portfolio_only,
                "watchlist_only": watchlist_only,
                "priority": priority,
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

df = market["df"]

def color_change(val):

    if val is None:
        return ""

    if val > 0:
        return "color: green;"

    if val < 0:
        return "color: red;"

    return "color: gray;"


styled = (
    df.style
    .format(
        {
            "Price": "{:,.2f}",
            "15m %": "{:+.2f}%",
            "1H %": "{:+.2f}%",
        }
    )
    .map(color_change, subset=["15m %"])
    .map(color_change, subset=["1H %"])
)

st.dataframe(
    styled,
    width="stretch",
    hide_index=True,
)