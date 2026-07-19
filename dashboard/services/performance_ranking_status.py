"""
Weekly/Monthly performance ranking status service.

Screens every asset across all four sources (Global Indices, US
Stocks, Indian Stocks, Crypto) for their week/month % return - see
analysis/performance_ranking.py. Stateless, same pattern as
EMAProximityStatusService.
"""

from concurrent.futures import ThreadPoolExecutor

from analysis.performance_ranking import check_performance

SCREEN_WORKERS = 3   # kept low - see rsi_wave_status.py's own reasoning (Streamlit Community Cloud's thread limit)


class PerformanceRankingStatusService:

    @staticmethod
    def _screen_one(symbol):

        try:
            return symbol, check_performance(symbol)
        except Exception:
            return symbol, {"week_pct": None, "month_pct": None}

    @classmethod
    def screen_states(cls, symbols):

        states = {}

        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as executor:

            futures = [executor.submit(cls._screen_one, symbol) for symbol in symbols]

            for future in futures:
                symbol, info = future.result()
                states[symbol] = info

        return states
