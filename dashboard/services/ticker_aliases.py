"""
Common CFD/broker-style ticker nicknames, resolved to Yahoo Finance
symbols - lets a user type "FRA40" or "GER40" instead of needing to
know the underlying ^FCHI/^GDAXI ticker. Anything not listed here is
passed straight through (uppercased) so a raw Yahoo ticker (^GDAXI,
AAPL, BTC-USD, ...) still works untouched.
"""

TICKER_ALIASES = {
    "FRA40": "^FCHI",
    "CAC40": "^FCHI",
    "GER40": "^GDAXI",
    "DAX40": "^GDAXI",
    "DAX": "^GDAXI",
    "UK100": "^FTSE",
    "FTSE100": "^FTSE",
    "US30": "^DJI",
    "DOW": "^DJI",
    "US100": "^NDX",
    "NAS100": "^NDX",
    "US500": "^GSPC",
    "SPX500": "^GSPC",
    "SPX": "^GSPC",
    "JPN225": "^N225",
    "JP225": "^N225",
    "NIKKEI": "^N225",
    "HK50": "^HSI",
}


def resolve_ticker(raw):
    symbol = raw.strip().upper()
    return TICKER_ALIASES.get(symbol, symbol)
