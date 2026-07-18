import streamlit as st

from dashboard.services.time_utils import now_cet


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
                "Updated (CET)",
                now_cet().strftime("%I:%M:%S %p")
            )

        st.divider()