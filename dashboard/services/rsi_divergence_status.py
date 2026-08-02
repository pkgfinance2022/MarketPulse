"""
RSI Divergence status service.

Mirrors dashboard/services/rsi_wave_status.py exactly, wired to
RSIDivergenceStrategy instead - turns its bar-by-bar trace into (a) a
live per-ticker status + SL/target box, and (b) a whole-universe
screener label, both sharing the same fetch+walk so the screener and
the notification-check fragment can never disagree about what's
happening for a given symbol.
"""

from concurrent.futures import ThreadPoolExecutor

import ta

from analysis.rsi_divergence_strategy import Crypto1mRSIDivergenceStrategy, Crypto5mRSIDivergenceStrategy, Crypto15mRSIDivergenceStrategy, DailyRSIDivergenceStrategy, RSIDivergenceStrategy, StockRSIDivergenceStrategy, WeeklyStockRSIDivergenceStrategy

ACTIONABLE_STATES = {
    "ENTRY_LONG_DIVERGENCE": "LONG",
    "ENTRY_SHORT_DIVERGENCE": "SHORT",
}

SCREEN_WORKERS = 3   # kept low - Streamlit Community Cloud's free-tier container has a much lower OS thread limit than local dev; a higher count caused "can't start new thread" crashes in production


class RSIDivergenceStatusService:

    STRATEGY = RSIDivergenceStrategy

    ATR_WINDOW = 14
    SUPPORT_RESISTANCE_WINDOW = 20

    @classmethod
    def analyse(cls, symbol, period=None):
        """
        period=None (the default) lets cls.STRATEGY.run_symbol fall
        back to ITS OWN default - real bug found: hardcoding "730d"
        here unconditionally passed it through even to strategies whose
        natural period is much shorter (Crypto5m/15m's 60d, Crypto1m's
        7d - both yfinance hard limits), silently breaking every fetch
        for those with a "no price data found" error.
        """

        kwargs = {"period": period} if period else {}
        trace, df = cls.STRATEGY.run_symbol(symbol, **kwargs)

        if trace is None:
            return None

        description, state, event_time = cls.STRATEGY.describe(trace)
        last = trace[-1]

        direction = ACTIONABLE_STATES.get(state)

        stop_target = None

        if direction:

            close, high, low = df["Close"], df["High"], df["Low"]

            atr = ta.volatility.average_true_range(high, low, close, window=cls.ATR_WINDOW)
            support = low.rolling(cls.SUPPORT_RESISTANCE_WINDOW).min()
            resistance = high.rolling(cls.SUPPORT_RESISTANCE_WINDOW).max()

            stop_target = cls._stop_target(
                direction,
                last["price"],
                float(support.iloc[-1]),
                float(resistance.iloc[-1]),
                float(atr.iloc[-1]) if not (atr.empty or atr.isna().iloc[-1]) else 0.0,
            )

        return {
            "symbol": symbol,
            "df": df,
            "trace": trace,
            "state": state,
            "description": description,
            "price": last["price"],
            "rsi": round(last["rsi"], 2),
            "direction": direction,
            "stop_target": stop_target,
            "event_time": event_time,
        }

    @staticmethod
    def _price_round(value, price):
        """
        Same fix as ReversalPlaybook._price_round / RSIWaveStatusService's
        own copy - round(x, 2) collapses distinct stop/target levels to
        the same value for anything priced under ~10 (major forex pairs),
        since a whole cent there is ~100 pips.
        """

        if value is None:
            return None

        decimals = 4 if abs(price) < 10 else 2

        return round(value, decimals)

    @classmethod
    def _stop_target(cls, direction, price, support, resistance, atr):
        """Identical formula to RSIWaveStatusService._stop_target - same
        risk model, kept duplicated rather than imported so this engine
        stays fully independent (matches DailyReversalStatusService's
        own reasoning for not sharing code across engines)."""

        if direction == "LONG":

            stop = max(support, price - 2 * atr) if atr else support
            stop = min(stop, price - 0.0001)

            risk = max(price - stop, price * 0.0001)
            target1 = resistance if resistance > price else price + risk * 2
            target2 = price + risk * 3

        else:  # SHORT

            stop = min(resistance, price + 2 * atr) if atr else resistance
            stop = max(stop, price + 0.0001)

            risk = max(stop - price, price * 0.0001)
            target1 = support if support < price else price - risk * 2
            target2 = price - risk * 3

        risk_reward = round(abs(target1 - price) / risk, 2) if risk else 0.0

        return {
            "stop": cls._price_round(stop, price),
            "target1": cls._price_round(target1, price),
            "target2": cls._price_round(target2, price),
            "risk": cls._price_round(risk, price),
            "risk_reward": risk_reward,
        }

    @classmethod
    def _screen_one(cls, symbol, period):

        try:
            kwargs = {"period": period} if period else {}
            trace, _ = cls.STRATEGY.run_symbol(symbol, **kwargs)

            if trace:
                description, state, event_time = cls.STRATEGY.describe(trace)
                last = trace[-1]
                return symbol, {
                    "state": state,
                    "description": description,
                    "price": last["price"],
                    "rsi": round(last["rsi"], 2),
                    "event_time": event_time,
                }

            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

        except Exception:
            return symbol, {"state": "NONE", "description": "", "price": None, "rsi": None, "event_time": None}

    @classmethod
    def screen_states(cls, symbols, period=None):

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol, period) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states

    @classmethod
    def screen(cls, symbols, period=None):

        states = cls.screen_states(symbols, period=period)

        return {
            symbol: cls.STRATEGY.STATE_LABELS.get(info["state"], "⚪ Watching")
            for symbol, info in states.items()
        }


class DailyRSIDivergenceStatusService(RSIDivergenceStatusService):
    STRATEGY = DailyRSIDivergenceStrategy


class WeeklyStockRSIDivergenceStatusService(RSIDivergenceStatusService):
    """
    BETA - see analysis/rsi_divergence_strategy.py's
    WeeklyStockRSIDivergenceStrategy for the recalibrated thresholds
    and analysis/backtester.py's backtest_weekly_stock_divergence for
    the honest (negative) backtest this shipped with anyway.
    """

    STRATEGY = WeeklyStockRSIDivergenceStrategy


class StockRSIDivergenceStatusService(RSIDivergenceStatusService):
    """
    BETA - Daily sibling of WeeklyStockRSIDivergenceStatusService (see
    StockRSIDivergenceStrategy). Real, positive backtest across both US
    (60.7% win rate, +0.92% avg return, n=215) and India (58.4% win
    rate, +0.20% avg return, n=468) - see
    analysis/backtester.py's backtest_stock_divergence.
    """

    STRATEGY = StockRSIDivergenceStrategy


class Crypto5mRSIDivergenceStatusService(RSIDivergenceStatusService):
    """BETA, crypto only - see Crypto5mRSIDivergenceStrategy."""

    STRATEGY = Crypto5mRSIDivergenceStrategy


class Crypto15mRSIDivergenceStatusService(RSIDivergenceStatusService):
    """BETA, crypto only - see Crypto15mRSIDivergenceStrategy."""

    STRATEGY = Crypto15mRSIDivergenceStrategy


class Crypto1mRSIDivergenceStatusService(RSIDivergenceStatusService):
    """BETA, UNVALIDATED, crypto only - see Crypto1mRSIDivergenceStrategy. Can never be backtested (yfinance keeps only 7-8 days of 1m data)."""

    STRATEGY = Crypto1mRSIDivergenceStrategy
