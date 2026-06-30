"""
Dashboard Formatters
"""


def trend(value):

    if value == "Bullish":
        return "🟢 Bullish"

    if value == "Bearish":
        return "🔴 Bearish"

    return "🟡 Neutral"


def rsi(value):

    if value is None:
        return "⚪ --"

    if value >= 70:
        return f"🔴 {value:.1f}"

    if value >= 55:
        return f"🟢 {value:.1f}"

    if value >= 45:
        return f"🟡 {value:.1f}"

    return f"🔵 {value:.1f}"


def score(value):

    if value is None:
        return "⚪ --"

    if value >= 80:
        return f"🟢 {value}"

    if value >= 60:
        return f"🟡 {value}"

    return f"🔴 {value}"


def price(value):

    if value is None:
        return "--"

    return f"{value:,.2f}"