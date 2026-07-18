"""
Weekly/Monthly 200/50 EMA proximity watchlist.

Not an entry signal - just flags instruments currently trading close to
a major long-term trend line, worth a manual look (support in an
uptrend, resistance in a downtrend, or a potential multi-year
trend-change zone). Weekly uses a 200-period EMA (~4 years of weekly
bars). Monthly deliberately uses a 50-period EMA, not 200 - a literal
200-period Monthly EMA needs ~17 years of history (most tickers don't
have that much, crypto especially) and even when it does exist, it's
so slow-moving that a stock in a normal long-term uptrend sits 70-300%
away from it, never triggering "near" at all. 50 months (~4 years) is
roughly the same timeframe as Weekly-200, just measured on a chunkier
bar size, and is actually usable.

Run once a day (see dashboard/services/ema_proximity_status.py and its
universe_cache wiring), not continuously - Weekly/Monthly bars don't
change intra-day, so there's nothing to gain from checking more often.
"""

import ta

from providers.yahoo import YahooProvider

WEEKLY_EMA_PERIOD = 200
MONTHLY_EMA_PERIOD = 50

PROXIMITY_TOLERANCE_PCT = 3.0


def _check_timeframe(symbol, interval, period, ema_period):

    df = YahooProvider().history(symbol, interval=interval, period=period)

    if df.empty or len(df) < ema_period:
        return None

    close = df["Close"]
    ema = ta.trend.ema_indicator(close, window=ema_period)

    price = float(close.iloc[-1])
    ema_val = float(ema.iloc[-1])

    if ema_val == 0:
        return None

    distance_pct = (price - ema_val) / ema_val * 100

    return {
        "price": price,
        "ema": round(ema_val, 4),
        "distance_pct": round(distance_pct, 2),
        "near": abs(distance_pct) <= PROXIMITY_TOLERANCE_PCT,
        "side": "above" if distance_pct >= 0 else "below",
    }


def check_proximity(symbol):
    """Returns {"weekly": {...} or None, "monthly": {...} or None}."""

    return {
        "weekly": _check_timeframe(symbol, "1wk", "10y", WEEKLY_EMA_PERIOD),
        "monthly": _check_timeframe(symbol, "1mo", "max", MONTHLY_EMA_PERIOD),
    }
