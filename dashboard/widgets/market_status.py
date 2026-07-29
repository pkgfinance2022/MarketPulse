import streamlit as st

from services.market_clock import MarketClock


class MarketStatus:

    @staticmethod
    def card(column, flag, name, data):

        icon = "🟢" if data["status"] == "OPEN" else "🔴"

        # Local clock time at that market - explicit request: a
        # scheduled event announced at a specific local time (e.g. an
        # FOMC decision at 2pm US Eastern) is easier to plan around
        # against this than mentally converting from wherever the app
        # happens to be running. None for Forex/Crypto - "24x7, no
        # single home timezone" doesn't have one local time to show.
        local_time_line = f"🕒 {data['local_time']} local\n\n" if data.get("local_time") else ""

        with column:

            st.markdown(
                f"""
### {flag} {name}

{icon} **{data["status"]}**

{local_time_line}{data["time"]}
"""
            )

    @staticmethod
    def render():

        st.subheader("🌍 Live Markets")

        c1, c2, c3, c4, c5 = st.columns(5)

        MarketStatus.card(
            c1,
            "🇮🇳",
            "India",
            MarketClock.status("India"),
        )

        MarketStatus.card(
            c2,
            "🇺🇸",
            "USA",
            MarketClock.status("USA"),
        )

        MarketStatus.card(
            c3,
            "🇪🇺",
            "Europe",
            MarketClock.status("Europe"),
        )

        MarketStatus.card(
            c4,
            "💱",
            "Forex",
            MarketClock.status("Forex"),
        )

        MarketStatus.card(
            c5,
            "₿",
            "Crypto",
            MarketClock.status("Crypto"),
        )

        st.divider()