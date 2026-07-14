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
