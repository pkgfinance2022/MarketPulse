"""
Reversal playbook - Daily+Weekly variant.

Same rules as analysis/reversal_playbook.py (the 1H+Daily engine), one
timeframe up: runs on DAILY bars, with the WEEKLY 200 EMA as the
higher-timeframe context filter. Built for symbols you trade
positionally/swing rather than intraday - explicitly requested for the
Indian Stocks tab, where the user does not trade individual stocks
intraday and wants a Daily-cadence read instead of (or alongside) the
existing 1H engine.

This is a SEPARATE, ADDITIVE engine - it does not replace or modify
analysis/reversal_playbook.py, which keeps running exactly as before
for Global Indices, US Stocks, and Crypto.

BUY SIDE:
    Pre-filter: price above the Weekly 200 EMA.
    Step 1: Daily RSI touches <=22 -> start watching.
    Step 2: Daily RSI crosses up through 65 -> ALERT ONLY, not a buy yet.
    Step 3 (either path fires the actual entry):
        Path A: Daily EMA20 and EMA200 are meaningfully far apart.
        Path B: Daily price crossed up through EMA200 recently AND is
                now holding/retesting it as support.
        Path C: Daily RSI holds 65 as support after the initial cross,
                then resumes above 65 while price has recently
                reclaimed the Daily 20 or 200 EMA.
    SL = low of the wave since the Step 1 touch. Target = TARGET_PCT
    (placeholder - wider than the 1H engine's, since Daily swings are
    naturally bigger; still explicitly left for the user to tune).

SELL SIDE - three ideas, same shape as the 1H engine:
    Breakdown: Daily RSI crosses below 40 with a fresh swing low.
    Rejection: Daily RSI rallies toward 60 and is rejected there while
               price is at/below the Daily EMA200.
    Continuation: after a Breakdown, RSI takes a "slight support"
                  bounce (stays below 50) then breaks down again -
                  flagged extra strongly if it follows a Rejection.
    Guardrail: suppressed near the Weekly 200 EMA (likely support).

Weekly confluence (independent, on top of the Daily machine):
    Multi-try breakout: Weekly RSI fails 2+ times in the 55-65 zone
    before finally breaking above 65 - a stronger, longer move.
    Weekly Path C: same "hold 65 as support + reclaim the 200 EMA"
    idea as the Daily engine's Path C, one timeframe up.

Every specific threshold is a first-pass reading carried over from the
1H+Daily engine, explicitly flagged as tunable - not yet independently
validated at this cadence beyond the backtest sanity-checks run before
wiring this in.
"""

import pandas as pd
import ta

from providers.yahoo import YahooProvider


