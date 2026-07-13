"""
Lightweight 15m readiness check.

NOT a full independent 15m engine - that dual-timeframe complexity was
explicitly removed earlier this session. This only runs for symbols
whose 1H Reversal Playbook state has ALREADY confirmed (RSI crossed 65,
Path C forming, or an actual BUY signal) - a confirmation lens applied
to a small subset of symbols, never scanned across the whole universe.

Mirrors the same "touch oversold, then move toward 65" shape as the
1H engine's Step 1/2, just at 15m granularity - per the observed
pattern: when the 1H is on a definite run and RSI holds ~40 as
support, the 15m chart often shows this same touch-then-recover
readiness at the same moment.
"""

import pandas as pd
import ta

from providers.yahoo import YahooProvider

OVERSOLD_TOUCH = 30    # 15m RSI oversold level - a bit looser than the 1H engine's 22, since 15m swings faster
TARGET_LEVEL = 65
LOOKBACK_BARS = 40     # ~10 hours of 15m bars - how far back to look for a recent oversold touch


def check_readiness(symbol):
    """
    Returns {"ready": bool, "label": str, "rsi": float} or None if
    there isn't enough recent 15m history. `ready=True` covers both a
    completed touch-then-65-cross and a resuming-toward-65 read - both
    count as "15m getting ready", per the pattern being confirmed.
    """

    df = YahooProvider().history(symbol, interval="15m", period="5d")

    if df.empty or len(df) < LOOKBACK_BARS:
        return None

    # RSI on OHLC4 (typical price), not raw Close - consistent with
    # the 1H/Daily/Weekly engines this confirms against.
    typical_price = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    rsi = ta.momentum.rsi(typical_price, window=14)
    recent_rsi = rsi.iloc[-LOOKBACK_BARS:]

    if recent_rsi.isna().all() or pd.isna(rsi.iloc[-1]):
        return None

    touch_low = float(recent_rsi.min())
    current = float(rsi.iloc[-1])

    touched_oversold = touch_low <= OVERSOLD_TOUCH
    crossed_target = current >= TARGET_LEVEL
    resuming = touched_oversold and not crossed_target and current > touch_low + 10

    if touched_oversold and crossed_target:
        return {"ready": True, "label": "🟢 15m ready — touched oversold, crossed 65", "rsi": round(current, 2)}

    if resuming:
        return {"ready": True, "label": "🟡 15m warming — touched oversold, rising toward 65", "rsi": round(current, 2)}

    return {"ready": False, "label": "⚪ 15m not aligned yet", "rsi": round(current, 2)}
