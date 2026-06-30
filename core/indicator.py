"""
Technical Indicator Model
"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class IndicatorSet:
    """
    Indicators for ONE timeframe.
    """

    timeframe: str

    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None

    rsi14: float | None = None

    trend: str = ""
    momentum: str = ""


@dataclass(slots=True)
class Indicators:
    """
    Holds all timeframe indicators.
    """

    m15: IndicatorSet = field(
        default_factory=lambda: IndicatorSet("15m")
    )

    h1: IndicatorSet = field(
        default_factory=lambda: IndicatorSet("1H")
    )

    d1: IndicatorSet = field(
        default_factory=lambda: IndicatorSet("1D")
    )

    w1: IndicatorSet = field(
        default_factory=lambda: IndicatorSet("1W")
    )