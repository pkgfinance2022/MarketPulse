"""
Timeseries helpers.

time_based_pct_change replaces a naive "go back N bars" percent-change
calculation. A fixed bar count silently breaks right after any session
gap (market open, post-weekend, post-holiday): if only 1 bar exists so
far today and you ask for "4 bars back" to get a 1H change, you get
today's open vs sometime in *yesterday's* afternoon session - an
overnight gap mislabeled as a 1-hour move, not a bug that shows up
later in the day, only right after open.
"""

import pandas as pd


def time_based_pct_change(close, minutes, max_gap_ratio=1.5):
    """
    Percent change from the latest bar back to whichever bar sits
    closest to `minutes` minutes earlier, using real timestamps rather
    than a fixed bar count. Returns None if no bar exists within
    max_gap_ratio x minutes of the target - e.g. right after a fresh
    market open, when today's session doesn't have enough history yet
    and the only earlier bar available is from the prior session.
    """

    if close is None or len(close) < 2:
        return None

    latest_time = close.index[-1]
    latest_price = float(close.iloc[-1])

    target_time = latest_time - pd.Timedelta(minutes=minutes)

    candidates = close[close.index <= target_time]

    if candidates.empty:
        return None

    reference_time = candidates.index[-1]
    gap_minutes = (latest_time - reference_time).total_seconds() / 60

    if gap_minutes > minutes * max_gap_ratio:
        return None

    reference_price = float(candidates.iloc[-1])

    if reference_price == 0:
        return None

    return round((latest_price / reference_price - 1) * 100, 2)
