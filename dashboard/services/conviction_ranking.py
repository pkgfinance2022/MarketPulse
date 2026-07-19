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
