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
from core.timeseries import daily_pct_change, time_based_pct_change
from models.asset import AssetModel
from providers.yahoo import YahooProvider
from services.market_service import MarketService
from services.summary_service import SummaryService
from services.indicator_service import IndicatorService
from dashboard.services.market_status_service import MarketStatusService

class DashboardLoader:

    GLOBAL_MACRO_INDIA_INCLUDE = {"^NSEI", "^NSEBANK"}

    CANONICAL_COLUMNS = [
        "Ticker",
        "Name",
        "Status",
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
        "Intraday %",
        "Setup",
        "Reversal",

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

    @staticmethod
    def _setup_label(trend, rsi):
        """
        Fallback label only - a cheap snapshot read off the
        already-loaded 1H trend/RSI, used if the real RSI-wave screener
        (dashboard/services/rsi_wave_status.py, run separately per
        region load) doesn't have a label for this ticker yet.
        """

        rsi = rsi or 0.0

        if trend == "Bullish":

            if rsi <= 30:
                return "🟢 Bullish · oversold pullback"

            if rsi >= 70:
                return "🟢 Bullish · stretched (high RSI)"

            return "🟢 Bullish · trending"

        if trend == "Bearish":

            if rsi >= 70:
                return "🔴 Bearish · overbought bounce"

            if rsi <= 30:
                return "🔴 Bearish · stretched (low RSI)"

            return "🔴 Bearish · trending"

        return "⚪ No clear trend"

    @staticmethod
    def _price_round(value):
        """
        4 decimals for sub-$10 instruments (forex pairs like EUR/USD
        need that much to show a real, distinguishable level), 2
        otherwise - same convention already used by ReversalPlaybook/
        RSIWaveStatusService/RSIDivergenceStatusService for stop/target
        levels, applied here too so raw yfinance floats (e.g.
        64766.109375) don't leak straight into every table's Price/EMA/
        Support/Resistance columns.
        """

        if value is None:
            return None

        return round(value, 4 if abs(value) < 10 else 2)

    @classmethod
    def _canonical_row(cls, asset):

        d1 = asset.indicators.d1
        technical = cls._technical_values(asset)
        fundamental = FundamentalEngine.analyse(asset)
        valuation = ValuationEngine.analyse(asset)

        row = {
            "Ticker": asset.symbol,
            "Name": asset.name,
            "Status": MarketStatusService.status(asset),
            "Market": asset.asset_class,
            "Country": asset.country,
            "Sector": asset.category,
            "Industry": asset.industry,
            "Price": cls._price_round(asset.summary.price or 0.0),
            "Change %": asset.summary.change_1d or 0.0,
            "Volume": int(asset.summary.volume or 0),
            "Market Cap": cls._market_cap(asset),
            "EMA20": cls._price_round(d1.ema20 or 0.0),
            "EMA50": cls._price_round(d1.ema50 or 0.0),
            "EMA200": cls._price_round(d1.ema200 or 0.0),
            "Trend": d1.trend or "Neutral",
            "RSI": round(d1.rsi14 or 0.0, 2),
            "MACD": technical["MACD"],
            "ADX": technical["ADX"],
            "ATR": technical["ATR"],
            "Support": cls._price_round(technical["Support"]),
            "Resistance": cls._price_round(technical["Resistance"]),
            "Stop Loss": cls._price_round(technical["Stop Loss"]),
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

            # Short-term momentum blend (15m weighted lighter than 1H,
            # since a lone 15m spike is noisier than a sustained 1H move).
            "Intraday %": round(
                0.4 * (asset.summary.change_15m or 0.0)
                + 0.6 * (asset.summary.change_1h or 0.0),
                2,
            ),

            "Setup": cls._setup_label(asset.indicators.h1.trend, asset.indicators.h1.rsi14),

            # Fallback only - the real reversal-playbook screener
            # (dashboard/services/reversal_status.py) overwrites this
            # after a region load, same pattern as "Setup" above.
            "Reversal": "⚪ Watching",

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

    @staticmethod
    def refresh_intraday_prices(df):
        """
        Lightweight intraday refresh for auto-refresh loops: re-fetches
        only 15m bars per ticker and patches Price/15m %/1H %/Intraday %
        in place. Deliberately skips AssetLoader/MarketService/the
        analysis engines - re-running the full `load()` pipeline on a
        timer would re-download 15m/1h/1d history and re-score every
        asset, which is both slow and unnecessary just to keep an
        already-loaded scanner/chart feeling live.
        """

        if df.empty:
            return df

        provider = YahooProvider()
        df = df.copy()

        for idx, row in df.iterrows():

            symbol = row["Ticker"]

            try:
                bars = provider.history(symbol, interval="15m", period="5d")
            except Exception:
                continue

            if bars.empty or len(bars) < 2:
                continue

            close = bars["Close"]
            latest = float(close.iloc[-1])

            # Time-based lookback, not a fixed bar count - a fixed
            # count silently spans into the PRIOR session right after
            # a fresh market open (only today's first bar exists),
            # turning an overnight gap into a mislabeled "1H %" move.
            change_15m = time_based_pct_change(close, 15)
            change_1h = time_based_pct_change(close, 60)

            df.at[idx, "Price"] = DashboardLoader._price_round(latest)
            df.at[idx, "15m %"] = change_15m if change_15m is not None else 0.0
            df.at[idx, "1H %"] = change_1h if change_1h is not None else 0.0
            df.at[idx, "Intraday %"] = round(0.4 * (change_15m or 0.0) + 0.6 * (change_1h or 0.0), 2)

            # "Change %" (the daily change) used to only get updated by
            # the full rescan (every 10 min for Global Indices), while
            # this loop refreshes Price every 45s - real bug found in
            # production: Price already reflecting a bounce-back while
            # Change % still showed the older, more negative reading
            # from up to 10 minutes earlier, visibly contradicting each
            # other in the same UI (e.g. Command Center's Movers table
            # next to the CEO Summary's "Avoid" pick). Same
            # daily_pct_change() SummaryService.build() uses for
            # change_1d, recomputed here from the same 15m/5d bars
            # already fetched above - no extra fetch needed to keep the
            # two consistent.
            change_1d = daily_pct_change(close)

            if change_1d is not None:
                df.at[idx, "Change %"] = change_1d
                # "1D %" is the same underlying value as "Change %"
                # (see dashboard_loader._canonical_row) - only patching
                # one of the two would leave them able to silently
                # disagree with each other for up to 10 minutes,
                # exactly the bug this whole fix addresses.
                if "1D %" in df.columns:
                    df.at[idx, "1D %"] = change_1d

        return df

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
        # Global Macro excludes Indian Indices, except the two
        # headline benchmarks (Nifty 50 / Nifty Bank) - genuinely
        # global-relevant intraday indices, unlike the sector-level
        # Nifty variants (IT/Auto/Pharma/...).
        # ------------------------------------

        if (
            filters["country"] == "Global"
            and filters["sector"] == "All"
        ):

            assets = [
                a
                for a in assets
                if a.category != "Indian Indices" or a.symbol in cls.GLOBAL_MACRO_INDIA_INCLUDE
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
