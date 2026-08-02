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

Static numbers, not live-recalculated - refreshing them for real means
re-running analysis/backtester.py against current data, a separate,
heavier operation from just reading what's already actionable right
now - worth doing periodically, not on every page load.

RE-BACKTESTED (28 Global Indices instruments, 365 days) after RSI
length changed from 14 to 28 everywhere in the app (explicit
instruction) - every engine here depends on RSI in some way, so the
old RSI(14) numbers would have been silently wrong the moment the
underlying indicator changed. Real, mixed result: Setup barely moved,
RSI Divergence improved slightly, but Reversal/Daily Reversal/both EMA
Reclaim variants all got WORSE, several flipping from a real positive
edge to flat-or-negative. Only RSI Divergence and the two Chart
Patterns (RSI-independent, unaffected by this change) still clear the
"Highest Conviction" bar (avg_return > 0) - Reversal/Daily Reversal/
EMA Reclaim/Daily EMA Reclaim no longer do, a real behavior change to
that list, not a rounding difference.
"""

WIN_RATE_LOOKUP = {
    "Setup": {"win_rate": 75.4, "avg_return": -0.02, "n": 566},
    "Reversal": {"win_rate": 43.0, "avg_return": -0.08, "n": 880},
    "Daily Reversal": {"win_rate": 25.0, "avg_return": -0.59, "n": 32},
    # Re-backtested (45 Global Indices/macro instruments incl. BTC/ETH,
    # 365 days) after a real logic fix: the second leg used to count as
    # "diverging" even if it only bottomed out around RSI 45 - nowhere
    # near oversold, i.e. divergence forming in the middle of the range
    # (explicit user correction: "diversion in middle has less value").
    # Added SECOND_LEG_OVERSOLD(30)/SECOND_LEG_OVERBOUGHT(60) so the
    # second leg has to actually retest the extreme zone. Filtered out
    # ~7 marginal trades (123 -> 116) with a slightly better avg return
    # (0.10% -> 0.14%), same win rate - a real quality improvement, no
    # regression to the already-live Telegram/Command Center engine.
    "RSI Divergence": {"win_rate": 32.8, "avg_return": 0.14, "n": 116},
    "Chart Patterns:Piercing Pattern": {"win_rate": 54.8, "avg_return": 0.76, "n": 32},
    "Chart Patterns:Double Bottom": {"win_rate": 77.0, "avg_return": 0.76, "n": 92},
    # RSI(28), same divergence gate as before (MIN_DOWNTREND_DIVERGENCE_PCT
    # = 2.0, untouched by the RSI length switch) - win rate held up
    # (47.7% -> 49.8%) but avg return flipped from a real positive edge
    # (+0.09%) to essentially breakeven (-0.0%).
    "EMA Reclaim": {"win_rate": 49.8, "avg_return": -0.0, "n": 438},
    # Daily variant - already flagged as a thin, noisy sample before
    # (n=42); RSI(28) didn't fix that (n=39 now) and made the average
    # return meaningfully worse (+0.45% -> -0.79%). Treat Daily EMA
    # Reclaim signals with real skepticism until more data accumulates.
    "Daily EMA Reclaim": {"win_rate": 43.2, "avg_return": -0.79, "n": 39},
    # BETA - deliberately excluded from Command Center/Highest
    # Conviction (avg_return < 0), shown only on the Overview page's
    # own Beta tab with its own disclaimer. See
    # analysis/divergence_reclaim_strategy.py.
    "Divergence Reclaim": {"win_rate": 25.0, "avg_return": -0.84, "n": 24},
    # BETA - Weekly, recalibrated specifically for individual stocks
    # (looser 40/60 RSI zone, 5% price tolerance on the second leg -
    # see StockRSIDivergenceStrategy/WeeklyStockRSIDivergenceStrategy).
    # Real anecdotes (MSFT, ASML, Cambricon) look great individually,
    # but the full backtest is net negative in BOTH markets - US (44.0%
    # win rate, -2.91% avg return, n=53) and India (48.4%, -0.85%,
    # n=99) - combined below. Shipped anyway as beta, alerts only.
    "Weekly Stock Divergence": {"win_rate": 46.9, "avg_return": -1.57, "n": 152},
    # BETA - Daily sibling, same recalibrated thresholds. Real surprise:
    # even though Daily never caught the original MSFT example that
    # motivated this whole recalibration, the full backtest is
    # genuinely POSITIVE in both markets - US (60.7% win rate, +0.92%
    # avg return, n=215) and India (58.4%, +0.20%, n=468) - combined
    # below. Still shown as beta (brand new, backtested only, not yet
    # observed live) rather than folded into Highest Conviction.
    "Daily Stock Divergence": {"win_rate": 59.1, "avg_return": 0.43, "n": 683},
    # BETA - macro thresholds loosened from 25/75 to 30/70 (base) and
    # 30/60 to 35/65 (second leg) after real misses (Silver's RSI
    # topped at 69, BTC-USD's bottomed at 25.68 - both just missed the
    # strict thresholds). 1H: more trades than strict (194 vs 116) but
    # avg return roughly flat (+0.02% vs +0.14%) - a wash, not a clear
    # win. Daily stays thin and negative even loosened (n=13, -3.0%).
    # Not swapped in for the live strict engine - shown here as its own
    # separate beta comparison point.
    "Wide RSI Divergence": {"win_rate": 43.8, "avg_return": 0.02, "n": 194},
    "Daily Wide RSI Divergence": {"win_rate": 8.3, "avg_return": -3.0, "n": 13},
    # BETA - BTC-USD/ETH-USD only, ultra-short timeframes (explicit
    # request after a real ETH 1m divergence example). Both backtests
    # are honest but genuinely thin - yfinance only keeps 60 days of
    # history at these intervals, nowhere near the 365-day/5-year
    # windows used elsewhere this session.
    "5m Crypto Divergence": {"win_rate": 46.7, "avg_return": 0.09, "n": 45},
    "15m Crypto Divergence": {"win_rate": 26.7, "avg_return": 0.08, "n": 15},
    # 1m has NO entry here deliberately - it cannot be backtested at
    # all (yfinance keeps only 7-8 days of 1-minute history), so there
    # is no honest number to report. Live observation only.
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
