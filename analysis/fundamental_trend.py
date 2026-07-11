"""
Fundamental trend engine.

Answers "is this company's fundamental picture actually getting
better" - not just a single snapshot score (that's what
FundamentalEngine already gives you), but a real trend across recent
quarters: revenue, earnings, margins, ROE, and analyst sentiment.

Uses yfinance's quarterly financials directly, which already span
several past quarters in one call - no need to wait for this screener
to run repeatedly over months before a trend appears.

Deliberately separate from FundamentalEngine (the snapshot scorer) -
this only judges DIRECTION (improving/declining/flat), reusing the
snapshot engine for the "how good is it right now" half instead of
recomputing that too.
"""

import yfinance as yf


class FundamentalTrendEngine:

    MIN_QUARTERS = 3

    @staticmethod
    def _trend_direction(values):
        """
        +1 if mostly increasing across recent quarters, -1 if mostly
        decreasing, 0 if mixed/flat/not enough data. `values` is
        ordered MOST RECENT FIRST (yfinance's own convention).
        """

        clean = [v for v in values if v is not None]

        if len(clean) < FundamentalTrendEngine.MIN_QUARTERS:
            return 0

        chronological = list(reversed(clean))

        diffs = [chronological[i + 1] - chronological[i] for i in range(len(chronological) - 1)]

        up = sum(1 for d in diffs if d > 0)
        down = sum(1 for d in diffs if d < 0)

        if up > down:
            return 1

        if down > up:
            return -1

        return 0

    @classmethod
    def analyse(cls, symbol):

        ticker = yf.Ticker(symbol)

        result = {
            "symbol": symbol,
            "revenue_trend": 0,
            "earnings_trend": 0,
            "margin_trend": 0,
            "roe_trend": 0,
            "analyst_trend": 0,
            "quarters_available": 0,
        }

        try:
            income = ticker.quarterly_income_stmt
        except Exception:
            income = None

        try:
            balance = ticker.quarterly_balance_sheet
        except Exception:
            balance = None

        revenue = None
        net_income = None

        if income is not None and not income.empty:

            if "Total Revenue" in income.index:
                revenue = income.loc["Total Revenue"].tolist()

            if "Net Income" in income.index:
                net_income = income.loc["Net Income"].tolist()

        if revenue:
            result["revenue_trend"] = cls._trend_direction(revenue)
            result["quarters_available"] = len([v for v in revenue if v is not None])

        if net_income:
            result["earnings_trend"] = cls._trend_direction(net_income)

        if revenue and net_income and len(revenue) == len(net_income):

            margins = [
                (ni / rev) if rev else None
                for ni, rev in zip(net_income, revenue)
            ]

            result["margin_trend"] = cls._trend_direction(margins)

        equity = None

        if balance is not None and not balance.empty and "Stockholders Equity" in balance.index:
            equity = balance.loc["Stockholders Equity"].tolist()

        if net_income and equity and len(net_income) == len(equity):

            roe_series = [
                (ni / eq) if eq else None
                for ni, eq in zip(net_income, equity)
            ]

            result["roe_trend"] = cls._trend_direction(roe_series)

        try:

            recs = ticker.recommendations

            if recs is not None and not recs.empty and len(recs) >= 2:

                latest = recs.iloc[0]
                oldest = recs.iloc[-1]

                buy_now = (latest.get("strongBuy", 0) or 0) + (latest.get("buy", 0) or 0)
                sell_now = (latest.get("sell", 0) or 0) + (latest.get("strongSell", 0) or 0)
                buy_before = (oldest.get("strongBuy", 0) or 0) + (oldest.get("buy", 0) or 0)
                sell_before = (oldest.get("sell", 0) or 0) + (oldest.get("strongSell", 0) or 0)

                score_now = buy_now - sell_now
                score_before = buy_before - sell_before

                if score_now > score_before:
                    result["analyst_trend"] = 1
                elif score_now < score_before:
                    result["analyst_trend"] = -1

        except Exception:
            pass

        # -5 (everything declining) to +5 (everything improving).
        result["improving_score"] = (
            result["revenue_trend"]
            + result["earnings_trend"]
            + result["margin_trend"]
            + result["roe_trend"]
            + result["analyst_trend"]
        )

        return result

    @staticmethod
    def label(direction):

        return {1: "up", -1: "down", 0: "flat"}.get(direction, "flat")
