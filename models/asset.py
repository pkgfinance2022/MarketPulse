"""
Dashboard-facing asset model.

This is the typed boundary between analysis engines and Streamlit widgets.
"""

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class AssetModel:

    ticker: str = ""
    name: str = ""
    market: str = ""
    country: str = ""
    sector: str = ""
    industry: str = ""

    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    market_cap: float = 0.0

    ema20: float = 0.0
    ema50: float = 0.0
    ema200: float = 0.0
    trend: str = "Neutral"

    rsi: float = 0.0
    macd: float = 0.0
    adx: float = 0.0
    atr: float = 0.0

    support: float = 0.0
    resistance: float = 0.0
    stop_loss: float = 0.0

    technical_score: int = 0
    fundamental_score: int = 50
    momentum_score: int = 0
    valuation_score: int = 50
    risk_score: int = 0
    news_score: int = 50
    macro_score: int = 50
    ai_score: int = 0

    signal: str = "HOLD"
    confidence: int = 0
    risk: str = "MEDIUM"
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    portfolio: bool = False
    quantity: float = 0.0
    average_buy: float = 0.0
    watchlist: bool = False
    priority: int = 3
    target_allocation: float = 0.0
    theme: str = ""

    investment_theme: str = ""
    thesis: str = ""
    expected_cagr: float = 0.0
    time_horizon: str = ""
    next_review_date: str = ""
    exit_conditions: str = ""

    change_15m: float = 0.0
    change_1h: float = 0.0
    change_1d: float = 0.0

    def to_dict(self):

        data = asdict(self)

        return {
            "Ticker": data["ticker"],
            "Name": data["name"],
            "Market": data["market"],
            "Country": data["country"],
            "Sector": data["sector"],
            "Industry": data["industry"],
            "Price": data["price"],
            "Change %": data["change_pct"],
            "Volume": data["volume"],
            "Market Cap": data["market_cap"],
            "EMA20": data["ema20"],
            "EMA50": data["ema50"],
            "EMA200": data["ema200"],
            "Trend": data["trend"],
            "RSI": data["rsi"],
            "MACD": data["macd"],
            "ADX": data["adx"],
            "ATR": data["atr"],
            "Support": data["support"],
            "Resistance": data["resistance"],
            "Stop Loss": data["stop_loss"],
            "Technical Score": data["technical_score"],
            "Fundamental Score": data["fundamental_score"],
            "Momentum Score": data["momentum_score"],
            "Valuation Score": data["valuation_score"],
            "Risk Score": data["risk_score"],
            "News Score": data["news_score"],
            "Macro Score": data["macro_score"],
            "AI Score": data["ai_score"],
            "Score": data["ai_score"],
            "Signal": data["signal"],
            "Confidence": data["confidence"],
            "Risk": data["risk"],
            "Reasons": data["reasons"],
            "Risks": data["risks"],
            "Portfolio": data["portfolio"],
            "Quantity": data["quantity"],
            "Average Buy": data["average_buy"],
            "Watchlist": data["watchlist"],
            "Priority": data["priority"],
            "Target Allocation": data["target_allocation"],
            "Theme": data["theme"],
            "Investment Theme": data["investment_theme"],
            "Thesis": data["thesis"],
            "Expected CAGR": data["expected_cagr"],
            "Time Horizon": data["time_horizon"],
            "Next Review Date": data["next_review_date"],
            "Exit Conditions": data["exit_conditions"],
            "15m %": data["change_15m"],
            "1H %": data["change_1h"],
            "1D %": data["change_1d"],
        }

    @classmethod
    def from_dict(cls, row):

        return cls(
            ticker=row.get("Ticker", ""),
            name=row.get("Name", ""),
            market=row.get("Market", ""),
            country=row.get("Country", ""),
            sector=row.get("Sector", ""),
            industry=row.get("Industry", ""),
            price=row.get("Price", 0.0),
            change_pct=row.get("Change %", 0.0),
            volume=row.get("Volume", 0),
            market_cap=row.get("Market Cap", 0.0),
            ema20=row.get("EMA20", 0.0),
            ema50=row.get("EMA50", 0.0),
            ema200=row.get("EMA200", 0.0),
            trend=row.get("Trend", "Neutral"),
            rsi=row.get("RSI", 0.0),
            macd=row.get("MACD", 0.0),
            adx=row.get("ADX", 0.0),
            atr=row.get("ATR", 0.0),
            support=row.get("Support", 0.0),
            resistance=row.get("Resistance", 0.0),
            stop_loss=row.get("Stop Loss", 0.0),
            technical_score=row.get("Technical Score", 0),
            fundamental_score=row.get("Fundamental Score", 50),
            momentum_score=row.get("Momentum Score", 0),
            valuation_score=row.get("Valuation Score", 50),
            risk_score=row.get("Risk Score", 0),
            news_score=row.get("News Score", 50),
            macro_score=row.get("Macro Score", 50),
            ai_score=row.get("AI Score", 0),
            signal=row.get("Signal", "HOLD"),
            confidence=row.get("Confidence", 0),
            risk=row.get("Risk", "MEDIUM"),
            reasons=row.get("Reasons", []),
            risks=row.get("Risks", []),
            portfolio=row.get("Portfolio", False),
            quantity=row.get("Quantity", 0.0),
            average_buy=row.get("Average Buy", 0.0),
            watchlist=row.get("Watchlist", False),
            priority=row.get("Priority", 3),
            target_allocation=row.get("Target Allocation", 0.0),
            theme=row.get("Theme", ""),
            investment_theme=row.get("Investment Theme", ""),
            thesis=row.get("Thesis", ""),
            expected_cagr=row.get("Expected CAGR", 0.0),
            time_horizon=row.get("Time Horizon", ""),
            next_review_date=row.get("Next Review Date", ""),
            exit_conditions=row.get("Exit Conditions", ""),
            change_15m=row.get("15m %", 0.0),
            change_1h=row.get("1H %", 0.0),
            change_1d=row.get("1D %", 0.0),
        )
