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

        df = yf.download(
            symbol,
            interval=interval,
            period=period,
            auto_adjust=True,
            progress=False,
            group_by="column",
            multi_level_index=False,
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