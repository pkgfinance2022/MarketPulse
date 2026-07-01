import streamlit as st
import plotly.graph_objects as go


class Charts:

    @staticmethod
    def price_chart(df):

        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Close"],
                name="Close",
            )
        )

        if "EMA20" in df:

            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["EMA20"],
                    name="EMA20",
                )
            )

        if "EMA50" in df:

            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["EMA50"],
                    name="EMA50",
                )
            )

        fig.update_layout(
            height=450,
            margin=dict(
                l=20,
                r=20,
                t=30,
                b=20,
            ),
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )

    @staticmethod
    def render(df):

        if df is None or df.empty:
            st.info("Load market data to view charts.")
            return

        Charts.price_chart(df)
