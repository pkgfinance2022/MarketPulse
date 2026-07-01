import yfinance as yf

import pandas as pd

from ta.trend import EMAIndicator


class ChartService:

    @staticmethod
    def history(symbol):

        df = yf.download(
            symbol,
            period="1y",
            interval="1d",
            progress=False,
        )

        df["EMA20"] = EMAIndicator(
            df["Close"],
            20,
        ).ema_indicator()

        df["EMA50"] = EMAIndicator(
            df["Close"],
            50,
        ).ema_indicator()

        return df