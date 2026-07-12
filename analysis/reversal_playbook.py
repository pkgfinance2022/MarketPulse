"""
Reversal playbook (v2).

Full replacement of the original dual-timeframe (1H+15m) Algo1/Algo2
engine, per explicit instruction: Algo 2 wasn't working, and the buy
side needed a real entry-timing rule instead of firing the moment RSI
crossed 65. Runs on 1H bars only, with the DAILY 200 EMA as a
higher-timeframe context filter - no 15m confirmation step anymore.

BUY SIDE (refines the original Algo 1):
    Pre-filter: price above the Daily 200 EMA.
    Step 1: 1H RSI touches <=22 -> start watching.
    Step 2: 1H RSI crosses up through 65 -> ALERT ONLY, not a buy yet.
    Step 3 (either path fires the actual entry):
        Path A: 1H EMA20 and EMA200 are meaningfully far apart
                (diverged) -> BUY.
        Path B: 1H price crossed up through EMA200 recently AND is
                now holding/retesting it as support -> BUY.
    SL = low of the wave since the Step 1 touch. Target = TARGET_PCT
    (a placeholder - explicitly left for the user to tune over time,
    not a settled rule).

SELL SIDE (replaces Algo 2 entirely) - two independent triggers, no
"arm" stage, both explicitly described as still tentative ideas:
    Trigger 1: 1H RSI crosses below 40 AND price breaks a recent
               swing low at the same time.
    Trigger 2: 1H RSI rallies toward 60 and is REJECTED there (fails
               to cross above 60, turns back down), while price is
               at/below the 1H EMA200 (acting as resistance).
    Guardrail: suppressed if price is within DAILY_SUPPORT_BAND_PCT of
               the Daily 200 EMA (that level likely acts as support -
               don't fight it).
    SL = high of the recent swing. Target = TARGET_PCT (mirrored
    placeholder).

Cross-check: if a BUY signal fires shortly after a SELL trigger was
active on the same symbol, the description explicitly flags that the
sell thesis just got invalidated - directly answering the concern
"if the sell call turns into a buy Path A/B, it's against me."

Every specific threshold below (the "22"/"65"/"40"/"60" RSI levels,
what counts as EMAs "far apart", the retest-support band, the
daily "don't sell near support" band, and the flat 1-1.5% target) is
this author's best-effort reading of a still-evolving, hand-drawn
spec - flagged as tunable class constants, not fixed truths.
"""

import pandas as pd
import ta

from providers.yahoo import YahooProvider


