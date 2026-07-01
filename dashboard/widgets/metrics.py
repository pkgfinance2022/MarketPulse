import streamlit as st


class Metrics:

    @staticmethod
    def render(stats):

        st.subheader("📊 Market Summary")

        c1, c2, c3, c4, c5, c6 = st.columns(6)

        c1.metric(
            "Bullish",
            stats["bullish"],
        )

        c2.metric(
            "Neutral",
            stats["neutral"],
        )

        c3.metric(
            "Bearish",
            stats["bearish"],
        )

        c4.metric(
            "Avg RSI",
            round(stats["avg_rsi"], 1),
        )

        c5.metric(
            "Avg Score",
            round(stats["avg_score"], 1),
        )

        c6.metric(
            "Assets",
            stats["assets"],
        )

        st.divider()