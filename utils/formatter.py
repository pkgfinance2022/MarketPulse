"""
UI Formatter
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
        return f"🔴 {value:.0f}"

    if value >= 55:
        return f"🟢 {value:.0f}"

    if value >= 45:
        return f"🟡 {value:.0f}"

    return f"🔵 {value:.0f}"


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


def change(value):

    if value is None:
        return "⚪ --"

    if value > 0:
        return f"🟢 +{value:.2f}%"

    if value < 0:
        return f"🔴 {value:.2f}%"

    return "🟡 0.00%"