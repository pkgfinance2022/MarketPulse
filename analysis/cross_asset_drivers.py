"""
Cross-asset driver analysis - for a given instrument, measures its
ROLLING same-day correlation against a fixed driver set (US 10Y yield,
DXY, Oil, Gold) over a recent window, then checks whether TODAY's
actual driver moves align with that instrument's own historically-
measured sensitivity.

Deliberately framed as concurrent, same-day alignment - NOT a
forecast, and NOT a fixed/permanent relationship. Checked against 2
years of real daily data before building this: DXY/US10Y/Oil showed
near-zero same-day correlation with NASDAQ/S&P500 on a 2-year average
(0.01-0.13), and essentially zero NEXT-DAY predictive power
(yesterday's driver move vs today's index return: 0.00-0.05) - so this
does not predict tomorrow's move. But the same-day correlation over
just the last 90 days was much stronger (US10Y -0.49, DXY -0.48, Oil
-0.35, Gold +0.39 for NASDAQ) - a real, current, regime-dependent
relationship, which is what this module measures and explains, framed
honestly as "moving together today" rather than "caused by."

VIX is deliberately excluded from the driver set here (unlike
analysis/market_regime.py, which does use it) - VIX is mathematically
derived from S&P option prices, so its correlation with equity indices
is partly circular, not an independent cross-asset relationship.
"""

from providers.yahoo import YahooProvider

CORRELATION_WINDOW_DAYS = 90
MEANINGFUL_CORRELATION = 0.3
DRIVER_MOVE_THRESHOLD_PCT = 0.15   # below this, a driver's own move today is too small to explain anything

DRIVER_TICKERS = {"US10Y": "^TNX", "DXY": "DX-Y.NYB", "Oil": "CL=F", "Gold": "GC=F"}


class CrossAssetDriverEngine:

    @staticmethod
    def _daily_returns(symbol, provider):

        bars = provider.history(symbol, interval="1d", period="180d")

        if bars.empty or len(bars) < CORRELATION_WINDOW_DAYS + 1:
            return None

        bars = bars.copy()
        bars.index = bars.index.tz_localize(None) if bars.index.tz is not None else bars.index
        bars.index = bars.index.normalize()

        return bars["Close"].pct_change().dropna()

    @classmethod
    def correlations(cls, symbol, provider=None):
        """
        Returns {"US10Y": corr, "DXY": corr, "Oil": corr, "Gold": corr}
        over the most recent CORRELATION_WINDOW_DAYS trading days that
        `symbol` and each driver both have data for. A driver is
        omitted if there isn't enough overlapping history. Pass a
        shared YahooProvider instance when computing this for many
        symbols against the same drivers, to reuse its fetch cache.
        """

        provider = provider or YahooProvider()

        target_returns = cls._daily_returns(symbol, provider)

        if target_returns is None:
            return {}

        result = {}

        for name, ticker in DRIVER_TICKERS.items():

            driver_returns = cls._daily_returns(ticker, provider)

            if driver_returns is None:
                continue

            aligned = target_returns.align(driver_returns, join="inner")
            target_aligned, driver_aligned = aligned

            if len(target_aligned) < CORRELATION_WINDOW_DAYS:
                continue

            target_recent = target_aligned.tail(CORRELATION_WINDOW_DAYS)
            driver_recent = driver_aligned.tail(CORRELATION_WINDOW_DAYS)

            corr = target_recent.corr(driver_recent)

            if corr == corr:  # not NaN
                result[name] = round(corr, 3)

        return result

    @classmethod
    def explain_today(cls, symbol, correlations, today_driver_changes):
        """
        today_driver_changes: {"US10Y": change_pct, "DXY": change_pct, ...}
        (the SAME "Change %" values already on the global_market
        dataframe - no extra fetch needed for "today").

        Returns a list of plain-language notes for drivers that are
        BOTH meaningfully correlated (over the rolling window) AND
        moved meaningfully today, explaining whether today's move in
        that driver lines up with `symbol`'s own recent sensitivity.
        Empty list if nothing qualifies - not every day has a clean
        story, and forcing one would be exactly the fake-precision
        problem this was built to avoid.
        """

        notes = []

        for name, corr in correlations.items():

            if abs(corr) < MEANINGFUL_CORRELATION:
                continue

            change = today_driver_changes.get(name)

            if change is None or abs(change) < DRIVER_MOVE_THRESHOLD_PCT:
                continue

            expected_direction = "down" if (corr > 0) == (change < 0) else "up"
            # corr > 0 means they move together; corr < 0 means opposite.
            # expected_direction is what `symbol` "should" do today if
            # this relationship holds, given the driver's actual move.
            if corr > 0:
                expected_direction = "down" if change < 0 else "up"
            else:
                expected_direction = "up" if change < 0 else "down"

            notes.append({
                "driver": name,
                "driver_change_pct": change,
                "correlation": corr,
                "expected_direction": expected_direction,
            })

        notes.sort(key=lambda n: abs(n["correlation"]), reverse=True)

        return notes
