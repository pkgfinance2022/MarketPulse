import streamlit as st

from services.market_clock import MarketClock


class MarketStatus:

    @staticmethod
    def card(column, flag, name, data):

        icon = "🟢" if data["status"] == "OPEN" else "🔴"

        with column:

            st.markdown(
                f"""
### {flag} {name}

{icon} **{data["status"]}**

{data["time"]}
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