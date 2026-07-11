import ta

from providers.yahoo import YahooProvider


class ChartService:

    SUPPORT_RESISTANCE_WINDOW = 20  # matches analysis/technical_engine.py

    @staticmethod
    def history(symbol, interval="1d", period="1y"):
        """
        Reuses YahooProvider (same one the main scan uses) instead of
        calling yf.download() directly. YahooProvider already flattens
        yfinance's MultiIndex columns - calling yf.download() raw here
        skipped that step, so df["Close"] could come back as a
        1-column DataFrame instead of a Series, which crashed
        indicators with "Data must be 1-dimensional".

        Adds everything the chart widget needs: EMA20/50/200, RSI14,
        MACD (line/signal/histogram), and rolling support/resistance,
        using the same formulas as the rest of the app (technical_engine.py
        / indicator_service.py) so the chart agrees with the Scanner and
        Stock Details numbers instead of silently using different math.
        """

        # yfinance only keeps ~5 days of 15m bars - a longer period
        # silently gets truncated, so cap it here instead of letting
        # callers pass an inconsistent period for an intraday interval.
        if interval == "15m":
            period = "5d"

        df = YahooProvider().history(
            symbol,
            interval=interval,
            period=period,
        )

        if df.empty:
            return df

        close = df["Close"]

        # Belt-and-braces: if a column ever comes back 2D for any
        # reason, squeeze it down to a plain Series rather than
        # crashing the whole chart.
        if hasattr(close, "ndim") and close.ndim > 1:
            close = close.iloc[:, 0]

        df["EMA20"] = ta.trend.ema_indicator(close, window=20)
        df["EMA50"] = ta.trend.ema_indicator(close, window=50)
        df["EMA200"] = ta.trend.ema_indicator(close, window=200)

        df["RSI14"] = ta.momentum.rsi(close, window=14)

        macd = ta.trend.MACD(close)
        df["MACD"] = macd.macd()
        df["MACD_Signal"] = macd.macd_signal()
        df["MACD_Hist"] = macd.macd_diff()

        window = ChartService.SUPPORT_RESISTANCE_WINDOW

        if "Low" in df and "High" in df:
            df["Support"] = df["Low"].rolling(window).min()
            df["Resistance"] = df["High"].rolling(window).max()

        return df