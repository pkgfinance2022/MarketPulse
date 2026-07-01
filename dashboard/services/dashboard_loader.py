"""
Dashboard Loader

Loads only the assets selected by the user.
"""

import pandas as pd

from core.loader import AssetLoader
from services.market_service import MarketService
from services.summary_service import SummaryService
from services.indicator_service import IndicatorService
from services.score_service import ScoreService
from utils.formatter import (
    trend,
    rsi,
    score,
    price,
    change,
)

class DashboardLoader:

    @staticmethod
    def metadata():

        loader = AssetLoader()
        assets = loader.all_assets()

        rows = []

        for a in assets:

            rows.append(
                {
                    "country": getattr(a, "country", ""),
                    "sector": a.category,
                    "name": a.name,
                    "symbol": a.symbol,
                }
            )

        return pd.DataFrame(rows)

    @staticmethod
    def load(filters):

        loader = AssetLoader()

        assets = loader.all_assets()

       
        # ------------------------
        # Country
        # ------------------------

        if filters["country"] != "All":

            assets = [
                a
                for a in assets
                if getattr(a, "country", "").strip().lower()
                == filters["country"].strip().lower()
            ]

            # ------------------------
            # Special Case:
            # Global Macro should exclude Indian Indices
            # ------------------------

            if (
                filters["country"] == "Global"
                and filters["sector"] == "All"
            ):

                assets = [
                    a
                    for a in assets
                    if a.category != "Indian Indices"
                ]

        if filters["sector"] != "All":

            assets = [
                a
                for a in assets
                if a.category == filters["sector"]
            ]

        # ------------------------
        # Search
        # ------------------------

        search = filters["search"].strip().lower()

        if search:

            assets = [
                a
                for a in assets
                if search in a.name.lower()
                or search in a.symbol.lower()
            ]

        repo = MarketService().load_market(assets)

        rows = []

        success = 0
        failed = 0

        for asset in repo.all():

            SummaryService.build(asset)
            IndicatorService.build(asset)
            ScoreService.build(asset)

            if asset.summary.price is None:
                failed += 1
                continue

            success += 1

            rows.append(
                {
                    "Country": asset.country,
                    "Sector": asset.category,
                    "Asset": asset.name,
                    "Symbol": asset.symbol,
                    "Price": asset.summary.price,
                    "Score": asset.scores.get("overall"),
                    "15m RSI": asset.indicators.m15.rsi14,
                    "1H RSI": asset.indicators.h1.rsi14,
                    "1D RSI": rsi(asset.indicators.d1.rsi14),
                    "15m %": change(asset.summary.change_15m) if asset.summary.change_15m is not None else "--",
                    "1H %": change(asset.summary.change_1h) if asset.summary.change_1h is not None else "--",
                    "15m Trend": trend(asset.indicators.m15.trend),
                    "1H Trend": trend(asset.indicators.h1.trend),
                    "1D Trend": trend(asset.indicators.d1.trend),
                }
            )

        df = pd.DataFrame(rows)

        if not df.empty:
            df = df.sort_values("Score", ascending=False)

        return df, success, failed
    def color_rsi(val):
        if val >= 70:
            return "color:red"
        elif val >= 55:
            return "color:green"
        elif val >= 45:
            return "color:orange"
        return "color:blue"

        styled = df.style.map(
            color_rsi,
            subset=["15m RSI", "1H RSI", "1D RSI"],
        )

        st.dataframe(styled, width="stretch")