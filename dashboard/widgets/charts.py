import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots


class Charts:

    @staticmethod
    def _candles_and_overlays(fig, df):

        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name="Price",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

        for name, color in [("EMA20", "#1f77b4"), ("EMA50", "#ff7f0e"), ("EMA200", "#9467bd")]:

            if name in df:

                fig.add_trace(
                    go.Scatter(
                        x=df.index,
                        y=df[name],
                        name=name,
                        line=dict(width=1.3, color=color),
                    ),
                    row=1,
                    col=1,
                )

        if "Support" in df and df["Support"].notna().any():

            fig.add_hline(
                y=df["Support"].dropna().iloc[-1],
                line_dash="dot",
                line_color="#1a7f37",
                annotation_text="Support",
                annotation_position="bottom right",
                row=1,
                col=1,
            )

        if "Resistance" in df and df["Resistance"].notna().any():

            fig.add_hline(
                y=df["Resistance"].dropna().iloc[-1],
                line_dash="dot",
                line_color="#c00000",
                annotation_text="Resistance",
                annotation_position="top right",
                row=1,
                col=1,
            )

    @staticmethod
    def _signal_markers(fig, df, signal_markers):
        """
        Plots a marker at each historical point a pullback-style signal
        fired, so it's visually obvious where similar setups triggered
        before and what happened afterward - not just the current state.
        """

        if not signal_markers:
            return

        for direction, symbol, color in [
            ("LONG", "triangle-up", "#1a7f37"),
            ("SHORT", "triangle-down", "#c00000"),
        ]:

            points = [m for m in signal_markers if m["direction"] == direction]

            if not points:
                continue

            fig.add_trace(
                go.Scatter(
                    x=[p["date"] for p in points],
                    y=[p["price"] for p in points],
                    mode="markers",
                    marker=dict(symbol=symbol, size=12, color=color, line=dict(width=1, color="black")),
                    name=f"{direction} signal",
                ),
                row=1,
                col=1,
            )

    @staticmethod
    def _volume(fig, df):

        if "Volume" not in df:
            return

        colors = [
            "#1a7f37" if c >= o else "#c00000"
            for o, c in zip(df["Open"], df["Close"])
        ]

        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["Volume"],
                marker_color=colors,
                name="Volume",
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    @staticmethod
    def _macd(fig, df):

        if "MACD" not in df:
            return

        hist_colors = [
            "#1a7f37" if v >= 0 else "#c00000"
            for v in df["MACD_Hist"].fillna(0)
        ]

        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["MACD_Hist"],
                marker_color=hist_colors,
                name="MACD Hist",
                showlegend=False,
            ),
            row=3,
            col=1,
        )

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD"],
                name="MACD",
                line=dict(width=1.2, color="#1f77b4"),
            ),
            row=3,
            col=1,
        )

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD_Signal"],
                name="Signal",
                line=dict(width=1.2, color="#ff7f0e"),
            ),
            row=3,
            col=1,
        )

    @staticmethod
    def _rsi(fig, df, pullback_levels=False):

        if "RSI14" not in df:
            return

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["RSI14"],
                name="RSI14",
                line=dict(width=1.3, color="#9467bd"),
                showlegend=False,
            ),
            row=4,
            col=1,
        )

        fig.add_hline(y=70, line_dash="dash", line_color="#c00000", row=4, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#1a7f37", row=4, col=1)

        if pullback_levels:

            # Pullback-in-trend thresholds - distinct from the classic
            # 70/30 overbought/oversold lines above.
            fig.add_hline(
                y=65, line_dash="dot", line_color="#1a7f37",
                annotation_text="Recovery (65)", annotation_position="right",
                row=4, col=1,
            )
            fig.add_hline(
                y=25, line_dash="dot", line_color="#1a7f37",
                annotation_text="Oversold touch (25)", annotation_position="right",
                row=4, col=1,
            )
            fig.add_hline(
                y=75, line_dash="dot", line_color="#c00000",
                annotation_text="Overbought touch (75)", annotation_position="right",
                row=4, col=1,
            )
            fig.add_hline(
                y=35, line_dash="dot", line_color="#c00000",
                annotation_text="Recovery (35)", annotation_position="right",
                row=4, col=1,
            )

    @staticmethod
    def render(df, signal_markers=None, pullback_levels=False, show_rangeslider=False, default_zoom_bars=None):
        """
        signal_markers: optional list of {"date", "price", "direction"}
        dicts - plots triangle markers on the price chart where a
        pullback-style signal fired historically.

        pullback_levels: adds the 25/65/75/35 pullback-strategy
        threshold lines to the RSI subplot, in addition to the classic
        70/30 lines.

        default_zoom_bars: if set, the chart opens zoomed to the last N
        bars instead of the whole fetched history squeezed into one
        view (which turns into an unreadable smear for anything more
        than a few hundred candles) - the rangeslider still exposes the
        full history to scroll/zoom back into.
        """

        if df is None or df.empty:
            st.info("No chart data available for this ticker.")
            return

        fig = make_subplots(
            rows=4,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.5, 0.15, 0.18, 0.17],
            subplot_titles=("Price", "Volume", "MACD", "RSI"),
        )

        Charts._candles_and_overlays(fig, df)
        Charts._signal_markers(fig, df, signal_markers)
        Charts._volume(fig, df)
        Charts._macd(fig, df)
        Charts._rsi(fig, df, pullback_levels=pullback_levels)

        fig.update_layout(
            height=850,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_rangeslider_visible=show_rangeslider,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        if default_zoom_bars and len(df) > default_zoom_bars:

            fig.update_xaxes(
                range=[df.index[-default_zoom_bars], df.index[-1]],
                row=1,
                col=1,
            )

        st.plotly_chart(fig, use_container_width=True)