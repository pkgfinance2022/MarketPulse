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

        assets = AssetLoader().all_assets()

        rows = []

        for a in assets:

            rows.append(
                {
                    "country": getattr(a, "country", ""),
                    "sector": getattr(a, "category", ""),
                    "industry": getattr(a, "industry", ""),
                    "asset_class": getattr(a, "asset_class", ""),
                    "name": a.name,
                    "symbol": a.symbol,
                    "portfolio": getattr(a, "portfolio", False),
                    "watchlist": getattr(a, "watchlist", False),
                    "priority": getattr(a, "priority", 3),
                }
            )

        return pd.DataFrame(rows)

    @staticmethod
    def load(filters):

        assets = AssetLoader().all_assets()

        # ------------------------------------
        # Country
        # ------------------------------------

        if filters["country"] != "All":

            assets = [
                a
                for a in assets
                if a.country.lower() == filters["country"].lower()
            ]

        # ------------------------------------
        # Global Macro excludes Indian Indices
        # ------------------------------------

        if (
            filters["country"] == "Global"
            and filters["sector"] == "All"
        ):

            assets = [
                a
                for a in assets
                if a.category != "Indian Indices"
            ]

        # ------------------------------------
        # Sector
        # ------------------------------------

        if filters["sector"] != "All":

            assets = [
                a
                for a in assets
                if a.category == filters["sector"]
            ]

        # ------------------------------------
        # Search
        # ------------------------------------

        search = filters["search"].strip().lower()

        if search:

            assets = [
                a
                for a in assets
                if search in a.name.lower()
                or search in a.symbol.lower()
            ]

        # ------------------------------------
        # Portfolio
        # ------------------------------------

        if filters.get("portfolio_only", False):

            assets = [
                a
                for a in assets
                if getattr(a, "portfolio", False)
            ]

        # ------------------------------------
        # Watchlist
        # ------------------------------------

        if filters.get("watchlist_only", False):

            assets = [
                a
                for a in assets
                if getattr(a, "watchlist", False)
            ]

        # ------------------------------------
        # Download
        # ------------------------------------

        repo = MarketService().load_market(assets)
        
        # ------------------------------------
        # Priority
        # ------------------------------------

        minimum_priority = filters.get("priority", 1)

        assets = [
            a
            for a in assets
            if getattr(a, "priority", 3) >= minimum_priority
        ]
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

                    "15m %": asset.summary.change_15m,
                    "1H %": asset.summary.change_1h,

                    "Score": asset.scores.get("overall"),

                    "15m RSI": round(asset.indicators.m15.rsi14)
                    if asset.indicators.m15.rsi14 is not None
                    else None,

                    "1H RSI": round(asset.indicators.h1.rsi14)
                    if asset.indicators.h1.rsi14 is not None
                    else None,

                    "1D RSI": round(asset.indicators.d1.rsi14)
                    if asset.indicators.d1.rsi14 is not None
                    else None,

                    "15m Trend": trend(asset.indicators.m15.trend),
                    "1H Trend": trend(asset.indicators.h1.trend),
                    "1D Trend": trend(asset.indicators.d1.trend),
                }
            )

        df = pd.DataFrame(rows)

        if not df.empty:
            df = df.sort_values(
                "Score",
                ascending=False,
            )

        return df, success, failed