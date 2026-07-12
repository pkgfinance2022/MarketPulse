import pandas as pd
import streamlit as st


class Scanner:

    SORTABLE_COLUMNS = [
        "AI Score",
        "Intraday %",
        "Momentum Score",
        "1D %",
        "1H %",
        "15m %",
        "1D RSI",
        "Price",
        "Ticker",
        "Name",
        "Setup",
        "Reversal",
        "Daily Reversal",
        "Weekly",
    ]

    @staticmethod
    def color_price(v):

        if pd.isna(v):
            return ""

        if v > 0:
            return "color:#1a7f37;font-weight:bold;"

        if v < 0:
            return "color:#c00000;font-weight:bold;"

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

        if "strong sell" in v:
            return "background-color:#c00000;color:white"

        if "sell" in v:
            return "background-color:#ff6666"

        return ""

    @staticmethod
    def color_rsi(v):

        if pd.isna(v):
            return ""

        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""

        if v >= 70:
            return "color:#c00000;font-weight:bold;"  # overbought

        if v <= 30:
            return "color:#0b6fb4;font-weight:bold;"  # oversold

        return ""

    @staticmethod
    def color_setup(v):

        if pd.isna(v):
            return ""

        v = str(v).lower()

        # Order matters: "alert (long)"/"alert (short)" and "too late"
        # must be caught before the plain long/short check below, since
        # they also contain the words "long"/"short". Alert (LONG) and
        # Alert (SHORT) previously both fell into the generic "alert"
        # branch and got the same blue color - split them so the two
        # are distinguishable without reading the text.
        if "too late" in v:
            return "color:#795548;font-weight:bold;"

        if "alert (long)" in v:
            return "color:#0b6fb4;font-weight:bold;"

        if "alert (short)" in v:
            return "color:#e65100;font-weight:bold;"

        if "bullish" in v or "long" in v:
            return "color:#1a7f37;font-weight:bold;"

        if "bearish" in v or "short" in v:
            return "color:#c00000;font-weight:bold;"

        return "color:#888888;"

    @staticmethod
    def color_reversal(v):
        """Colors for the dual-timeframe Reversal Playbook column - a
        different label vocabulary from Setup (Algo1/Algo2 phases)."""

        if pd.isna(v):
            return ""

        v = str(v).lower()

        if "buy signal" in v or "bullish signal" in v:
            return "color:#1a7f37;font-weight:bold;"

        if "sell signal" in v:
            return "color:#c00000;font-weight:bold;"

        if "decision" in v:
            return "color:#e65100;font-weight:bold;"

        if "algo2 alert" in v:
            return "color:#e65100;font-weight:bold;"

        if "algo1 alert" in v:
            return "color:#b8860b;font-weight:bold;"

        if "cancelled" in v:
            return "color:#0b6fb4;font-weight:bold;"

        return "color:#888888;"

    @staticmethod
    def color_trend(v):

        if pd.isna(v):
            return ""

        v = str(v).lower()

        if v == "bullish":
            return "color:#1a7f37;font-weight:bold;"

        if v == "bearish":
            return "color:#c00000;font-weight:bold;"

        return "color:#888888;"

    @staticmethod
    def color_ai_score(v):
        """
        Pastel red -> yellow -> green gradient, computed by hand so we
        don't need to add matplotlib as a dependency just for this.
        """

        if pd.isna(v):
            return ""

        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""

        v = max(0, min(100, v))

        low = (255, 205, 210)   # pastel red
        mid = (255, 249, 196)   # pastel yellow
        high = (200, 230, 201)  # pastel green

        if v <= 50:
            t = v / 50
            start, end = low, mid
        else:
            t = (v - 50) / 50
            start, end = mid, high

        r = round(start[0] + t * (end[0] - start[0]))
        g = round(start[1] + t * (end[1] - start[1]))
        b = round(start[2] + t * (end[2] - start[2]))

        return f"background-color: rgb({r},{g},{b}); font-weight:bold;"

    @staticmethod
    def _sort_controls(df, default_sort, key_prefix):

        available = [c for c in Scanner.SORTABLE_COLUMNS if c in df.columns]

        if not available:
            return df

        default_index = (
            available.index(default_sort)
            if default_sort in available
            else 0
        )

        left, right = st.columns([3, 1])

        with left:
            sort_col = st.selectbox(
                "Sort by",
                available,
                index=default_index,
                key=f"{key_prefix}_sort_col",
            )

        with right:
            direction = st.selectbox(
                "Order",
                ["Descending", "Ascending"],
                index=0,
                key=f"{key_prefix}_sort_dir",
            )

        # Row order is frozen across auto-refresh ticks unless the sort
        # settings change or the ticker set itself changes (region
        # switch/reload). Without this, sorting by a live-updating
        # column (e.g. "1H %") on every 45s refresh can reshuffle row
        # positions - and since st.dataframe's row selection is
        # POSITION-based, the very rerun triggered by the user clicking
        # a row can re-sort first and resolve their click against a
        # DIFFERENT ticker that ended up at that row index, silently
        # selecting the wrong instrument.
        order_key = f"{key_prefix}_row_order"
        settings_key = f"{key_prefix}_row_order_settings"

        ticker_set = (
            tuple(sorted(df["Ticker"].tolist()))
            if "Ticker" in df.columns
            else None
        )

        current_settings = (sort_col, direction, ticker_set)

        if st.session_state.get(settings_key) != current_settings:

            sorted_df = df.sort_values(
                sort_col,
                ascending=(direction == "Ascending"),
                kind="stable",
            )

            st.session_state[settings_key] = current_settings
            st.session_state[order_key] = (
                sorted_df["Ticker"].tolist() if "Ticker" in df.columns else None
            )

            return sorted_df

        order = st.session_state.get(order_key)

        if order and "Ticker" in df.columns:
            return df.set_index("Ticker").reindex(order).reset_index()

        return df

    FULL_COLUMNS = [

        "Signal",
        "AI Score",
        "Momentum Score",
        "Status",

        "Ticker",
        "Name",

        "15m %",
        "1H %",
        "Intraday %",
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

    # For traders who want "what's happening and is it worth looking
    # at" at a glance, not a full technical-analysis grid. The "why"
    # columns were tried here too, but st.dataframe cells don't wrap -
    # cramming a full sentence into a narrow grid cell made the whole
    # table unreadable. The full, non-truncated description already
    # shows in the per-ticker detail box below the table on row click -
    # that's a better place to read it than a grid cell.
    COMPACT_COLUMNS = [
        "Status",
        "Ticker",
        "Name",
        "Price",
        "15m %",
        "1H %",
        "Setup",
        "Reversal",
        "Daily Reversal",
    ]

    @staticmethod
    def render(df, default_sort=None, key_prefix="scanner", compact=False, columns=None, title="🔍 Market Scanner", height=700):
        """
        Renders the scanner table and returns the ticker of the
        currently selected row (or None if nothing is selected /
        the table is empty), so the caller can drive the rest of the
        page (details panel, chart) from a row click instead of a
        separate dropdown.

        `key_prefix` keeps widget keys unique when the Scanner is
        rendered more than once in the same script run (e.g. several
        timeframe-specific tables on the same tab).

        `compact` switches to a bare-minimum column set (Status,
        Ticker, Name, Price, 1H %, Setup) instead of the full
        multi-timeframe grid - ignored if `columns` is given.

        `columns` overrides both of the above with an exact column
        list, for callers that want a specific timeframe's table (e.g.
        just the Daily columns) rather than either preset.
        """

        if df.empty:
            st.warning("No assets found.")
            return None

        st.subheader(title)

        df = Scanner._sort_controls(df, default_sort, key_prefix)

        # ==========================
        # Columns to DISPLAY ONLY
        # ==========================

        display_columns = columns if columns is not None else (Scanner.COMPACT_COLUMNS if compact else Scanner.FULL_COLUMNS)

        display_columns = [
            c
            for c in display_columns
            if c in df.columns
        ]

        # Only display these columns. Reset the index so row position
        # (used for click-selection below) lines up cleanly with what's
        # on screen, regardless of how the data was sorted upstream.
        df = df[display_columns].reset_index(drop=True)
        df = df.loc[:, ~df.columns.duplicated()]

        rsi_cols = [c for c in ["15m RSI", "1H RSI", "1D RSI"] if c in df.columns]
        trend_cols = [c for c in ["15m Trend", "1H Trend", "1D Trend"] if c in df.columns]
        pct_cols = [c for c in ["15m %", "1H %", "1D %", "Intraday %"] if c in df.columns]

        styled = (
            df.style
            .map(Scanner.color_price, subset=pct_cols)
            .map(Scanner.color_rsi, subset=rsi_cols)
            .map(Scanner.color_trend, subset=trend_cols)
        )

        if "Signal" in df.columns:
            styled = styled.map(Scanner.color_signal, subset=["Signal"])

        if "AI Score" in df.columns:
            styled = styled.map(Scanner.color_ai_score, subset=["AI Score"])

        if "Momentum Score" in df.columns:
            styled = styled.map(Scanner.color_ai_score, subset=["Momentum Score"])

        if "Setup" in df.columns:
            styled = styled.map(Scanner.color_setup, subset=["Setup"])

        if "Reversal" in df.columns:
            styled = styled.map(Scanner.color_reversal, subset=["Reversal"])

        if "Daily Reversal" in df.columns:
            styled = styled.map(Scanner.color_reversal, subset=["Daily Reversal"])

        if "Weekly" in df.columns:
            styled = styled.map(Scanner.color_reversal, subset=["Weekly"])

        # .style.map() above only applies color - it doesn't touch number
        # formatting, so Streamlit falls back to full float precision
        # (values already rounded upstream can still render with 6+
        # decimals). Format explicitly so what's displayed matches what
        # was actually computed.
        decimal_cols = {c: "{:.2f}" for c in pct_cols + rsi_cols}

        if "Price" in df.columns:
            decimal_cols["Price"] = "{:.2f}"

        styled = styled.format(decimal_cols)

        event = st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=height,
            on_select="rerun",
            selection_mode="single-row",
            key=f"{key_prefix}_table",
        )

        selected_rows = event.selection.rows if event and event.selection else []

        if selected_rows:
            return df.iloc[selected_rows[0]]["Ticker"]

        return None