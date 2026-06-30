"""
Yahoo Finance Provider.

Responsible ONLY for downloading data.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from providers.base import BaseProvider


class YahooProvider(BaseProvider):

    def download(
        self,
        symbol: str,
        period: str,
        interval: str,
    ) -> pd.DataFrame:

        df = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if df.empty:
            return df

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df