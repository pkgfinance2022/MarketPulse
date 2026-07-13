"""
Yahoo Finance Provider
"""

import pandas as pd
import yfinance as yf


class YahooProvider:

    def history(
        self,
        symbol,
        interval="1d",
        period="1y",
    ):
        """
        Uses yf.Ticker(symbol).history(), NOT yf.download() - every
        call site here fetches one symbol at a time, but yf.download()
        always routes through yfinance's own internal multi-threaded
        bulk-download path (via the `multitasking` package) regardless
        of how many tickers you pass it, spawning its OWN thread pool
        on top of whatever ThreadPoolExecutor calls this from. That
        compounded with this app's own thread usage to exceed
        Streamlit Cloud's container thread limit ("RuntimeError: can't
        start new thread"). Ticker.history() is a single direct
        request with no internal threading at all.
        """

        df = yf.Ticker(symbol).history(
            interval=interval,
            period=period,
            auto_adjust=True,
        )

        if df.empty:
            return df

        # Flatten MultiIndex columns if yfinance returns them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Keep only the columns we use
        expected = ["Open", "High", "Low", "Close", "Volume"]

        for col in expected:
            if col not in df.columns:
                df[col] = None

        return df[expected]