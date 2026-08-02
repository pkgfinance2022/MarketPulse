"""
Historical signal backtester.

Every engine's own describe()/analyse() only ever looks at the LATEST
bar of its trace - useful for "what's happening right now," useless
for "how has this actually performed." This module walks the FULL
trace instead, and for every signal that fired within a recent window,
simulates what actually happened afterward: did price hit the target
first, the stop first, or is the trade still open. It always reuses
each engine's own stop/target formula exactly (_buy_levels/_sell_levels,
or RSIWaveStatusService._stop_target) - never invents a risk model of
its own.

Whenever a single bar's High/Low would satisfy both the stop AND the
target (a gap, or a wide whipsaw candle), the stop is assumed to have
been hit first - the standard conservative convention when you only
have OHLC bars, not tick data, and can't know the true intrabar
sequence.

Weekly confluence (Multi-try breakout / Path C) is a different shape
entirely - it's LONG-only and has no stop/target anywhere in the app
(it's a confluence note, not an independently tradeable setup), so
backtest_weekly() reports a fixed-horizon forward return instead of a
fabricated target/stop outcome.
"""

import pandas as pd
import ta

from analysis.divergence_reclaim_strategy import DivergenceReclaimStrategy
from analysis.ema_reclaim_strategy import DailyEMAReclaimStrategy, EMAReclaimStrategy
from analysis.reversal_playbook import ReversalPlaybook
from analysis.reversal_playbook_daily import DailyWeeklyReversalPlaybook
from analysis.rsi_divergence_strategy import Crypto5mRSIDivergenceStrategy, Crypto15mRSIDivergenceStrategy, DailyRSIDivergenceStrategy, DailyWideRSIDivergenceStrategy, RSIDivergenceStrategy, StockRSIDivergenceStrategy, WeeklyStockRSIDivergenceStrategy, WideRSIDivergenceStrategy
from analysis.rsi_wave_strategy import RSIWaveStrategy
from dashboard.services.rsi_wave_status import RSIWaveStatusService
from dashboard.services.time_utils import now_cet


def _cutoff(window_days):
    return now_cet().replace(tzinfo=None) - pd.Timedelta(days=window_days)


def _naive(ts):
    return ts.tz_localize(None) if ts is not None and ts.tzinfo is not None else ts


def simulate_outcome(high, low, close, entry_pos, direction, entry_price, stop, target):
    """
    Walks forward from entry_pos+1 to the end of three aligned
    High/Low/Close Series. Returns None if stop/target aren't both
    known (nothing to simulate against), otherwise a dict: outcome
    ("TARGET"/"STOP"/"OPEN"), exit_time, exit_price, return_pct,
    bars_held.
    """

    if stop is None or target is None or pd.isna(stop) or pd.isna(target):
        return None

    n = len(close)

    for pos in range(entry_pos + 1, n):

        h = high.iloc[pos]
        l = low.iloc[pos]

        if direction == "LONG":
            hit_target = h >= target
            hit_stop = l <= stop
        else:
            hit_target = l <= target
            hit_stop = h >= stop

        # Stop checked first on an ambiguous same-bar hit - see module
        # docstring.
        if hit_stop:
            return_pct = (
                (stop / entry_price - 1) * 100
                if direction == "LONG"
                else (entry_price / stop - 1) * 100
            )
            return {
                "outcome": "STOP",
                "exit_time": close.index[pos],
                "exit_price": stop,
                "return_pct": round(return_pct, 2),
                "bars_held": pos - entry_pos,
            }

        if hit_target:
            return_pct = (
                (target / entry_price - 1) * 100
                if direction == "LONG"
                else (entry_price / target - 1) * 100
            )
            return {
                "outcome": "TARGET",
                "exit_time": close.index[pos],
                "exit_price": target,
                "return_pct": round(return_pct, 2),
                "bars_held": pos - entry_pos,
            }

    # Still open at the end of available data - mark-to-market against
    # the last close.
    last_price = float(close.iloc[-1])
    return_pct = (
        (last_price / entry_price - 1) * 100
        if direction == "LONG"
        else (entry_price / last_price - 1) * 100
    )

    return {
        "outcome": "OPEN",
        "exit_time": close.index[-1],
        "exit_price": round(last_price, 4),
        "return_pct": round(return_pct, 2),
        "bars_held": n - 1 - entry_pos,
    }


