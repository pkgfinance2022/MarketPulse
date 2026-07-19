"""
Weekly/Monthly % return per instrument - "what actually moved the most
this week/month", not just today's change.

Run once a day (see dashboard/services/performance_ranking_status.py
and its universe_cache wiring), not continuously - a week/month-old
return doesn't meaningfully change intra-day, so there's nothing to
gain from checking more often. One history fetch per symbol covers
both windows (no separate weekly/monthly fetch needed).
"""

from providers.yahoo import YahooProvider

WEEK_TRADING_DAYS = 5
MONTH_TRADING_DAYS = 21


def check_performance(symbol):
    """
    Returns {"week_pct": float or None, "month_pct": float or None} -
    None for a window if there isn't enough daily history yet (a
    recent IPO/listing).
    """

    df = YahooProvider().history(symbol, interval="1d", period="2mo")

    if df.empty:
        return {"week_pct": None, "month_pct": None}

    close = df["Close"]
    last = float(close.iloc[-1])

    def pct_change_over(trading_days):

        if len(close) <= trading_days:
            return None

        base = float(close.iloc[-(trading_days + 1)])

        if base == 0:
            return None

        return round((last / base - 1) * 100, 2)

    return {
        "week_pct": pct_change_over(WEEK_TRADING_DAYS),
        "month_pct": pct_change_over(MONTH_TRADING_DAYS),
    }