class DailyWeeklyReversalPlaybook:

    MIN_HISTORY_DAILY = 220

    # --- Buy side ---
    BUY_OVERSOLD_TOUCH = 22
    BUY_CONFIRM_LEVEL = 65
    FAR_THRESHOLD_PCT = 2.0
    RETEST_BAND_PCT = 0.5
    CROSS_LOOKBACK_BARS = 15       # days - how far back to look for a fresh EMA200 cross-up
    RSI_RETEST_FLOOR = 60

    # --- Sell side ---
    SELL_BREAKDOWN_LEVEL = 40
    SELL_REJECTION_LEVEL = 60
    SWING_LOOKBACK_BARS = 10       # days
    RESISTANCE_BAND_PCT = 0.5
    SELL_BOUNCE_CEILING = 50
    SELL_CONTINUATION_LOOKBACK_BARS = 30   # days

    # --- Shared ---
    WEEKLY_SUPPORT_BAND_PCT = 1.0
    TARGET_PCT = 4.0                # wider placeholder than the 1H engine's 1.25% - Daily swings are bigger. Still explicitly for the user to tune.
    WEEKLY_CONFLUENCE_RECENT_WEEKS = 3   # how long the weekly confluence note stays "fresh"

    REJECTION_PEAK_LOOKBACK = 8   # days

    @staticmethod
    def _prepare_daily(df):

        close, high, low = df["Close"], df["High"], df["Low"]

        # RSI on OHLC4 (typical price), not raw Close - see
        # analysis/reversal_playbook.py's _prepare_1h for the real-data
        # case that motivated this (a threshold touch missed by Close
        # alone, caught by OHLC4). EMA/price levels still use Close.
        typical_price = (df["Open"] + high + low + close) / 4
        rsi = ta.momentum.rsi(typical_price, window=14)

        return {
            "close": close,
            "high": high,
            "low": low,
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": rsi,
            "rsi_peak": rsi.rolling(DailyWeeklyReversalPlaybook.REJECTION_PEAK_LOOKBACK).max(),
            "swing_low": low.rolling(DailyWeeklyReversalPlaybook.SWING_LOOKBACK_BARS).min(),
            "swing_high": high.rolling(DailyWeeklyReversalPlaybook.SWING_LOOKBACK_BARS).max(),
        }

    # --- Weekly confluence (independent of the Daily buy/sell machine) ---
    WEEKLY_ZONE_LOW = 55
    WEEKLY_ZONE_HIGH = 65
    WEEKLY_RESET_FLOOR = 45
    WEEKLY_MIN_ATTEMPTS = 2

    @classmethod
    def _weekly_multi_try_breakout(cls, rsi):
        """
        For each week, tracks how many distinct prior "attempts" (a
        rally that reached into [WEEKLY_ZONE_LOW, WEEKLY_ZONE_HIGH)
        then retreated back below WEEKLY_ZONE_LOW without ever
        breaking out) happened since the last reset (RSI dropping
        below WEEKLY_RESET_FLOOR), and flags the week RSI finally
        crosses above WEEKLY_ZONE_HIGH as a "multi-try breakout" only
        if at least WEEKLY_MIN_ATTEMPTS occurred first. Sequential/
        stateful, same reasoning as the Daily engine's version.
        """

        attempts = 0
        in_attempt = False
        prev = None
        flags = []

        for value in rsi:

            flag = False

            if pd.isna(value):
                flags.append(False)
                prev = value
                continue

            if prev is not None:

                if value < cls.WEEKLY_RESET_FLOOR:
                    attempts = 0
                    in_attempt = False

                if cls.WEEKLY_ZONE_LOW <= value < cls.WEEKLY_ZONE_HIGH and not in_attempt:
                    in_attempt = True

                if in_attempt and value < cls.WEEKLY_ZONE_LOW:
                    attempts += 1
                    in_attempt = False

                if value >= cls.WEEKLY_ZONE_HIGH and prev < cls.WEEKLY_ZONE_HIGH:
                    flag = attempts >= cls.WEEKLY_MIN_ATTEMPTS
                    attempts = 0
                    in_attempt = False

            flags.append(flag)
            prev = value

        return pd.Series(flags, index=rsi.index)

    WEEKLY_CROSS_LOOKBACK_WEEKS = 4

    @classmethod
    def _weekly_support_reclaim(cls, rsi, close, ema200):
        """
        Weekly analog of Daily Path C: once Weekly RSI has crossed
        above 65, watch for it to pull back into the 60-65 band and
        then resume above 65, at the same time price has recently
        reclaimed the Weekly 200 EMA. Returns (forming, confirmed)
        boolean Series - same shape as the Daily engine's version.
        """

        phase = "NONE"
        retest_armed = False
        weeks_since_ema_cross = None
        prev_rsi = None
        prev_above_ema200 = None

        forming_flags = []
        confirmed_flags = []

        for r, c, e in zip(rsi, close, ema200):

            forming = False
            confirmed = False

            if pd.isna(r) or pd.isna(e):
                forming_flags.append(False)
                confirmed_flags.append(False)
                prev_rsi = r
                continue

            above_ema200 = c > e

            if prev_above_ema200 is None:
                prev_above_ema200 = above_ema200

            if above_ema200 and not prev_above_ema200:
                weeks_since_ema_cross = 0
            elif weeks_since_ema_cross is not None:
                weeks_since_ema_cross += 1

            if weeks_since_ema_cross is not None and weeks_since_ema_cross > cls.WEEKLY_CROSS_LOOKBACK_WEEKS:
                weeks_since_ema_cross = None

            if prev_rsi is not None:

                if r < cls.WEEKLY_RESET_FLOOR:
                    phase = "NONE"
                    retest_armed = False

                if phase == "NONE" and r >= cls.BUY_CONFIRM_LEVEL and prev_rsi < cls.BUY_CONFIRM_LEVEL:
                    phase = "ARMED"

                elif phase == "ARMED":

                    if r < cls.BUY_CONFIRM_LEVEL and r >= cls.RSI_RETEST_FLOOR:
                        retest_armed = True
                    elif r < cls.RSI_RETEST_FLOOR:
                        retest_armed = False

                    forming = retest_armed and r < cls.BUY_CONFIRM_LEVEL and above_ema200

                    if (
                        retest_armed
                        and r >= cls.BUY_CONFIRM_LEVEL
                        and prev_rsi < cls.BUY_CONFIRM_LEVEL
                        and weeks_since_ema_cross is not None
                    ):
                        confirmed = True
                        phase = "NONE"
                        retest_armed = False

            forming_flags.append(forming)
            confirmed_flags.append(confirmed)

            prev_rsi = r
            prev_above_ema200 = above_ema200

        return (
            pd.Series(forming_flags, index=rsi.index),
            pd.Series(confirmed_flags, index=rsi.index),
        )

    @classmethod
    def _prepare_weekly(cls, df):

        close = df["Close"]
        typical_price = (df["Open"] + df["High"] + df["Low"] + close) / 4
        rsi = ta.momentum.rsi(typical_price, window=14)
        ema200 = ta.trend.ema_indicator(close, window=200)

        path_c_forming, path_c_confirmed = cls._weekly_support_reclaim(rsi, close, ema200)

        return {
            "close": close,
            "ema200": ema200,
            "rsi": rsi,
            "multi_try_breakout": cls._weekly_multi_try_breakout(rsi),
            "path_c_forming": path_c_forming,
            "path_c_confirmed": path_c_confirmed,
        }

    @classmethod
    def _combined_timeline(cls, ind_daily, ind_weekly):
        """
        One row per Daily bar, with the Weekly 200 EMA forward-filled
        onto it (merge_asof, direction="backward") - never looks at a
        weekly bar that hasn't actually closed yet.
        """

        d_index = ind_daily["close"].index
        d_index_naive = d_index.tz_localize(None) if d_index.tz is not None else d_index

        daily = pd.DataFrame(
            {
                "d_close": ind_daily["close"].values,
                "d_high": ind_daily["high"].values,
                "d_low": ind_daily["low"].values,
                "d_ema20": ind_daily["ema20"].values,
                "d_ema200": ind_daily["ema200"].values,
                "d_rsi": ind_daily["rsi"].values,
                "d_rsi_peak": ind_daily["rsi_peak"].values,
                "d_swing_low": ind_daily["swing_low"].values,
                "d_swing_high": ind_daily["swing_high"].values,
            },
            index=d_index_naive,
        ).sort_index()

        w_index = ind_weekly["close"].index
        w_index_naive = w_index.tz_localize(None) if w_index.tz is not None else w_index

        weekly = pd.DataFrame(
            {
                "weekly_close": ind_weekly["close"].values,
                "weekly_ema200": ind_weekly["ema200"].values,
                "weekly_rsi": ind_weekly["rsi"].values,
                "weekly_multi_try_breakout": ind_weekly["multi_try_breakout"].values,
                "weekly_path_c_forming": ind_weekly["path_c_forming"].values,
                "weekly_path_c_confirmed": ind_weekly["path_c_confirmed"].values,
            },
            index=w_index_naive,
        ).sort_index()

        combined = pd.merge_asof(
            daily,
            weekly,
            left_index=True,
            right_index=True,
            direction="backward",
        )

        combined.index = d_index

        return combined.dropna(subset=["d_rsi", "weekly_ema200"])

    # ------------------------------------------------------------------
    # Walk
    # ------------------------------------------------------------------

    @classmethod
    def walk(cls, combined):
        """
        Single pass over the Daily timeline. Identical state-machine
        shape to the 1H engine's walk() - see that file for the full
        reasoning behind each debounce/consumed flag; only the
        granularity of the underlying bars changed (Daily instead of
        1H, Weekly instead of Daily for the higher-timeframe filter).
        """

        phase = "NONE"
        wave_start_time = None

        bars_since_cross_up = None
        bars_since_cross_up_20 = None

        rsi_retest_armed = False

        last_sell_trigger_bars_ago = None
        rejection_consumed = False
        breakdown_consumed = False

        sell_phase = "NONE"
        sell_retest_armed = False
        sell_wave_low = None

        bars_since_rejection_trigger = None

        trace = []

        prev_rsi = None
        prev_above_ema200 = None
        prev_above_ema20 = None
        prev_weekly_multi_try = None
        prev_weekly_path_c_confirmed = None

        for i in range(len(combined)):

            row = combined.iloc[i]
            timestamp = combined.index[i]

            price = row["d_close"]
            high = row["d_high"]
            low = row["d_low"]
            ema20 = row["d_ema20"]
            ema200 = row["d_ema200"]
            rsi = row["d_rsi"]
            rsi_peak = row["d_rsi_peak"]
            swing_low = row["d_swing_low"]
            swing_high = row["d_swing_high"]
            weekly_ema200 = row["weekly_ema200"]
            weekly_rsi = row["weekly_rsi"]
            weekly_multi_try = bool(row["weekly_multi_try_breakout"]) if pd.notna(row["weekly_multi_try_breakout"]) else False
            weekly_path_c_forming = bool(row["weekly_path_c_forming"]) if pd.notna(row["weekly_path_c_forming"]) else False
            weekly_path_c_confirmed = bool(row["weekly_path_c_confirmed"]) if pd.notna(row["weekly_path_c_confirmed"]) else False

            if prev_rsi is None:
                prev_rsi = rsi

            if prev_weekly_multi_try is None:
                prev_weekly_multi_try = weekly_multi_try

            if prev_weekly_path_c_confirmed is None:
                prev_weekly_path_c_confirmed = weekly_path_c_confirmed

            weekly_event = "WEEKLY_MULTI_TRY_BREAKOUT" if (weekly_multi_try and not prev_weekly_multi_try) else None
            weekly_path_c_event = "WEEKLY_PATH_C_BREAKOUT" if (weekly_path_c_confirmed and not prev_weekly_path_c_confirmed) else None
            prev_weekly_multi_try = weekly_multi_try
            prev_weekly_path_c_confirmed = weekly_path_c_confirmed

            above_ema200 = price > ema200 if pd.notna(ema200) else None
            above_ema20 = price > ema20 if pd.notna(ema20) else None

            if prev_above_ema200 is None:
                prev_above_ema200 = above_ema200

            if prev_above_ema20 is None:
                prev_above_ema20 = above_ema20

            if above_ema200 and not prev_above_ema200:
                bars_since_cross_up = 0
            elif bars_since_cross_up is not None:
                bars_since_cross_up += 1

            if bars_since_cross_up is not None and bars_since_cross_up > cls.CROSS_LOOKBACK_BARS:
                bars_since_cross_up = None

            if above_ema20 and not prev_above_ema20:
                bars_since_cross_up_20 = 0
            elif bars_since_cross_up_20 is not None:
                bars_since_cross_up_20 += 1

            if bars_since_cross_up_20 is not None and bars_since_cross_up_20 > cls.CROSS_LOOKBACK_BARS:
                bars_since_cross_up_20 = None

            if last_sell_trigger_bars_ago is not None:
                last_sell_trigger_bars_ago += 1

            event = None
            path_c_forming = False
            price_above_weekly = pd.notna(weekly_ema200) and price > weekly_ema200
            price_below_weekly = pd.notna(weekly_ema200) and price < weekly_ema200

            near_weekly_support = (
                pd.notna(weekly_ema200)
                and abs(price - weekly_ema200) / weekly_ema200 * 100 <= cls.WEEKLY_SUPPORT_BAND_PCT
            )

            # ---------------- BUY side (phase machine) ----------------

            if phase == "NONE" and price_above_weekly and rsi <= cls.BUY_OVERSOLD_TOUCH:
                phase = "BUY_ALERT_TOUCH"
                wave_start_time = timestamp
                event = "BUY_TOUCH"

            elif phase == "BUY_ALERT_TOUCH":

                if rsi >= cls.BUY_CONFIRM_LEVEL and prev_rsi < cls.BUY_CONFIRM_LEVEL:
                    phase = "BUY_ALERT_CONFIRM"
                    event = "BUY_ALERT"
                    rsi_retest_armed = False

            elif phase == "BUY_ALERT_CONFIRM":

                far_apart = (
                    pd.notna(ema20) and pd.notna(ema200) and ema200
                    and abs(ema20 - ema200) / ema200 * 100 >= cls.FAR_THRESHOLD_PCT
                )

                retesting_support = (
                    bars_since_cross_up is not None
                    and pd.notna(ema200) and ema200
                    and above_ema200
                    and abs(price - ema200) / ema200 * 100 <= cls.RETEST_BAND_PCT
                )

                if rsi < cls.BUY_CONFIRM_LEVEL and rsi >= cls.RSI_RETEST_FLOOR:
                    rsi_retest_armed = True
                elif rsi < cls.RSI_RETEST_FLOOR:
                    rsi_retest_armed = False

                ema_reclaimed = bars_since_cross_up is not None or bars_since_cross_up_20 is not None

                path_c = (
                    rsi_retest_armed
                    and rsi >= cls.BUY_CONFIRM_LEVEL
                    and prev_rsi < cls.BUY_CONFIRM_LEVEL
                    and ema_reclaimed
                )

                path_c_forming = (
                    rsi_retest_armed
                    and rsi < cls.BUY_CONFIRM_LEVEL
                    and (bool(above_ema20) or bool(above_ema200))
                )

                if far_apart:
                    event = "BUY_SIGNAL_PATH_A"
                    phase = "NONE"
                    wave_start_time = None

                elif retesting_support:
                    event = "BUY_SIGNAL_PATH_B"
                    phase = "NONE"
                    wave_start_time = None

                elif path_c:
                    event = "BUY_SIGNAL_PATH_C"
                    phase = "NONE"
                    wave_start_time = None

            # ---------------- SELL side (independent triggers) ----------------

            if bars_since_rejection_trigger is not None:
                bars_since_rejection_trigger += 1

            if event is None and not near_weekly_support and price_below_weekly:

                broke_down = (
                    not breakdown_consumed
                    and rsi < cls.SELL_BREAKDOWN_LEVEL <= prev_rsi
                    and pd.notna(swing_low)
                    and low <= swing_low
                )

                rejected_at_60 = (
                    not rejection_consumed
                    and pd.notna(rsi_peak)
                    and 55 <= rsi_peak < cls.BUY_CONFIRM_LEVEL
                    and rsi <= rsi_peak - 5
                    and pd.notna(ema200)
                    and price <= ema200 * (1 + cls.RESISTANCE_BAND_PCT / 100)
                )

                if broke_down:
                    event = "SELL_TRIGGER_BREAKDOWN"
                    last_sell_trigger_bars_ago = 0
                    breakdown_consumed = True
                    sell_phase = "ARMED"
                    sell_retest_armed = False
                    sell_wave_low = low

                elif rejected_at_60:
                    event = "SELL_TRIGGER_REJECTION"
                    last_sell_trigger_bars_ago = 0
                    rejection_consumed = True
                    bars_since_rejection_trigger = 0

            preceded_by_rejection = False

            if sell_phase == "ARMED" and near_weekly_support:
                sell_phase = "NONE"
                sell_retest_armed = False

            elif event is None and sell_phase == "ARMED":

                prior_wave_low = sell_wave_low

                if rsi >= cls.SELL_BOUNCE_CEILING:
                    sell_phase = "NONE"
                    sell_retest_armed = False

                elif cls.SELL_BREAKDOWN_LEVEL <= rsi < cls.SELL_BOUNCE_CEILING:
                    sell_retest_armed = True

                elif sell_retest_armed and rsi < cls.SELL_BREAKDOWN_LEVEL and low <= prior_wave_low:
                    event = "SELL_SIGNAL_CONTINUATION"
                    last_sell_trigger_bars_ago = 0
                    preceded_by_rejection = (
                        bars_since_rejection_trigger is not None
                        and bars_since_rejection_trigger <= cls.SELL_CONTINUATION_LOOKBACK_BARS
                    )
                    sell_phase = "NONE"
                    sell_retest_armed = False

                if sell_phase == "ARMED":
                    sell_wave_low = min(prior_wave_low, low)

            if rsi >= cls.SELL_REJECTION_LEVEL:
                rejection_consumed = False

            if rsi >= cls.SELL_BREAKDOWN_LEVEL + 5:
                breakdown_consumed = False

            reverses_sell = (
                event in ("BUY_SIGNAL_PATH_A", "BUY_SIGNAL_PATH_B", "BUY_SIGNAL_PATH_C")
                and last_sell_trigger_bars_ago is not None
                and last_sell_trigger_bars_ago <= 10
            )

            trace.append(
                {
                    "time": timestamp,
                    "phase": phase,
                    "event": event,
                    "path_c_forming": path_c_forming,
                    "preceded_by_rejection": preceded_by_rejection,
                    "reverses_sell": reverses_sell,
                    "rsi": rsi,
                    "price": price,
                    "ema20": ema20,
                    "ema200": ema200,
                    "weekly_ema200": weekly_ema200,
                    "weekly_rsi": weekly_rsi,
                    "weekly_event": weekly_event,
                    "weekly_path_c_event": weekly_path_c_event,
                    "weekly_path_c_forming": weekly_path_c_forming,
                    "swing_low": swing_low,
                    "swing_high": swing_high,
                    "wave_start_time": wave_start_time,
                }
            )

            prev_rsi = rsi
            prev_above_ema200 = above_ema200
            prev_above_ema20 = above_ema20

        return trace

    # ------------------------------------------------------------------
    # Trade levels (placeholders - see module docstring)
    # ------------------------------------------------------------------

    @staticmethod
    def _price_round(value, price):

        if value is None or pd.isna(value):
            return None

        decimals = 4 if abs(price) < 10 else 2

        return round(value, decimals)

    @classmethod
    def _buy_levels(cls, ind_daily, wave_start_time, price):

        low_series = ind_daily["low"]
        wave_lows = low_series[low_series.index >= wave_start_time] if wave_start_time else low_series

        stop = cls._price_round(float(wave_lows.min()), price) if not wave_lows.empty else None
        target = cls._price_round(price * (1 + cls.TARGET_PCT / 100), price)

        risk = round(price - stop, 6) if stop else None
        risk_reward = round((target - price) / risk, 2) if risk and risk > 0 else 0.0

        return {"stop": stop, "target1": target, "target2": target, "risk_reward": risk_reward}

    @classmethod
    def _sell_levels(cls, swing_high, price):

        stop = cls._price_round(float(swing_high), price) if pd.notna(swing_high) else None
        target = cls._price_round(price * (1 - cls.TARGET_PCT / 100), price)

        risk = round(stop - price, 6) if stop else None
        risk_reward = round((price - target) / risk, 2) if risk and risk > 0 else 0.0

        return {"stop": stop, "target1": target, "target2": target, "risk_reward": risk_reward}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @classmethod
    def run_symbol(cls, symbol, period_daily="10y", period_weekly="15y"):

        df_daily = YahooProvider().history(symbol, interval="1d", period=period_daily)
        df_weekly = YahooProvider().history(symbol, interval="1wk", period=period_weekly)

        if df_daily.empty or df_weekly.empty or len(df_daily) < cls.MIN_HISTORY_DAILY:
            return None

        ind_daily = cls._prepare_daily(df_daily)
        ind_weekly = cls._prepare_weekly(df_weekly)

        combined = cls._combined_timeline(ind_daily, ind_weekly)

        if combined.empty:
            return None

        trace = cls.walk(combined)

        return {"trace": trace, "ind_daily": ind_daily}

    @classmethod
    def describe(cls, result):

        if result is None or not result["trace"]:
            return "Not enough Daily+Weekly history to evaluate this instrument yet.", "NONE", None

        trace = result["trace"]
        last = trace[-1]
        phase = last["phase"]
        rsi = round(last["rsi"], 2)
        price = float(last["price"])

        weekly_note = ""
        last_weekly_event_bar = next((bar for bar in reversed(trace) if bar["weekly_event"]), None)

        if last_weekly_event_bar is not None:
            weeks_since = (last["time"] - last_weekly_event_bar["time"]).total_seconds() / (86400 * 7)

            if weeks_since <= cls.WEEKLY_CONFLUENCE_RECENT_WEEKS:
                weekly_note += (
                    f" 📅 Weekly confluence: RSI broke above {cls.WEEKLY_ZONE_HIGH} after multiple failed tries "
                    f"(weekly RSI {round(last_weekly_event_bar['weekly_rsi'], 2)}) - often a stronger, longer move."
                )

        last_weekly_path_c_bar = next((bar for bar in reversed(trace) if bar["weekly_path_c_event"]), None)

        if last_weekly_path_c_bar is not None:
            weeks_since = (last["time"] - last_weekly_path_c_bar["time"]).total_seconds() / (86400 * 7)

            if weeks_since <= cls.WEEKLY_CONFLUENCE_RECENT_WEEKS:
                weekly_note += (
                    f" 📅 Weekly confluence: RSI held 65 as support and price reclaimed the Weekly 200 EMA "
                    f"(weekly RSI {round(last_weekly_path_c_bar['weekly_rsi'], 2)}) - the weekly-timeframe version of Path C."
                )

        if last["weekly_path_c_forming"]:
            weekly_note += " 🔵 Weekly Path C forming — Weekly RSI holding 60-65 as support with price above the Weekly 200 EMA."

        last_event_bar = next((bar for bar in reversed(trace) if bar["event"]), None)
        recent = last_event_bar is not None and trace.index(last_event_bar) >= len(trace) - 3

        if recent:

            event = last_event_bar["event"]

            if event in ("BUY_SIGNAL_PATH_A", "BUY_SIGNAL_PATH_B", "BUY_SIGNAL_PATH_C"):

                levels = cls._buy_levels(result["ind_daily"], last_event_bar["wave_start_time"], price)

                path_names = {
                    "BUY_SIGNAL_PATH_A": "A (EMA20/200 far apart)",
                    "BUY_SIGNAL_PATH_B": "B (200 EMA reclaimed + retest)",
                    "BUY_SIGNAL_PATH_C": "C (RSI held 65 as support + 200 EMA reclaimed)",
                }
                path = path_names[event]
                reversal_note = " ⚠️ This reverses a recent SELL trigger - that thesis is now invalidated." if last_event_bar["reverses_sell"] else ""

                return (
                    f"🟢 BUY signal (Daily) — Path {path}. RSI {rsi}.{reversal_note}{weekly_note}",
                    "BUY_SIGNAL",
                    levels,
                )

            if event == "SELL_TRIGGER_BREAKDOWN":

                levels = cls._sell_levels(last_event_bar["swing_high"], price)

                return (
                    f"🔴 SELL trigger (Daily) — RSI broke below 40 with price breaking a recent low. RSI {rsi}.{weekly_note}",
                    "SELL_SIGNAL",
                    levels,
                )

            if event == "SELL_TRIGGER_REJECTION":

                levels = cls._sell_levels(last_event_bar["swing_high"], price)

                return (
                    f"🔴 SELL trigger (Daily) — RSI rejected near 60 with price at/below the Daily EMA200. RSI {rsi}.{weekly_note}",
                    "SELL_SIGNAL",
                    levels,
                )

            if event == "SELL_SIGNAL_CONTINUATION":

                levels = cls._sell_levels(last_event_bar["swing_high"], price)

                rejection_note = (
                    " This follows a failed rally near 60-65 - the earlier bullish breakout attempt failed, reinforcing this move."
                    if last_event_bar["preceded_by_rejection"]
                    else ""
                )

                return (
                    f"🔴🔴 SELL continuation (Daily) — RSI broke below 40, took a slight support bounce, then broke down again. RSI {rsi}.{rejection_note}{weekly_note}",
                    "SELL_SIGNAL_CONTINUATION",
                    levels,
                )

        if phase == "BUY_ALERT_TOUCH":
            return f"🟡 BUY watch (Daily) — RSI touched ≤22 ({rsi}), waiting for a cross above 65.{weekly_note}", "BUY_ALERT", None

        if phase == "BUY_ALERT_CONFIRM":

            forming_note = ""

            if last["path_c_forming"]:
                forming_note = (
                    " 🔵 Path C forming — RSI holding the 60-65 zone as support, price already holding "
                    "above the Daily EMA20/200. A re-cross above 65 would confirm the BUY."
                )

            return (
                f"🟠 BUY confirmed alert (Daily) — RSI crossed 65 ({rsi}), watching EMA20/200 spacing or a 200-EMA retest for entry.{forming_note}{weekly_note}",
                "BUY_ALERT_CONFIRM_PATH_C_FORMING" if last["path_c_forming"] else "BUY_ALERT_CONFIRM",
                None,
            )

        return f"⚪ Watching (Daily) — RSI {rsi}, no setup active.{weekly_note}", "WATCHING", None

    STATE_LABELS = {
        "NONE": "⚪ No data",
        "WATCHING": "⚪ Watching",
        "BUY_ALERT": "🟡 Buy watch — RSI touched 22",
        "BUY_ALERT_CONFIRM": "🟠 Buy alert — RSI crossed 65",
        "BUY_ALERT_CONFIRM_PATH_C_FORMING": "🔵 Path C forming — watch closely",
        "BUY_SIGNAL": "🟢 BUY signal",
        "SELL_SIGNAL": "🔴 SELL trigger",
        "SELL_SIGNAL_CONTINUATION": "🔴🔴 SELL continuation",
    }

    @classmethod
    def short_label(cls, result):

        _, state, _ = cls.describe(result)

        return cls.STATE_LABELS.get(state, "⚪ Watching")

    WEEKLY_STATE_LABELS = {
        "NONE": "⚪ No data",
        "WATCHING": "⚪ Watching",
        "MULTI_TRY_BREAKOUT": "📅 Multi-try breakout",
        "PATH_C_CONFIRMED": "📅 Path C confirmed",
        "PATH_C_FORMING": "🔵 Path C forming",
    }

    @classmethod
    def weekly_describe(cls, result):
        """
        Weekly-level read, independent of the Daily BUY/SELL machine
        above - the Weekly timeframe here is confluence-only (no own
        buy/sell state machine, same as the Daily confluence notes on
        top of the 1H engine), so this surfaces whichever weekly
        confluence signal is most recently relevant: a confirmed
        multi-try breakout or Path C (within
        WEEKLY_CONFLUENCE_RECENT_WEEKS), else whether one is currently
        forming, else just the current Weekly RSI. No extra fetch -
        reuses the same result already computed by run_symbol().
        """

        if result is None or not result["trace"]:
            return "Not enough Weekly history to evaluate this instrument yet.", "NONE"

        trace = result["trace"]
        last = trace[-1]
        weekly_rsi = round(last["weekly_rsi"], 2) if pd.notna(last["weekly_rsi"]) else None

        last_event_bar = next((bar for bar in reversed(trace) if bar["weekly_event"]), None)

        if last_event_bar is not None:
            weeks_since = (last["time"] - last_event_bar["time"]).total_seconds() / (86400 * 7)

            if weeks_since <= cls.WEEKLY_CONFLUENCE_RECENT_WEEKS:
                return (
                    f"📅 Weekly multi-try breakout — RSI broke above {cls.WEEKLY_ZONE_HIGH} after multiple failed tries "
                    f"(weekly RSI {round(last_event_bar['weekly_rsi'], 2)}).",
                    "MULTI_TRY_BREAKOUT",
                )

        last_path_c_bar = next((bar for bar in reversed(trace) if bar["weekly_path_c_event"]), None)

        if last_path_c_bar is not None:
            weeks_since = (last["time"] - last_path_c_bar["time"]).total_seconds() / (86400 * 7)

            if weeks_since <= cls.WEEKLY_CONFLUENCE_RECENT_WEEKS:
                return (
                    f"📅 Weekly Path C confirmed — RSI held 65 as support and price reclaimed the Weekly 200 EMA "
                    f"(weekly RSI {round(last_path_c_bar['weekly_rsi'], 2)}).",
                    "PATH_C_CONFIRMED",
                )

        if last["weekly_path_c_forming"]:
            return (
                f"🔵 Weekly Path C forming — Weekly RSI ({weekly_rsi}) holding 60-65 as support with price above the Weekly 200 EMA.",
                "PATH_C_FORMING",
            )

        return f"⚪ Watching — Weekly RSI {weekly_rsi}, no confluence active.", "WATCHING"