def summarize_trades(trades):

    if not trades:
        return {"total": 0, "hit_target": 0, "hit_stop": 0, "open": 0, "win_rate": 0.0, "avg_return": 0.0}

    hit_target = sum(1 for t in trades if t["outcome"] == "TARGET")
    hit_stop = sum(1 for t in trades if t["outcome"] == "STOP")
    still_open = sum(1 for t in trades if t["outcome"] == "OPEN")
    closed = hit_target + hit_stop

    return {
        "total": len(trades),
        "hit_target": hit_target,
        "hit_stop": hit_stop,
        "open": still_open,
        "win_rate": round(hit_target / closed * 100, 1) if closed else 0.0,
        "avg_return": round(sum(t["return_pct"] for t in trades) / len(trades), 2),
    }


RSI_WAVE_ENTRY_EVENTS = ("ENTRY_LONG_DIRECT", "ENTRY_LONG_RETEST", "ENTRY_SHORT_DIRECT", "ENTRY_SHORT_RETEST")


def backtest_rsi_wave(ticker, window_days, period="730d"):
    """RSI Wave Setup (1H) - every real entry (direct or retest) in the window."""

    trace, df = RSIWaveStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_WAVE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        kind = "Retest (2nd touch)" if "RETEST" in bar["event"] else "Direct"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "RSI Wave", "type": kind,
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def _simulate_moving_target(high, low, close, entry_pos, entry_price, stop, target_series):
    """
    Same semantics as simulate_outcome() (stop-first on an ambiguous
    same-bar hit, walks to the end of data rather than a fixed
    window), but for EMA Reclaim's target - the EMA200 level, which
    moves bar to bar rather than being fixed at entry. This is what
    the live app actually shows (a moving target line), and what the
    original validation backtest checked before this was built - using
    a scalar target here would silently disagree with both.
    """

    n = len(close)

    for pos in range(entry_pos + 1, n):

        h = high.iloc[pos]
        l = low.iloc[pos]
        target = float(target_series.iloc[pos])

        hit_target = h >= target
        hit_stop = l <= stop

        if hit_stop:
            return_pct = round((stop / entry_price - 1) * 100, 2)
            return {"outcome": "STOP", "exit_time": close.index[pos], "exit_price": stop, "return_pct": return_pct, "bars_held": pos - entry_pos}

        if hit_target:
            return_pct = round((target / entry_price - 1) * 100, 2)
            return {"outcome": "TARGET", "exit_time": close.index[pos], "exit_price": target, "return_pct": return_pct, "bars_held": pos - entry_pos}

    last_price = float(close.iloc[-1])
    return_pct = round((last_price / entry_price - 1) * 100, 2)

    return {"outcome": "OPEN", "exit_time": close.index[-1], "exit_price": round(last_price, 4), "return_pct": return_pct, "bars_held": n - 1 - entry_pos}


