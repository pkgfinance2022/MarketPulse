"""
Asset model.
"""

from dataclasses import dataclass, field

import pandas as pd

from core.summary import Summary
from core.indicator import Indicators


@dataclass(slots=True)
class Asset:

    # --------------------------------------------------
    # Identity
    # --------------------------------------------------

    name: str
    symbol: str
    category: str
    country: str = ""

    exchange: str = ""
    currency: str = ""

    # --------------------------------------------------
    # Database Fields
    # --------------------------------------------------

    watchlist: bool = False
    portfolio: bool = False
    priority: int = 3
    quantity: float = 0.0
    average_buy: float = 0.0
    target_allocation: float = 0.0
    theme: str = ""
    investment_theme: str = ""
    thesis: str = ""
    expected_cagr: float = 0.0
    time_horizon: str = ""
    next_review_date: str = ""
    exit_conditions: str = ""

    asset_class: str = ""
    industry: str = ""
    tags: str = ""

    # --------------------------------------------------
    # Raw Market Data
    # --------------------------------------------------

    data_15m: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_1h: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_1d: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_1w: pd.DataFrame = field(default_factory=pd.DataFrame)

    # --------------------------------------------------
    # Analysis
    # --------------------------------------------------

    summary: Summary = field(default_factory=Summary)
    indicators: Indicators = field(default_factory=Indicators)
    scores: dict = field(default_factory=dict)

    # --------------------------------------------------

    @property
    def latest_price(self):

        if self.summary.price is not None:
            return self.summary.price

        if self.data_15m.empty:
            return None

        return float(self.data_15m["Close"].iloc[-1])

    # --------------------------------------------------

    def __repr__(self):

        return f"Asset({self.symbol}, {self.category})"
