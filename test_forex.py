import yfinance as yf

symbols = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "USDCHF=X",
    "USDCAD=X",
    "AUDUSD=X",
    "NZDUSD=X",
    "DX-Y.NYB",
]

for s in symbols:

    print("=" * 40)
    print(s)

    df = yf.download(
        s,
        period="5d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    print(df.tail())