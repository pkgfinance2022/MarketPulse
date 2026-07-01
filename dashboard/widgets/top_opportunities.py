import streamlit as st


class TopOpportunities:

    @staticmethod
    def render(df):

        if df.empty:
            return

        st.subheader("⭐ Top Opportunities")

        cols = [
            "Name",
            "Signal",
            "AI Score",
            "Trend",
            "RSI",
            "Price",
        ]

        show = (
            df.sort_values(
                "AI Score",
                ascending=False,
            )[cols]
            .head(10)
        )

        st.dataframe(
            show,
            use_container_width=True,
            hide_index=True,
            height=370,
        )