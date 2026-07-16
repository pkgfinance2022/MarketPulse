"""
CET/CEST display helpers.

Every timestamp shown on a dashboard page should read in Central
European time, regardless of the exchange/instrument's own timezone
(yfinance returns tz-aware bars in the exchange's local tz - Asia/
Kolkata for NSE, America/New_York for US stocks, Asia/Tokyo for
Nikkei, etc.) or the server's own local clock (which varies by
deployment host). "Europe/Berlin" tracks CET/CEST with DST handled
automatically, unlike a fixed UTC+1 offset.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

CET = ZoneInfo("Europe/Berlin")


def now_cet():
    return datetime.now(CET)


def unix_to_cet(epoch_seconds):
    """Converts a time.time()-style Unix timestamp to a CET datetime."""

    return datetime.fromtimestamp(epoch_seconds, tz=CET)


def to_cet(ts):
    """
    Converts a tz-aware timestamp (pandas Timestamp, datetime, etc.)
    to CET. Naive timestamps are assumed to already be UTC (the one
    case this shows up is a plain time.time()/datetime.now() value
    with no tz attached) since every engine's own bar timestamps come
    back tz-aware from yfinance.
    """

    ts = pd.Timestamp(ts)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")

    return ts.tz_convert(CET)


def format_event_time(ts):
    """
    Formats an engine's event_time into a display string - "Jul 16, 7
    PM CET". Rounded to the hour deliberately (no minutes) - plenty
    precise for "when did this happen". Hourly/15m bars carry a real
    time of day, always converted to CET/CEST so events from different
    exchanges (each with their own tz from yfinance) read on one
    shared clock. Daily/Weekly bars carry a midnight exchange-local
    timestamp that only identifies WHICH trading day - converting that
    to CET would risk shifting it onto the wrong calendar date (e.g. a
    Tokyo midnight bar sliding back a day), so those are shown as their
    original date, untouched.
    """

    if ts is None or pd.isna(ts):
        return "—"

    ts = pd.Timestamp(ts)

    if ts.hour == 0 and ts.minute == 0:
        return ts.strftime("%b %d")

    ts = to_cet(ts)
    hour12 = ts.hour % 12 or 12
    ampm = "AM" if ts.hour < 12 else "PM"

    return f"{ts.strftime('%b %d')}, {hour12} {ampm} CET"