class ReversalPlaybook:

    MIN_HISTORY_1H = 210

    # --- Buy side ---
    BUY_OVERSOLD_TOUCH = 22
    BUY_CONFIRM_LEVEL = 65
    FAR_THRESHOLD_PCT = 2.0        # |EMA20 - EMA200| / EMA200 * 100 - how far counts as "far apart"
    RETEST_BAND_PCT = 0.5          # how close to EMA200 counts as "holding support"
    CROSS_LOOKBACK_BARS = 15       # how far back to look for a fresh EMA200 cross-up
    RSI_RETEST_FLOOR = 60          # Path C: RSI can dip back toward 65 but not below this and still count as "holding support"

    # --- Sell side ---
    SELL_BREAKDOWN_LEVEL = 40
    SELL_REJECTION_LEVEL = 60
    SWING_LOOKBACK_BARS = 10       # for "breaking a recent low" / recent swing high
    RESISTANCE_BAND_PCT = 0.5      # how close to (at-or-below) EMA200 counts as resistance
    SELL_BOUNCE_CEILING = 50       # a post-breakdown bounce must stay below this to count as "slight support", not a real recovery
    SELL_CONTINUATION_LOOKBACK_BARS = 30   # how long a prior 60-65 rejection still counts as "preceding" a continuation breakdown

    # --- Shared ---
    DAILY_SUPPORT_BAND_PCT = 1.0   # don't sell within this % of the Daily 200 EMA
    TARGET_PCT = 1.25              # placeholder - explicitly left for the user to tune
    DAILY_CONFLUENCE_RECENT_DAYS = 3   # how long the daily multi-try breakout note stays "fresh"

    # --- Uptrend RSI-40 support (independent confluence note) ---
    # Observed pattern: once price has held above the 1H 200 EMA for a
    # sustained run (not just a fresh cross), a pullback to ~RSI 40
    # that holds as support and bounces is often a good continuation
    # entry - "not always, but not a thing to skip." Deliberately does
    # NOT reintroduce a live 15m data feed (that dual-timeframe
    # complexity was explicitly removed earlier) - the description
    # just notes that 15m often shows its own oversold-then-65
    # readiness at the same moment, as context, not a computed check.
    UPTREND_MIN_STREAK_BARS = 50   # how long price must have held above the 1H 200 EMA to call it a "definite run"
    UPTREND_SUPPORT_ZONE_LOW = 35
    UPTREND_SUPPORT_ZONE_HIGH = 45
    UPTREND_SUPPORT_RESET_LEVEL = 55   # RSI must rally back above this to re-arm a fresh support test
    UPTREND_SUPPORT_RECENT_BARS = 5    # how long the note stays "fresh" after firing

    # ------------------------------------------------------------------
    # Data prep
    # ------------------------------------------------------------------

    REJECTION_PEAK_LOOKBACK = 8   # bars - how far back to look for the RSI rally peak near 60

    @staticmethod
    def _prepare_1h(df):

        close, high, low = df["Close"], df["High"], df["Low"]

        rsi = ta.momentum.rsi(close, window=14)

        return {
            "close": close,
            "high": high,
            "low": low,
            "ema20": ta.trend.ema_indicator(close, window=20),
            "ema200": ta.trend.ema_indicator(close, window=200),
            "rsi": rsi,
            "rsi_peak": rsi.rolling(ReversalPlaybook.REJECTION_PEAK_LOOKBACK).max(),
            "swing_low": low.rolling(ReversalPlaybook.SWING_LOOKBACK_BARS).min(),
            "swing_high": high.rolling(ReversalPlaybook.SWING_LOOKBACK_BARS).max(),
        }

    # --- Daily confluence (independent of the 1H buy/sell machine) ---
    DAILY_ZONE_LOW = 55
    DAILY_ZONE_HIGH = 65
    DAILY_RESET_FLOOR = 45
    DAILY_MIN_ATTEMPTS = 2

    @classmethod
    def _daily_multi_try_breakout(cls, rsi):
        """
        For each day, tracks how many distinct prior "attempts" (a
        rally that reached into [DAILY_ZONE_LOW, DAILY_ZONE_HIGH) then
        retreated back below DAILY_ZONE_LOW without ever breaking out)
        happened since the last reset (RSI dropping below
        DAILY_RESET_FLOOR - a fresh cycle), and flags the day RSI
        finally crosses above DAILY_ZONE_HIGH as a "multi-try
        breakout" only if at least DAILY_MIN_ATTEMPTS occurred first.
        Sequential/stateful, so a plain loop over the (short) daily
        series rather than a vectorized rolling computation.
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

                if value < cls.DAILY_RESET_FLOOR:
                    attempts = 0
                    in_attempt = False

                if cls.DAILY_ZONE_LOW <= value < cls.DAILY_ZONE_HIGH and not in_attempt:
                    in_attempt = True

                if in_attempt and value < cls.DAILY_ZONE_LOW:
                    attempts += 1
                    in_attempt = False

                if value >= cls.DAILY_ZONE_HIGH and prev < cls.DAILY_ZONE_HIGH:
                    flag = attempts >= cls.DAILY_MIN_ATTEMPTS
                    attempts = 0
                    in_attempt = False

            flags.append(flag)
            prev = value

        return pd.Series(flags, index=rsi.index)

    DAILY_CROSS_LOOKBACK_DAYS = 5   # Daily analog of CROSS_LOOKBACK_BARS

    @classmethod
    def _daily_support_reclaim(cls, rsi, close, ema200):
        """
        Daily analog of the 1H Path C idea: once Daily RSI has crossed
        above 65, watch for it to pull back into the 60-65 band
        (holding it as support - same RSI_RETEST_FLOOR/BUY_CONFIRM_LEVEL
        band as the 1H machine) and then resume back above 65, at the
        same time price has recently reclaimed the Daily 200 EMA.
        Returns two boolean Series: "forming" (still developing - RSI
        currently holding the band with price already above the Daily
        200 EMA) and "confirmed" (the day RSI actually resumes above
        65 with a recent EMA reclaim). Sequential/stateful, same
        reasoning as _daily_multi_try_breakout for using a plain loop.
        """

        phase = "NONE"
        retest_armed = False
        days_since_ema_cross = None
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
                days_since_ema_cross = 0
            elif days_since_ema_cross is not None:
                days_since_ema_cross += 1

            if days_since_ema_cross is not None and days_since_ema_cross > cls.DAILY_CROSS_LOOKBACK_DAYS:
                days_since_ema_cross = None

            if prev_rsi is not None:

                if r < cls.DAILY_RESET_FLOOR:
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
                        and days_since_ema_cross is not None
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
    def _prepare_daily(cls, df):

        close = df["Close"]
        rsi = ta.momentum.rsi(close, window=14)
        ema200 = ta.trend.ema_indicator(close, window=200)

        path_c_forming, path_c_confirmed = cls._daily_support_reclaim(rsi, close, ema200)

        return {
            "close": close,
            "ema200": ema200,
            "rsi": rsi,
            "multi_try_breakout": cls._daily_multi_try_breakout(rsi),
            "path_c_forming": path_c_forming,
            "path_c_confirmed": path_c_confirmed,
        }

    @classmethod
    def _combined_timeline(cls, ind_1h, ind_daily):
        """
        One row per 1H bar, with the Daily 200 EMA forward-filled onto
        it (merge_asof, direction="backward") - never looks at a
        daily bar that hasn't actually closed yet. yfinance returns 1H
        data tz-aware and daily data tz-naive; both are normalized to
        naive timestamps here purely so merge_asof can align them.
        """

        h1_index = ind_1h["close"].index
        h1_index_naive = h1_index.tz_localize(None) if h1_index.tz is not None else h1_index

        h1 = pd.DataFrame(
            {
                "h1_close": ind_1h["close"].values,
                "h1_high": ind_1h["high"].values,
                "h1_low": ind_1h["low"].values,
                "h1_ema20": ind_1h["ema20"].values,
                "h1_ema200": ind_1h["ema200"].values,
                "h1_rsi": ind_1h["rsi"].values,
                "h1_rsi_peak": ind_1h["rsi_peak"].values,
                "h1_swing_low": ind_1h["swing_low"].values,
                "h1_swing_high": ind_1h["swing_high"].values,
            },
            index=h1_index_naive,
        ).sort_index()

        d_index = ind_daily["close"].index
        d_index_naive = d_index.tz_localize(None) if d_index.tz is not None else d_index

        daily = pd.DataFrame(
            {
                "daily_close": ind_daily["close"].values,
                "daily_ema200": ind_daily["ema200"].values,
                "daily_rsi": ind_daily["rsi"].values,
                "daily_multi_try_breakout": ind_daily["multi_try_breakout"].values,
                "daily_path_c_forming": ind_daily["path_c_forming"].values,
                "daily_path_c_confirmed": ind_daily["path_c_confirmed"].values,
            },
            index=d_index_naive,
        ).sort_index()

        combined = pd.merge_asof(
            h1,
            daily,
            left_index=True,
            right_index=True,
            direction="backward",
        )

        # Real 1H timestamps for display, restored after the merge.
        combined.index = h1_index

        return combined.dropna(subset=["h1_rsi", "daily_ema200"])

    # ------------------------------------------------------------------
    # Walk
    # ------------------------------------------------------------------

    @classmethod
    def walk(cls, combined):
        """
        Single pass over the 1H timeline. Buy side is a 3-stage phase
        machine (touch -> confirm -> entry). Sell side is two
        independent, stateless triggers checked every bar (no "arm"
        stage was described for them). Returns the full per-bar trace.
        """

        phase = "NONE"
        wave_start_time = None

        # Tracks how many bars ago price crossed up through EMA200,
        # for Path B's "crossed up AND now retesting support" check,
        # and Path C's "correction over" confirmation below.
        bars_since_cross_up = None

        # Same tracking for EMA20 - Path C's "correction over" signal
        # counts a reclaim of either the 1H 20 or 200 EMA.
        bars_since_cross_up_20 = None

        # Path C: has RSI, after crossing 65, pulled back toward it
        # (without falling below RSI_RETEST_FLOOR) and is now resuming
        # up? True while a retest is "in progress" - reset if RSI
        # falls through the floor (retest failed) or once consumed by
        # a fresh cross back above 65.
        rsi_retest_armed = False

        last_sell_trigger_bars_ago = None

        # Without this, "RSI rejected near 60" would re-fire on every
        # single bar RSI drifts slightly lower while still hovering
        # near 60 - one real rally-and-rollover swing produced
        # hundreds of near-duplicate events before this was added.
        # Consumed once fired; only re-armed once RSI rallies back up
        # to 60+ again (a fresh attempt).
        rejection_consumed = False

        # Same whipsaw problem on choppier instruments (oil, FX): RSI
        # oscillating right around 40 re-fired "crossed below 40" on
        # every whipsaw. Consumed once fired; only re-armed once RSI
        # recovers meaningfully above 40 (not just barely back over
        # the line) first.
        breakdown_consumed = False

        # Sell continuation: after an initial breakdown, RSI often
        # takes a "slight support" bounce (a weak recovery that stays
        # below SELL_BOUNCE_CEILING - not a real reversal) before
        # breaking down again to a fresh low. That second break is the
        # "real" bear call. sell_phase tracks this separately from the
        # instant breakdown/rejection triggers above.
        sell_phase = "NONE"
        sell_retest_armed = False
        sell_wave_low = None

        # Bars since the last rejected-at-60 trigger, so a
        # continuation signal can flag when it was preceded by a
        # failed breakout attempt (the "breakout failed" read).
        bars_since_rejection_trigger = None

        # Uptrend RSI-40 support: counts consecutive bars price has
        # held above the 1H 200 EMA (resets the instant it closes
        # below), and tracks whether RSI has dipped into the 35-45
        # support band during a qualifying run, armed/consumed the
        # same debounce shape as the other whipsaw-prone triggers.
        bars_above_ema200_streak = 0
        uptrend_support_armed = False
        uptrend_support_consumed = False

        trace = []

        prev_rsi = None
        prev_above_ema200 = None
        prev_above_ema20 = None
        prev_daily_multi_try = None
        prev_daily_path_c_confirmed = None

        for i in range(len(combined)):

            row = combined.iloc[i]
            timestamp = combined.index[i]

            price = row["h1_close"]
            high = row["h1_high"]
            low = row["h1_low"]
            ema20 = row["h1_ema20"]
            ema200 = row["h1_ema200"]
            rsi = row["h1_rsi"]
            rsi_peak = row["h1_rsi_peak"]
            swing_low = row["h1_swing_low"]
            swing_high = row["h1_swing_high"]
            daily_ema200 = row["daily_ema200"]
            daily_rsi = row["daily_rsi"]
            daily_multi_try = bool(row["daily_multi_try_breakout"]) if pd.notna(row["daily_multi_try_breakout"]) else False
            daily_path_c_forming = bool(row["daily_path_c_forming"]) if pd.notna(row["daily_path_c_forming"]) else False
            daily_path_c_confirmed = bool(row["daily_path_c_confirmed"]) if pd.notna(row["daily_path_c_confirmed"]) else False

            if prev_rsi is None:
                prev_rsi = rsi

            if prev_daily_multi_try is None:
                prev_daily_multi_try = daily_multi_try

            if prev_daily_path_c_confirmed is None:
                prev_daily_path_c_confirmed = daily_path_c_confirmed

            # The daily flags are forward-filled across every 1H bar
            # of that trading day, so only the first bar where each
            # flips False -> True counts as the actual event (once
            # per day).
            daily_event = "DAILY_MULTI_TRY_BREAKOUT" if (daily_multi_try and not prev_daily_multi_try) else None
            daily_path_c_event = "DAILY_PATH_C_BREAKOUT" if (daily_path_c_confirmed and not prev_daily_path_c_confirmed) else None
            prev_daily_multi_try = daily_multi_try
            prev_daily_path_c_confirmed = daily_path_c_confirmed

            above_ema200 = price > ema200 if pd.notna(ema200) else None
            above_ema20 = price > ema20 if pd.notna(ema20) else None

            if prev_above_ema200 is None:
                prev_above_ema200 = above_ema200

            if prev_above_ema20 is None:
                prev_above_ema20 = above_ema20

            if above_ema200:
                bars_above_ema200_streak += 1
            else:
                bars_above_ema200_streak = 0
                uptrend_support_armed = False
                uptrend_support_consumed = False

            in_definite_uptrend = bars_above_ema200_streak >= cls.UPTREND_MIN_STREAK_BARS

            uptrend_rsi40_support = False

            if in_definite_uptrend:

                if not uptrend_support_consumed and cls.UPTREND_SUPPORT_ZONE_LOW <= rsi < cls.UPTREND_SUPPORT_ZONE_HIGH:
                    uptrend_support_armed = True

                if uptrend_support_armed and rsi >= cls.UPTREND_SUPPORT_ZONE_HIGH and prev_rsi < cls.UPTREND_SUPPORT_ZONE_HIGH:
                    uptrend_rsi40_support = True
                    uptrend_support_consumed = True
                    uptrend_support_armed = False

                if rsi >= cls.UPTREND_SUPPORT_RESET_LEVEL:
                    uptrend_support_consumed = False

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
            price_above_daily = pd.notna(daily_ema200) and price > daily_ema200
            price_below_daily = pd.notna(daily_ema200) and price < daily_ema200

            near_daily_support = (
                pd.notna(daily_ema200)
                and abs(price - daily_ema200) / daily_ema200 * 100 <= cls.DAILY_SUPPORT_BAND_PCT
            )

            # ---------------- BUY side (phase machine) ----------------

            if phase == "NONE" and price_above_daily and rsi <= cls.BUY_OVERSOLD_TOUCH:
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

                # Path C: RSI pulled back toward 65 after the initial
                # cross (without falling through the floor - a failed
                # retest), then resumes back above 65, at the same
                # time the "hourly correction" is confirmed over
                # (price has recently reclaimed the 1H 20 or 200 EMA).
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

                # Visible *during* formation, before the re-cross above
                # 65 confirms it - RSI is currently holding the 60-65
                # zone as support while price is already holding above
                # the 20 or 200 EMA, so a Path C confirmation could
                # fire on the very next bar.
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

            if event is None and not near_daily_support and price_below_daily:

                broke_down = (
                    not breakdown_consumed
                    and rsi < cls.SELL_BREAKDOWN_LEVEL <= prev_rsi
                    and pd.notna(swing_low)
                    and low <= swing_low
                )

                # A rally that peaked somewhere in the 55-65 zone (got
                # close to 60 without breaking out through 65, which
                # would instead be a BUY confirm) and has since rolled
                # over meaningfully off that peak - not just any bar
                # where RSI ticks down while hovering near 60.
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

            # Sell continuation - checked independently of the instant
            # triggers above (an ARMED sell_phase can persist across
            # many bars while the two instant triggers stay quiet).
            preceded_by_rejection = False

            if sell_phase == "ARMED" and near_daily_support:
                # Same guardrail as the instant triggers - don't keep
                # arming a sell continuation into likely daily support.
                sell_phase = "NONE"
                sell_retest_armed = False

            elif event is None and sell_phase == "ARMED":

                prior_wave_low = sell_wave_low

                if rsi >= cls.SELL_BOUNCE_CEILING:
                    # A real recovery, not just a "slight" bounce -
                    # this specific continuation thesis is invalidated
                    # (the ordinary BUY machine/reverses_sell flag
                    # covers the case where this turns bullish).
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
                    "uptrend_rsi40_support": uptrend_rsi40_support,
                    "preceded_by_rejection": preceded_by_rejection,
                    "reverses_sell": reverses_sell,
                    "rsi": rsi,
                    "price": price,
                    "ema20": ema20,
                    "ema200": ema200,
                    "daily_ema200": daily_ema200,
                    "daily_rsi": daily_rsi,
                    "daily_event": daily_event,
                    "daily_path_c_event": daily_path_c_event,
                    "daily_path_c_forming": daily_path_c_forming,
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
    def _buy_levels(cls, ind_1h, wave_start_time, price):

        low_series = ind_1h["low"]
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
    def run_symbol(cls, symbol, period_1h="730d", period_daily="5y"):

        df_1h = YahooProvider().history(symbol, interval="1h", period=period_1h)
        df_daily = YahooProvider().history(symbol, interval="1d", period=period_daily)

        if df_1h.empty or df_daily.empty or len(df_1h) < cls.MIN_HISTORY_1H:
            return None

        ind_1h = cls._prepare_1h(df_1h)
        ind_daily = cls._prepare_daily(df_daily)

        combined = cls._combined_timeline(ind_1h, ind_daily)

        if combined.empty:
            return None

        trace = cls.walk(combined)

        return {"trace": trace, "ind_1h": ind_1h}

    @classmethod
    def describe(cls, result):

        if result is None or not result["trace"]:
            return "Not enough 1H+Daily history to evaluate this instrument yet.", "NONE", None

        trace = result["trace"]
        last = trace[-1]
        phase = last["phase"]
        rsi = round(last["rsi"], 2)
        price = float(last["price"])

        # Daily confluence is independent of the 1H buy/sell machine -
        # surfaced as an extra note on top of whatever else is active,
        # never gating or replacing the 1H state. Two distinct daily
        # notes can both be relevant (multi-try breakout, and the
        # Path C-style support-reclaim), so both are checked and
        # either/both can be appended.
        daily_note = ""
        last_daily_event_bar = next((bar for bar in reversed(trace) if bar["daily_event"]), None)

        if last_daily_event_bar is not None:
            days_since = (last["time"] - last_daily_event_bar["time"]).total_seconds() / 86400

            if days_since <= cls.DAILY_CONFLUENCE_RECENT_DAYS:
                daily_note += (
                    f" 📅 Daily confluence: RSI broke above {cls.DAILY_ZONE_HIGH} after multiple failed tries "
                    f"(daily RSI {round(last_daily_event_bar['daily_rsi'], 2)}) - often a stronger, longer move."
                )

        last_daily_path_c_bar = next((bar for bar in reversed(trace) if bar["daily_path_c_event"]), None)

        if last_daily_path_c_bar is not None:
            days_since = (last["time"] - last_daily_path_c_bar["time"]).total_seconds() / 86400

            if days_since <= cls.DAILY_CONFLUENCE_RECENT_DAYS:
                daily_note += (
                    f" 📅 Daily confluence: RSI held 65 as support and price reclaimed the Daily 200 EMA "
                    f"(daily RSI {round(last_daily_path_c_bar['daily_rsi'], 2)}) - the daily-timeframe version of Path C."
                )

        if last["daily_path_c_forming"]:
            daily_note += " 🔵 Daily Path C forming — Daily RSI holding 60-65 as support with price above the Daily 200 EMA."

        # Uptrend RSI-40 support - independent confluence, same
        # additive treatment as the daily notes above. "Not always,
        # but not a thing to skip": price has held above the 1H 200
        # EMA for a sustained run, and RSI just tested ~40 as support
        # and bounced - often a good continuation entry. 15m often
        # shows its own oversold-to-65 readiness at the same moment
        # (observed, not computed here - no live 15m feed).
        last_uptrend_support_bar = next((bar for bar in reversed(trace) if bar["uptrend_rsi40_support"]), None)

        if last_uptrend_support_bar is not None:
            bars_since = len(trace) - 1 - trace.index(last_uptrend_support_bar)

            if bars_since <= cls.UPTREND_SUPPORT_RECENT_BARS:
                daily_note += (
                    f" 🟢 Uptrend RSI-40 support: price has held above the 1H 200 EMA for a sustained run, and RSI "
                    f"just tested ~40 support and bounced - often a good continuation entry (not always, but worth "
                    f"noting). 15m often shows its own oversold-to-65 readiness at the same time."
                )

        last_event_bar = next((bar for bar in reversed(trace) if bar["event"]), None)
        recent = last_event_bar is not None and trace.index(last_event_bar) >= len(trace) - 3

        if recent:

            event = last_event_bar["event"]

            if event in ("BUY_SIGNAL_PATH_A", "BUY_SIGNAL_PATH_B", "BUY_SIGNAL_PATH_C"):

                levels = cls._buy_levels(result["ind_1h"], last_event_bar["wave_start_time"], price)

                path_names = {
                    "BUY_SIGNAL_PATH_A": "A (EMA20/200 far apart)",
                    "BUY_SIGNAL_PATH_B": "B (200 EMA reclaimed + retest)",
                    "BUY_SIGNAL_PATH_C": "C (RSI held 65 as support + 200 EMA reclaimed)",
                }
                path = path_names[event]
                reversal_note = " ⚠️ This reverses a recent SELL trigger - that thesis is now invalidated." if last_event_bar["reverses_sell"] else ""

                return (
                    f"🟢 BUY signal — Path {path}. RSI {rsi}.{reversal_note}{daily_note}",
                    "BUY_SIGNAL",
                    levels,
                )

            if event == "SELL_TRIGGER_BREAKDOWN":

                levels = cls._sell_levels(last_event_bar["swing_high"], price)

                return (
                    f"🔴 SELL trigger — RSI broke below 40 with price breaking a recent low. RSI {rsi}.{daily_note}",
                    "SELL_SIGNAL",
                    levels,
                )

            if event == "SELL_TRIGGER_REJECTION":

                levels = cls._sell_levels(last_event_bar["swing_high"], price)

                return (
                    f"🔴 SELL trigger — RSI rejected near 60 with price at/below the 1H EMA200. RSI {rsi}.{daily_note}",
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
                    f"🔴🔴 SELL continuation — RSI broke below 40, took a slight support bounce, then broke down again. RSI {rsi}.{rejection_note}{daily_note}",
                    "SELL_SIGNAL_CONTINUATION",
                    levels,
                )

        if phase == "BUY_ALERT_TOUCH":
            return f"🟡 BUY watch — 1H RSI touched ≤22 ({rsi}), waiting for a cross above 65.{daily_note}", "BUY_ALERT", None

        if phase == "BUY_ALERT_CONFIRM":

            forming_note = ""

            if last["path_c_forming"]:
                forming_note = (
                    " 🔵 Path C forming — RSI holding the 60-65 zone as support, price already holding "
                    "above the 1H EMA20/200. A re-cross above 65 would confirm the BUY."
                )

            return (
                f"🟠 BUY confirmed alert — RSI crossed 65 ({rsi}), watching EMA20/200 spacing or a 200-EMA retest for entry.{forming_note}{daily_note}",
                "BUY_ALERT_CONFIRM_PATH_C_FORMING" if last["path_c_forming"] else "BUY_ALERT_CONFIRM",
                None,
            )

        return f"⚪ Watching — RSI {rsi}, no setup active.{daily_note}", "WATCHING", None

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
