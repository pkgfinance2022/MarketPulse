"""
Ranks currently-actionable Command Center rows by each engine's own
backtested track record, rather than just listing everything that's
technically "actionable" right now.

Win rate alone is misleading here: RSI Wave's Setup engine wins 76.5%
of the time but nets essentially 0% per trade (lots of small wins, a
few large losses), while Piercing Pattern/Double Bottom both win less
often but net +0.76% per trade. Ranked by avg return - the actual
expectancy - with win rate shown alongside for context, and only
engines with a genuinely positive backtested edge are included at all
(a "highest conviction" list that includes a historically breakeven-
or-losing engine isn't conviction, it's noise).

Static numbers, not live-recalculated - these are the same 27-symbol,
365-day Global Indices backtest results already reported to the user
this session. Refreshing them for real would mean re-running
analysis/backtester.py against current data, a separate, heavier
operation from just reading what's already actionable right now -
worth doing periodically, not on every page load.
"""

WIN_RATE_LOOKUP = {
    "Setup": {"win_rate": 76.5, "avg_return": -0.01, "n": 1578},
    "Reversal": {"win_rate": 44.9, "avg_return": 0.13, "n": 1247},
    "Daily Reversal": {"win_rate": 36.4, "avg_return": 0.22, "n": 59},
    "RSI Divergence": {"win_rate": 29.2, "avg_return": -0.00, "n": 345},
    "Chart Patterns:Piercing Pattern": {"win_rate": 54.8, "avg_return": 0.76, "n": 32},
    "Chart Patterns:Double Bottom": {"win_rate": 77.0, "avg_return": 0.76, "n": 92},
    # ~2 years, 9 Global Indices instruments (analysis/backtester.py's
    # backtest_ema_reclaim), AFTER adding MIN_DOWNTREND_DIVERGENCE_PCT
    # (a real bug found live: it fired on Russell 2000 while chopping
    # sideways, EMA20/EMA200 tangled ~0.2% apart, not a real downtrend).
    # Requiring a genuine down-move dropped trade count sharply
    # (941->298) and even lowered the raw win rate (57.7%->47.7%), but
    # nearly 9x'd the average return (0.01%->0.09%) - fewer, deeper,
    # more real setups. EUR/USD stopped qualifying at all under a flat
    # 2% divergence threshold (its volatility is naturally much
    # smaller than gold/oil/equities) - worth revisiting if that
    # matters, not treated as broken.
    "EMA Reclaim": {"win_rate": 47.7, "avg_return": 0.09, "n": 298},
    # Daily variant (analysis/backtester.py's backtest_daily_ema_reclaim),
    # same ~2 years, 11 instruments (now including BTC/ETH). Genuinely
    # thin sample - n=42 total, and most individual instruments only
    # had 0-2 qualifying trades in the whole window (major equity
    # indices: 1 each). The two instruments with an actually meaningful
    # sample - Oil (n=10) and ETH (n=11) - both came back NEGATIVE
    # (-0.47%/-0.62%), while the aggregate average return looks
    # positive (+0.45%) almost entirely because of a few single-trade
    # outliers (Russell 2000 +7.85% on n=1). Included per explicit
    # instruction, but this number is much less trustworthy than every
    # other entry here - treat "Daily EMA Reclaim" signals with more
    # skepticism than the Hourly version until more data accumulates.
    "Daily EMA Reclaim": {"win_rate": 47.5, "avg_return": 0.45, "n": 42},
}


def _lookup_key(column, why_text):

    if column == "Chart Patterns":

        text = (why_text or "").lower()

        if "double bottom" in text:
            return "Chart Patterns:Double Bottom"

        if "piercing" in text:
            return "Chart Patterns:Piercing Pattern"

        return None

    return column


def lookup_stats(column, why_text):
    """
    Returns {"win_rate", "loss_rate", "avg_return", "n"} for this
    row's engine, or None if there's no backtest for it. Win/Loss is a
    binary split (every simulated trade in the backtest resolved to
    hit-target or hit-stop, no "still open" bucket) - this is that
    engine's own historical hit rate, not a per-instance forecast for
    this specific signal right now. Used to annotate every actionable
    row (not just the "positive edge" subset rank() filters to), so
    you can see the real track record even for engines that don't
    make the Highest Conviction cut.
    """

    stats = WIN_RATE_LOOKUP.get(_lookup_key(column, why_text))

    if stats is None:
        return None

    return {
        "win_rate": stats["win_rate"],
        "loss_rate": round(100 - stats["win_rate"], 1),
        "avg_return": stats["avg_return"],
        "n": stats["n"],
    }


def rank(rows, min_avg_return=0.0, top_n=10):
    """
    rows: the same row-dict shape _build_command_center_rows() already
    produces (needs "Signal Type" and "Why" at minimum). Returns the
    subset with a backtested avg return above min_avg_return,
    annotated with Win Rate %/Avg Return %/Backtest N, sorted by avg
    return descending.
    """

    ranked = []

    for row in rows:

        key = _lookup_key(row.get("Signal Type"), row.get("Why"))
        stats = WIN_RATE_LOOKUP.get(key)

        if stats is None or stats["avg_return"] <= min_avg_return:
            continue

        annotated = dict(row)
        annotated["Win Rate %"] = stats["win_rate"]
        annotated["Avg Return %"] = stats["avg_return"]
        annotated["Backtest N"] = stats["n"]
        ranked.append(annotated)

    ranked.sort(key=lambda r: r["Avg Return %"], reverse=True)

    return ranked[:top_n]
