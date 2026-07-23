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

            st.markdown(
                "<span style='font-size:0.85rem; color:gray;'>Prem Kumar Gupta&trade;</span>",
                unsafe_allow_html=True,
            )

        with right:

            st.metric(
                "Updated (CET)",
                now_cet().strftime("%I:%M:%S %p")
            )

        # Streamlit's own data-grid table component doesn't reflow
        # columns for a narrow screen the way plain text does - the
        # full Scanner column set (18 columns) becomes an unusably
        # cramped horizontal-scroll mess on a phone. There's no
        # reliable server-side "is this a mobile browser" signal in
        # Streamlit without injecting custom JS, so this is a manual
        # toggle rather than automatic detection - global and
        # top-of-page since it affects every Scanner table on every
        # tab (see Scanner.render's own use of it).
        st.checkbox(
            "📱 Compact tables (fewer columns - better for mobile)",
            key="compact_tables",
            help="Shows only Status/Ticker/Name/Price/Setup/Reversal on every scanner table instead of the full column set.",
        )

        st.divider()