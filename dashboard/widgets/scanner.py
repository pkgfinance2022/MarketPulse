import streamlit as st
import pandas as pd


class Scanner:

    @staticmethod
    def color_price(v):
        if pd.isna(v):
            return ""

        if v > 0:
            return "color:green;font-weight:bold;"

        if v < 0:
            return "color:red;font-weight:bold;"

        return ""

    @staticmethod
    def color_signal(v):

        if pd.isna(v):
            return ""

        v = str(v).lower()

        if "strong buy" in v:
            return "background-color:#00b050;color:white"

        if "buy" in v:
            return "background-color:#92d050"

        if "sell" in v:
            return "background-color:#ff6666"

        if "strong sell" in v:
            return "background-color:#c00000;color:white"

        return ""

    @staticmethod
    def render(df):

        if df.empty:

            st.warning("No assets found.")
            return

        st.subheader("🔍 Market Scanner")

        # ==========================
        # Columns to DISPLAY ONLY
        # ==========================

        display_columns = [

            "Signal",
            "AI Score",

            "Ticker",
            "Name",

            "15m %",
            "1H %",
            "1D %",

            "15m RSI",
            "1H RSI",
            "1D RSI",

            "15m Trend",
            "1H Trend",
            "1D Trend",

            "Price",

            "Sector",
        ]

        display_columns = [
            c
            for c in display_columns
            if c in df.columns
        ]

        # Only display these columns
        df = df[display_columns]
        df = df.loc[:, ~df.columns.duplicated()]

        styled = (
            df.style
            .map(
                Scanner.color_price,
                subset=[
                    c for c in ["15m %", "1H %", "1D %"]
                    if c in df.columns
                ],
            )
            .map(
                Scanner.color_signal,
                subset=["Signal"],
            )
        )

        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=900,
        )