"""
Static known-events economic calendar - FOMC decision dates, US CPI,
and US Nonfarm Payrolls release dates for 2026, sourced from the
Federal Reserve's and BLS's own published schedules (both publish
these many months ahead, so unlike a live news feed this doesn't need
a paid calendar API to be accurate).

Deliberately narrow in scope (just these three event types) rather
than a general economic calendar - these are the events that reliably
move markets across every asset class MarketPulse covers, and a wider
calendar (earnings, minor regional data, speeches) would add noise
without a paid data source to keep it current and complete.

A rare official reschedule (it happens - see the Feb 2026 CPI date
being pushed back two days in BLS's own schedule notes) won't be
reflected here until this file is manually updated.
"""

from pathlib import Path

import pandas as pd

CALENDAR_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "economic_calendar.csv"


def upcoming(days=14, reference_date=None):
    """
    Returns events from reference_date (default: today) through
    reference_date + days, ascending by date. Empty DataFrame if the
    file is missing or nothing falls in range (e.g. the file hasn't
    been extended past the year it was written for).
    """

    if not CALENDAR_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(CALENDAR_PATH, parse_dates=["Date"])

    today = pd.Timestamp(reference_date) if reference_date is not None else pd.Timestamp.now().normalize()
    cutoff = today + pd.Timedelta(days=days)

    window = df[(df["Date"] >= today) & (df["Date"] <= cutoff)]

    return window.sort_values("Date")
