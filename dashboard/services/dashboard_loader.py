"""
Dashboard Loader

Loads only the assets selected by the user.
"""

import pandas as pd

from analysis.fundamental_engine import FundamentalEngine
from analysis.risk_engine import RiskEngine
from analysis.scoring_engine import ScoringEngine
from analysis.signal_engine import SignalEngine
from analysis.technical_engine import TechnicalEngine
from analysis.valuation_engine import ValuationEngine
from core.loader import AssetLoader
from models.asset import AssetModel
from services.market_service import MarketService
from services.summary_service import SummaryService
from services.indicator_service import IndicatorService


class DashboardLoader:

    CANONICAL_COLUMNS = [
        "Ticker",
        "Name",
        "Market",
        "Country",
        "Sector",
        "Industry",
        "Price",
        "Change %",
        "Volume",
        "Market Cap",
        "EMA20",
        "EMA50",
        "EMA200",
        "Trend",
        "RSI",
        "MACD",
        "ADX",
        "ATR",
        "Support",
        "Resistance",
        "Stop Loss",
        "Technical Score",
        "Fundamental Score",
        "Momentum Score",
        "Valuation Score",
        "Risk Score",
        "News Score",
        "Macro Score",
        "AI Score",
        "Signal",
        "Confidence",
        "Risk",
        "Portfolio",
        "Quantity",
        "Average Buy",
        "Watchlist",
        "Priority",
        "Target Allocation",
        "Theme",
        "Investment Theme",
        "Thesis",
        "Expected CAGR",
        "Time Horizon",
        "Next Review Date",
        "Exit Conditions",
        "Score",
        "15m %",
        "1H %",
        "15m RSI",
        "1H RSI",
        "1D RSI",

        "15m Trend",
        "1H Trend",
        "1D Trend",

        "15m EMA20",
        "1H EMA20",
        "1D EMA20",

        "15m EMA50",
        "1H EMA50",
        "1D EMA50",

        "15m EMA200",
        "1H EMA200",
        "1D EMA200",

        "1D %",
        
    ]

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
                    "quantity": getattr(a, "quantity", 0.0),
                    "average_buy": getattr(a, "average_buy", 0.0),
                    "watchlist": getattr(a, "watchlist", False),
                    "priority": getattr(a, "priority", 3),
                    "target_allocation": getattr(a, "target_allocation", 0.0),
                    "theme": getattr(a, "theme", ""),
                    "investment_theme": getattr(a, "investment_theme", ""),
                    "thesis": getattr(a, "thesis", ""),
                    "expected_cagr": getattr(a, "expected_cagr", 0.0),
                    "time_horizon": getattr(a, "time_horizon", ""),
                    "next_review_date": getattr(a, "next_review_date", ""),
                    "exit_conditions": getattr(a, "exit_conditions", ""),
                }
            )

        return pd.DataFrame(rows)

    @staticmethod
    def _latest(series, default=0.0):

        if series is None or series.empty:
            return default

        value = series.iloc[-1]

        if pd.isna(value):
            return default

        return round(float(value), 2)

    @classmethod
    def _market_cap(cls, asset):

        for key in ("Market Cap", "MarketCap", "market_cap"):
            value = getattr(asset, key, 0)
            if value:
                return value

        return 0

    @classmethod
    def _technical_values(cls, asset):

        technical = TechnicalEngine.analyse(asset)

        return {
            "MACD": technical["macd_value"],
            "ADX": technical["adx"],
            "ATR": technical["atr"],
            "Support": technical["support"],
            "Resistance": technical["resistance"],
            "Stop Loss": technical["stop_loss"],
            "Technical Score": technical["technical_score"],
        }

    @classmethod
    def _canonical_row(cls, asset):

        d1 = asset.indicators.d1
        technical = cls._technical_values(asset)
        fundamental = FundamentalEngine.analyse(asset)
        valuation = ValuationEngine.analyse(asset)

        row = {
            "Ticker": asset.symbol,
            "Name": asset.name,
            "Market": asset.asset_class,
            "Country": asset.country,
            "Sector": asset.category,
            "Industry": asset.industry,
            "Price": asset.summary.price or 0.0,
            "Change %": asset.summary.change_1d or 0.0,
            "Volume": int(asset.summary.volume or 0),
            "Market Cap": cls._market_cap(asset),
            "EMA20": d1.ema20 or 0.0,
            "EMA50": d1.ema50 or 0.0,
            "EMA200": d1.ema200 or 0.0,
            "Trend": d1.trend or "Neutral",
            "RSI": d1.rsi14 or 0.0,
            "MACD": technical["MACD"],
            "ADX": technical["ADX"],
            "ATR": technical["ATR"],
            "Support": technical["Support"],
            "Resistance": technical["Resistance"],
            "Stop Loss": technical["Stop Loss"],
            "Technical Score": technical["Technical Score"],
            "Fundamental Score": fundamental["fundamental_score"],
            "Momentum Score": 0,
            "Valuation Score": valuation["valuation_score"],
            "Risk Score": 0,
            "News Score": 50,
            "Macro Score": 50,
            "AI Score": 0,
            "Signal": "",
            "Confidence": 0,
            "Risk": "MEDIUM",
            "Portfolio": bool(getattr(asset, "portfolio", False)),
            "Quantity": getattr(asset, "quantity", 0.0),
            "Average Buy": getattr(asset, "average_buy", 0.0),
            "Watchlist": bool(getattr(asset, "watchlist", False)),
            "Priority": getattr(asset, "priority", 3),
            "Target Allocation": getattr(asset, "target_allocation", 0.0),
            "Theme": getattr(asset, "theme", ""),
            "Investment Theme": getattr(asset, "investment_theme", ""),
            "Thesis": getattr(asset, "thesis", ""),
            "Expected CAGR": getattr(asset, "expected_cagr", 0.0),
            "Time Horizon": getattr(asset, "time_horizon", ""),
            "Next Review Date": getattr(asset, "next_review_date", ""),
            "Exit Conditions": getattr(asset, "exit_conditions", ""),
            "15m %": asset.summary.change_15m or 0.0,
            "1H %": asset.summary.change_1h or 0.0,
            "1D %": asset.summary.change_1d or 0.0,

            # --------------------
            # Multi-timeframe RSI
            # --------------------

            "15m RSI": asset.indicators.m15.rsi14 or 0.0,
            "1H RSI": asset.indicators.h1.rsi14 or 0.0,
            "1D RSI": asset.indicators.d1.rsi14 or 0.0,

            # --------------------
            # Multi-timeframe Trend
            # --------------------

            "15m Trend": asset.indicators.m15.trend,
            "1H Trend": asset.indicators.h1.trend,
            "1D Trend": asset.indicators.d1.trend,

            # --------------------
            # EMA
            # --------------------

            "15m EMA20": asset.indicators.m15.ema20,
            "1H EMA20": asset.indicators.h1.ema20,
            "1D EMA20": asset.indicators.d1.ema20,

            "15m EMA50": asset.indicators.m15.ema50,
            "1H EMA50": asset.indicators.h1.ema50,
            "1D EMA50": asset.indicators.d1.ema50,

            "15m EMA200": asset.indicators.m15.ema200,
            "1H EMA200": asset.indicators.h1.ema200,
            "1D EMA200": asset.indicators.d1.ema200,
            
        }

        risk = RiskEngine.analyse(row)
        row["Risk"] = risk["risk"]
        row["Risk Score"] = risk["risk_score"]
        row["Stop Loss"] = risk["stop_loss"]
        row["Target 1"] = risk["target1"]
        row["Target 2"] = risk["target2"]
        row["Risk Reward"] = risk["risk_reward"]

        ScoringEngine.score(row)

        signal = SignalEngine.analyse(row)
        row["Signal"] = signal["signal"]
        row["Confidence"] = signal["confidence"]
        row["Reasons"] = signal["reasons"]
        row["Risks"] = signal["risks"]

        return row

    @classmethod
    def _asset_model(cls, asset):

        return AssetModel.from_dict(cls._canonical_row(asset))

    @classmethod
    def load_assets(cls, filters):

        df, success, failed = cls.load(filters)

        assets = [
            AssetModel.from_dict(row)
            for row in df.to_dict("records")
        ]

        return assets, success, failed

    @classmethod
    def load(cls, filters):

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
        # Priority
        # ------------------------------------

        minimum_priority = filters.get("priority", 1)

        assets = [
            a
            for a in assets
            if getattr(a, "priority", 3) >= minimum_priority
        ]

        # ------------------------------------
        # Download
        # ------------------------------------

        repo = MarketService().load_market(assets)

        rows = []

        success = 0
        failed = 0

        for asset in repo.all():

            SummaryService.build(asset)
            IndicatorService.build(asset)

            if asset.summary.price is None:
                failed += 1
                continue

            success += 1

            rows.append(cls._canonical_row(asset))

        df = pd.DataFrame(rows, columns=cls.CANONICAL_COLUMNS + ["Reasons", "Risks"])

        if not df.empty:
            df = df.sort_values(
                "Score",
                ascending=False,
            )

        return df, success, failed
