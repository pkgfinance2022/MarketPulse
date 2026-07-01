import streamlit as st


class StockDetails:

    @staticmethod
    def render(stock):

        if stock is None:
            st.info("Select a stock")
            return

        st.subheader(
            f"{stock['Ticker']} — {stock['Name']}"
        )

        c1, c2, c3 = st.columns(3)

        c1.metric("Price", stock["Price"])
        c2.metric("Signal", stock["Signal"])
        c3.metric("AI Score", stock["AI Score"])

        c1.metric("Trend", stock["Trend"])
        c2.metric("RSI", round(stock["RSI"], 1))
        c3.metric("ADX", round(stock["ADX"], 1))

        st.divider()

        st.write("### Support / Resistance")

        c1, c2, c3 = st.columns(3)

        c1.metric("Support", stock["Support"])
        c2.metric("Resistance", stock["Resistance"])
        c3.metric("Stop Loss", stock["Stop Loss"])

        st.divider()

        st.write("### Thesis")
        st.write(stock.get("Thesis", "") or "No thesis recorded yet.")

        if stock.get("Reasons"):
            st.write("### Reasons")
            for reason in stock["Reasons"]:
                st.write("-", reason)

        if stock.get("Risks"):
            st.write("### Risks")
            for risk in stock["Risks"]:
                st.write("-", risk)
