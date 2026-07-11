"""
TradingView deep links.

Maps this app's Yahoo-style tickers (^GDAXI, EURUSD=X, CL=F, ...) to
TradingView's symbol format, so the "Open in TradingView" button jumps
straight to the selected instrument on the user's saved chart layout
instead of always opening it as-is.

Best-effort: TradingView's exact ticker for some symbols (particularly
the NSE sectoral indices) isn't 100% guaranteed to match - if a symbol
doesn't resolve, the link still opens the saved layout, just without
auto-switching, and the user can search it manually.
"""

TRADINGVIEW_SYMBOLS = {
    # Indian Indices
    "^NSEI": "NSE:NIFTY",
    "^NSEBANK": "NSE:BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "NSE:FINNIFTY",
    "^NSEMDCP50": "NSE:NIFTYMIDCAP100",
    "^CNXIT": "NSE:NIFTYIT",
    "^CNXAUTO": "NSE:NIFTYAUTO",
    "^CNXPHARMA": "NSE:NIFTYPHARMA",
    "^CNXFMCG": "NSE:NIFTYFMCG",
    "^CNXMETAL": "NSE:NIFTYMETAL",
    "^CNXREALTY": "NSE:NIFTYREALTY",
    "^CNXENERGY": "NSE:NIFTYENERGY",
    "^CNXINFRA": "NSE:NIFTYINFRA",
    "^CNXPSUBANK": "NSE:NIFTYPSUBANK",
    "^CNXMEDIA": "NSE:NIFTYMEDIA",
    "^CNXCONSUM": "NSE:NIFTYCONSUMPTION",

    # US Indices
    "^GSPC": "TVC:SPX",
    "^DJI": "TVC:DJI",
    "^RUT": "TVC:RUT",
    "^NDX": "TVC:NDX",

    # European Indices
    "^GDAXI": "TVC:DAX",
    "^FTSE": "TVC:UKX",
    "^FCHI": "TVC:CAC",
    "^AEX": "TVC:AEX",
    "^BIT40P": "TVC:ITA40",

    # Asian Indices
    "^N225": "TVC:NI225",
    "^HSI": "TVC:HSI",
    "^KS11": "TVC:KOSPI",
    "^STI": "TVC:STI",

    # Commodities
    "GC=F": "TVC:GOLD",
    "SI=F": "TVC:SILVER",
    "HG=F": "COMEX:HG1!",
    "ALI=F": "LME:ALI1!",
    "CL=F": "TVC:USOIL",

    # Currencies
    "DX-Y.NYB": "TVC:DXY",
    "EURUSD=X": "FX:EURUSD",
    "GBPUSD=X": "FX:GBPUSD",
    "JPY=X": "FX:USDJPY",
    "USDCHF=X": "FX:USDCHF",
    "USDCAD=X": "FX:USDCAD",
    "AUDUSD=X": "FX:AUDUSD",
    "NZDUSD=X": "FX:NZDUSD",
}


def tradingview_url(base_url, ticker):

    symbol = TRADINGVIEW_SYMBOLS.get(ticker)

    if not symbol:
        return base_url

    separator = "&" if "?" in base_url else "?"

    return f"{base_url}{separator}symbol={symbol}"
