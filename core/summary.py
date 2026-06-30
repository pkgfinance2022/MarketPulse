"""
Summary model.

Contains calculated information
about an Asset.

Everything shown on the dashboard
comes from this object.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class Summary:

    # Latest price
    price: float | None = None

    # Percentage changes
    change_15m: float | None = None
    change_1h: float | None = None
    change_4h: float | None = None
    change_1d: float | None = None

    # Today's statistics
    high: float | None = None
    low: float | None = None
    volume: float | None = None

    # Future
    trend: str = ""
    momentum: str = ""