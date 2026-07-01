import streamlit as st
from datetime import datetime


class Header:

    @staticmethod
    def render():

        left, right = st.columns([5, 1])

        with left:

            st.title("📈 MarketPulse")

            st.caption(
                "Professional Market Intelligence Dashboard"
            )

        with right:

            st.metric(
                "Updated",
                datetime.now().strftime("%H:%M:%S")
            )

        st.divider()