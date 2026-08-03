"""
Activity score - a volatility-expansion gauge, not a directional
signal. Explicit request: "there are moments in day when the
instruments move faster... how can we capture those moments... may be
that we can use to find if we have to participate more or less."

Compares the current ATR against its own recent rolling average - when
a bar's true range is meaningfully wider than what's been typical
lately, something real is happening (breakout, news, session overlap),
regardless of direction. Deliberately stateless (no bar-by-bar phase
machine like the divergence engines) - this is a live snapshot read,
not an entry/exit strategy, so there's nothing to backtest for win
rate. What CAN be checked is whether "high activity" bars are actually
followed by bigger subsequent moves than average - see
analysis/backtester.py's check_activity_score_correlation for that.
"""

import ta

ATR_WINDOW = 14        # matches RSIWaveStatusService.ATR_WINDOW - same short-term volatility read used elsewhere
BASELINE_WINDOW = 50   # the "what's normal lately" comparison window

HIGH_RATIO = 1.5   # current ATR at least 50% above its own recent baseline
QUIET_RATIO = 0.6  # current ATR at most 60% of its own recent baseline

STATE_LABELS = {
    "HIGH": "🔥 High activity",
    "NORMAL": "⚪ Normal",
    "QUIET": "😴 Quiet",
    "NONE": "⚪ No data",
}


def compute_activity(df):
    """
    df needs High/Low/Close. Returns {"ratio", "state", "label"} for
    the LATEST bar, or None if there's not enough history yet
    (BASELINE_WINDOW + ATR_WINDOW bars minimum).
    """

    if df is None or df.empty or len(df) < ATR_WINDOW + BASELINE_WINDOW:
        return None

    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=ATR_WINDOW)
    baseline = atr.rolling(BASELINE_WINDOW).mean()

    current_atr = atr.iloc[-1]
    current_baseline = baseline.iloc[-1]

    if current_baseline is None or current_baseline != current_baseline or current_baseline == 0:
        return None

    ratio = float(current_atr / current_baseline)

    if ratio >= HIGH_RATIO:
        state = "HIGH"
    elif ratio <= QUIET_RATIO:
        state = "QUIET"
    else:
        state = "NORMAL"

    return {"ratio": round(ratio, 2), "state": state, "label": STATE_LABELS[state]}
