import streamlit as st


class AISummary:

    @staticmethod
    def render(summary):

        st.subheader("🤖 AI Summary")

        st.success(summary["signal"])

        st.progress(
            summary["confidence"] / 100
        )

        for item in summary["reasons"]:

            st.write("✅", item)

        if summary["risks"]:

            st.warning("Risks")

            for risk in summary["risks"]:

                st.write("⚠", risk)