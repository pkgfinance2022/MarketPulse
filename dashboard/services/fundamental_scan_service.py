"""
Fundamental scan service.

Shared core loop for the fundamental-improvement scan, used by both
fundamental_scan.py (CLI) and the dashboard's Fundamentals tab, so the
two never drift apart into two different implementations of the same
scan.
"""

import pandas as pd

from analysis.fundamental_engine import FundamentalEngine
from analysis.fundamental_trend import FundamentalTrendEngine
from core.indicator import IndicatorSet
from providers.yahoo import YahooProvider
from services.indicator_service import IndicatorService


class FundamentalScanService:

    @staticmethod
    def daily_technical_trend(symbol):
        """
        Cross-check against the DAILY chart, not hourly - fundamentals
        move on a quarters timescale, so an intraday read would be the
        wrong comparison.
        """

        try:
            df_1d = YahooProvider().history(symbol, interval="1d", period="1y")
        except Exception:
            return "Unknown"

        if df_1d.empty:
            return "Unknown"

        indicator = IndicatorSet("1D")
        IndicatorService._calculate(df_1d, indicator)

        return indicator.trend or "Neutral"

    @classmethod
    def scan(cls, assets, min_score=1, progress_callback=None):
        """
        progress_callback(index, total, symbol), if given, is called
        before each stock is processed - lets the CLI print progress
        and the dashboard update a progress bar without duplicating
        the scan loop itself.
        """

        rows = []
        total = len(assets)

        for index, asset in enumerate(assets, start=1):

            if progress_callback:
                progress_callback(index, total, asset.symbol)

            try:
                fundamental = FundamentalEngine.analyse(asset)
                trend = FundamentalTrendEngine.analyse(asset.symbol)
            except Exception:
                continue

            if trend["improving_score"] < min_score:
                continue

            tech_trend = cls.daily_technical_trend(asset.symbol)

            rows.append(
                {
                    "Ticker": asset.symbol,
                    "Name": asset.name,
                    "Country": asset.country,
                    "Improving Score": trend["improving_score"],
                    "Revenue": FundamentalTrendEngine.label(trend["revenue_trend"]),
                    "Earnings": FundamentalTrendEngine.label(trend["earnings_trend"]),
                    "Margin": FundamentalTrendEngine.label(trend["margin_trend"]),
                    "ROE": FundamentalTrendEngine.label(trend["roe_trend"]),
                    "Analysts": FundamentalTrendEngine.label(trend["analyst_trend"]),
                    "Fundamental Score": fundamental["fundamental_score"],
                    "Daily Trend": tech_trend,
                    "Technical Agrees?": (
                        "YES" if tech_trend == "Bullish"
                        else "NO" if tech_trend == "Bearish"
                        else "Neutral"
                    ),
                }
            )

        df = pd.DataFrame(rows)

        if not df.empty:
            df = df.sort_values("Improving Score", ascending=False).reset_index(drop=True)

        return df