def backtest_ema_reclaim(ticker, window_days, period="730d"):
    """EMA20 Reclaim (1H) - every confirmed reclaim entry in the window."""

    trace, df = EMAReclaimStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]
    ema200 = ta.trend.ema_indicator(close, window=200)

    trades = []

    for bar in trace:

        if bar["event"] != "ENTRY_LONG" or _naive(bar["time"]) < cutoff:
            continue

        idx = bar["index"]
        price = bar["price"]
        stop = bar["wave_low"]

        if stop is None or stop >= price:
            continue

        outcome = _simulate_moving_target(high, low, close, idx, price, stop, ema200)

        trades.append({
            "time": bar["time"], "engine": "EMA20 Reclaim", "type": "Confirmed reclaim",
            "direction": "LONG", "entry": price, "stop": round(stop, 4), "target": round(float(ema200.iloc[idx]), 4),
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_divergence_reclaim(ticker, window_days, period="730d"):
    """
    Divergence Reclaim (1H) - a new, separate engine (not EMA Reclaim,
    not RSI Divergence - see analysis/divergence_reclaim_strategy.py).
    Every confirmed entry in the window, riding a moving EMA200 target
    exactly like backtest_ema_reclaim - same reasoning: this is what
    the live app actually shows and what the user described trading.
    """

    trace, df = DivergenceReclaimStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]
    ema200 = ta.trend.ema_indicator(close, window=200)

    trades = []

    for bar in trace:

        if bar["event"] != "ENTRY_LONG" or _naive(bar["time"]) < cutoff:
            continue

        idx = bar["index"]
        price = bar["price"]
        stop = bar["wave_low"]

        if stop is None or stop >= price:
            continue

        outcome = _simulate_moving_target(high, low, close, idx, price, stop, ema200)

        trades.append({
            "time": bar["time"], "engine": "Divergence Reclaim", "type": "Confirmed reclaim",
            "direction": "LONG", "entry": price, "stop": round(stop, 4), "target": round(float(ema200.iloc[idx]), 4),
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_daily_ema_reclaim(ticker, window_days, period="730d"):
    """EMA20 Reclaim (Daily) - same as backtest_ema_reclaim but using DailyEMAReclaimStrategy on Daily bars."""

    trace, df = DailyEMAReclaimStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]
    ema200 = ta.trend.ema_indicator(close, window=200)

    trades = []

    for bar in trace:

        if bar["event"] != "ENTRY_LONG" or _naive(bar["time"]) < cutoff:
            continue

        idx = bar["index"]
        price = bar["price"]
        stop = bar["wave_low"]

        if stop is None or stop >= price:
            continue

        outcome = _simulate_moving_target(high, low, close, idx, price, stop, ema200)

        trades.append({
            "time": bar["time"], "engine": "EMA20 Reclaim (Daily)", "type": "Confirmed reclaim",
            "direction": "LONG", "entry": price, "stop": round(stop, 4), "target": round(float(ema200.iloc[idx]), 4),
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


RSI_DIVERGENCE_ENTRY_EVENTS = ("ENTRY_LONG_DIVERGENCE", "ENTRY_SHORT_DIVERGENCE")


def backtest_rsi_divergence(ticker, window_days, period="730d"):
    """
    RSI Divergence (1H) - every confirmed regular-divergence entry in
    the window (see RSIDivergenceStrategy's own docstring for the exact
    pattern). Reuses RSI Wave's exact stop/target formula
    (RSIWaveStatusService._stop_target) - same risk model, a different
    entry condition being tested.
    """

    trace, df = RSIDivergenceStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_DIVERGENCE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "RSI Divergence", "type": "RSI Divergence",
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_daily_rsi_divergence(ticker, window_days, period="730d"):
    """
    Same regular-divergence pattern as backtest_rsi_divergence, Daily
    bars instead of Hourly (see DailyRSIDivergenceStrategy) - explicit
    follow-up request after the user showed the same price-down/RSI-up
    shape on a Daily BTC-USD chart.
    """

    trace, df = DailyRSIDivergenceStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_DIVERGENCE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "Daily RSI Divergence", "type": "RSI Divergence",
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def _backtest_generic_rsi_divergence(strategy, engine_label, ticker, window_days, period="730d"):
    """
    Shared body for backtest_wide_rsi_divergence/backtest_daily_wide_rsi_divergence
    - same fixed-R:R stop/target formula as every other RSI Divergence
    variant (RSIWaveStatusService._stop_target).
    """

    trace, df = strategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_DIVERGENCE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": engine_label, "type": "RSI Divergence",
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_wide_rsi_divergence(ticker, window_days, period="730d"):
    """1H macro, loosened thresholds - see WideRSIDivergenceStrategy."""
    return _backtest_generic_rsi_divergence(WideRSIDivergenceStrategy, "Wide RSI Divergence", ticker, window_days, period=period)


def backtest_daily_wide_rsi_divergence(ticker, window_days, period="730d"):
    """Daily macro, loosened thresholds - see DailyWideRSIDivergenceStrategy."""
    return _backtest_generic_rsi_divergence(DailyWideRSIDivergenceStrategy, "Daily Wide RSI Divergence", ticker, window_days, period=period)


def backtest_crypto_5m_divergence(ticker, window_days=60, period="60d"):
    """
    5-minute, crypto only (BTC-USD/ETH-USD) - see
    Crypto5mRSIDivergenceStrategy. period="60d" is yfinance's own hard
    limit for this interval, not a design choice - window_days defaults
    to match it since there's no more history to filter against anyway.
    """
    return _backtest_generic_rsi_divergence(Crypto5mRSIDivergenceStrategy, "5m Crypto Divergence", ticker, window_days, period=period)


def backtest_crypto_15m_divergence(ticker, window_days=60, period="60d"):
    """15-minute, crypto only - see Crypto15mRSIDivergenceStrategy. Same 60d hard limit as the 5m variant."""
    return _backtest_generic_rsi_divergence(Crypto15mRSIDivergenceStrategy, "15m Crypto Divergence", ticker, window_days, period=period)


def backtest_weekly_stock_divergence(ticker, window_days, period="10y"):
    """
    Weekly RSI Divergence, recalibrated for individual stocks (see
    WeeklyStockRSIDivergenceStrategy - looser 40/60 base zone, 5% price
    tolerance on the second leg, verified on a real MSFT example before
    this backtest). Same stop/target formula as backtest_rsi_divergence
    (RSIWaveStatusService._stop_target), same risk model, just applied
    to weekly ATR/support-resistance instead of hourly.
    """

    trace, df = WeeklyStockRSIDivergenceStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_DIVERGENCE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "Weekly Stock Divergence", "type": "RSI Divergence",
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_stock_divergence(ticker, window_days, period="730d"):
    """
    Daily RSI Divergence, recalibrated for individual stocks (see
    StockRSIDivergenceStrategy) - same recalibration as the Weekly
    variant (looser 40/60 base zone, 5% price tolerance), Daily bars
    instead of Weekly. Real caveat: even recalibrated, this missed the
    original MSFT example that motivated the whole recalibration (Daily
    bars are noisier - price had already run too far from the base by
    the time RSI carved out a clean second-leg low) - shipped anyway at
    the user's request, honest backtest number attached.
    """

    trace, df = StockRSIDivergenceStrategy.run_symbol(ticker, period=period)

    if not trace:
        return None

    cutoff = _cutoff(window_days)
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = ta.volatility.average_true_range(high, low, close, window=RSIWaveStatusService.ATR_WINDOW)
    support = low.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).min()
    resistance = high.rolling(RSIWaveStatusService.SUPPORT_RESISTANCE_WINDOW).max()

    trades = []

    for bar in trace:

        if bar["event"] not in RSI_DIVERGENCE_ENTRY_EVENTS or _naive(bar["time"]) < cutoff:
            continue

        direction = "LONG" if "LONG" in bar["event"] else "SHORT"
        idx = bar["index"]
        price = bar["price"]

        atr_val = float(atr.iloc[idx]) if pd.notna(atr.iloc[idx]) else 0.0
        support_val = float(support.iloc[idx]) if pd.notna(support.iloc[idx]) else price
        resistance_val = float(resistance.iloc[idx]) if pd.notna(resistance.iloc[idx]) else price

        levels = RSIWaveStatusService._stop_target(direction, price, support_val, resistance_val, atr_val)
        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "Daily Stock Divergence", "type": "RSI Divergence",
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


REVERSAL_BUY_EVENTS = ("BUY_SIGNAL_PATH_A", "BUY_SIGNAL_PATH_B", "BUY_SIGNAL_PATH_C", "BUY_SIGNAL_PATH_D")
REVERSAL_SELL_EVENTS = ("SELL_TRIGGER_BREAKDOWN", "SELL_TRIGGER_REJECTION", "SELL_SIGNAL_CONTINUATION")

REVERSAL_TYPE_LABELS = {
    "BUY_SIGNAL_PATH_A": "Path A",
    "BUY_SIGNAL_PATH_B": "Path B",
    "BUY_SIGNAL_PATH_C": "Path C (2nd touch)",
    "BUY_SIGNAL_PATH_D": "Path D (counter-trend)",
    "SELL_TRIGGER_BREAKDOWN": "Breakdown",
    "SELL_TRIGGER_REJECTION": "Rejection",
    "SELL_SIGNAL_CONTINUATION": "Continuation (2nd break)",
}


def backtest_reversal_playbook(ticker, window_days):
    """Reversal Playbook (1H) - every BUY/SELL signal in the window."""

    result = ReversalPlaybook.run_symbol(ticker)

    if result is None:
        return None

    trace = result["trace"]
    ind_1h = result["ind_1h"]
    high, low, close = ind_1h["high"], ind_1h["low"], ind_1h["close"]

    cutoff = _cutoff(window_days)
    trades = []

    for bar in trace:

        event = bar["event"]

        if event not in REVERSAL_BUY_EVENTS and event not in REVERSAL_SELL_EVENTS:
            continue

        if _naive(bar["time"]) < cutoff:
            continue

        try:
            idx = close.index.get_loc(bar["time"])
        except KeyError:
            continue

        price = bar["price"]

        if event in REVERSAL_BUY_EVENTS:
            direction = "LONG"
            wave_start = bar["wave_start_time_d"] if event == "BUY_SIGNAL_PATH_D" else bar["wave_start_time"]
            levels = ReversalPlaybook._buy_levels(ind_1h, wave_start, price)
        else:
            direction = "SHORT"
            levels = ReversalPlaybook._sell_levels(bar["swing_high"], price)

        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "Reversal Playbook", "type": REVERSAL_TYPE_LABELS.get(event, event),
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


DAILY_BUY_EVENTS = ("BUY_SIGNAL_PATH_A", "BUY_SIGNAL_PATH_B", "BUY_SIGNAL_PATH_C")
DAILY_SELL_EVENTS = ("SELL_TRIGGER_BREAKDOWN", "SELL_TRIGGER_REJECTION", "SELL_SIGNAL_CONTINUATION")

DAILY_TYPE_LABELS = {
    "BUY_SIGNAL_PATH_A": "Path A",
    "BUY_SIGNAL_PATH_B": "Path B",
    "BUY_SIGNAL_PATH_C": "Path C (2nd touch)",
    "SELL_TRIGGER_BREAKDOWN": "Breakdown",
    "SELL_TRIGGER_REJECTION": "Rejection",
    "SELL_SIGNAL_CONTINUATION": "Continuation (2nd break)",
}

WEEKLY_EVENT_LABELS = {
    "WEEKLY_MULTI_TRY_BREAKOUT": "Multi-try breakout",
    "WEEKLY_PATH_C_BREAKOUT": "Path C confirmed",
}

WEEKLY_FORWARD_DAYS = 20   # ~4 trading weeks


def backtest_daily(ticker, window_days):
    """Reversal Playbook (Daily) - every BUY/SELL signal in the window."""

    result = DailyWeeklyReversalPlaybook.run_symbol(ticker)

    if result is None:
        return None

    trace = result["trace"]
    ind_daily = result["ind_daily"]
    high, low, close = ind_daily["high"], ind_daily["low"], ind_daily["close"]

    cutoff = _cutoff(window_days)
    trades = []

    for bar in trace:

        event = bar["event"]

        if event not in DAILY_BUY_EVENTS and event not in DAILY_SELL_EVENTS:
            continue

        if _naive(bar["time"]) < cutoff:
            continue

        try:
            idx = close.index.get_loc(bar["time"])
        except KeyError:
            continue

        price = bar["price"]

        if event in DAILY_BUY_EVENTS:
            direction = "LONG"
            levels = DailyWeeklyReversalPlaybook._buy_levels(ind_daily, bar["wave_start_time"], price)
        else:
            direction = "SHORT"
            levels = DailyWeeklyReversalPlaybook._sell_levels(bar["swing_high"], price)

        outcome = simulate_outcome(high, low, close, idx, direction, price, levels["stop"], levels["target1"])

        if outcome is None:
            continue

        trades.append({
            "time": bar["time"], "engine": "Reversal Playbook (Daily)", "type": DAILY_TYPE_LABELS.get(event, event),
            "direction": direction, "entry": price, "stop": levels["stop"], "target": levels["target1"],
            **outcome,
        })

    return {"trades": trades, "summary": summarize_trades(trades)}


def backtest_weekly(ticker, window_days):
    """
    Weekly confluence has no stop/target anywhere in the app (it's a
    LONG-only confluence note, not an independently tradeable setup) -
    reports a fixed ~4-week forward return per signal instead.
    """

    result = DailyWeeklyReversalPlaybook.run_symbol(ticker)

    if result is None:
        return None

    trace = result["trace"]
    cutoff = _cutoff(window_days)

    signals = []

    for i, bar in enumerate(trace):

        for event_field in ("weekly_event", "weekly_path_c_event"):

            event = bar.get(event_field)

            if not event or _naive(bar["time"]) < cutoff:
                continue

            entry_price = bar["price"]
            future_idx = i + WEEKLY_FORWARD_DAYS

            if future_idx < len(trace):
                future_price = trace[future_idx]["price"]
                future_time = trace[future_idx]["time"]
                status = "Closed"
            else:
                future_price = trace[-1]["price"]
                future_time = trace[-1]["time"]
                status = "Not enough time elapsed yet"

            return_pct = round((future_price / entry_price - 1) * 100, 2)

            signals.append({
                "time": bar["time"], "type": WEEKLY_EVENT_LABELS[event], "entry": entry_price,
                "as_of": future_time, "return_pct": return_pct, "status": status,
            })

    closed = [s for s in signals if s["status"] == "Closed"]

    return {
        "signals": signals,
        "summary": {
            "total": len(signals),
            "avg_return": round(sum(s["return_pct"] for s in closed) / len(closed), 2) if closed else 0.0,
        },
    }
