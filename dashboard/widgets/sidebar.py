import streamlit as st


class Sidebar:

    @staticmethod
    def _sectors(meta, market):

        if market == "India":
            data = meta[meta["country"].str.lower() == "india"]
        elif market == "USA":
            data = meta[meta["country"].str.lower() == "usa"]
        elif market == "Crypto":
            data = meta[meta["country"].str.lower() == "crypto"]
        elif market == "Indian Indices":
            return ["Indian Indices"]
        elif market == "Global Macro":
            data = meta[
                (meta["country"].str.lower() == "global")
                & (meta["sector"] != "Indian Indices")
            ]
        else:
            data = meta

        return ["All"] + sorted(data["sector"].dropna().unique().tolist())

    @staticmethod
    def _country(market):

        if market in ("Indian Indices", "Global Macro"):
            return "Global"

        return market

    @staticmethod
    def render(meta):

        st.sidebar.header("Filters")

        market = st.sidebar.selectbox(
            "Market",
            [
                "All",
                "India",
                "USA",
                "Crypto",
                "Indian Indices",
                "Global Macro",
            ],
        )

        sector = st.sidebar.selectbox(
            "Sector",
            Sidebar._sectors(meta, market),
        )

        search = st.sidebar.text_input("Search")

        portfolio = st.sidebar.checkbox(
            "Portfolio Only"
        )

        watchlist = st.sidebar.checkbox(
            "Watchlist Only"
        )

        priority = st.sidebar.slider(
            "Minimum Priority",
            1,
            5,
            1,
        )

        load = st.sidebar.button(
            "🚀 Load",
            use_container_width=True,
        )

        refresh = st.sidebar.button(
            "Refresh Cache",
            use_container_width=True,
        )

        if market == "Indian Indices":
            sector = "Indian Indices"

        return {
            "market": market,
            "country": Sidebar._country(market),
            "sector": sector,
            "search": search,
            "portfolio_only": portfolio,
            "watchlist_only": watchlist,
            "priority": priority,
            "load": load,
            "refresh": refresh,
        }
