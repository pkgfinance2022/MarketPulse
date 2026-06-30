"""
Market Pulse

Application Entry Point
"""

from core.loader import AssetLoader
from services.market_service import MarketService
from services.summary_service import SummaryService
from services.indicator_service import IndicatorService


def fmt(value, decimals=2):
    """Safely format values for display."""

    if value is None:
        return "-"

    if isinstance(value, float):
        return f"{value:.{decimals}f}"

    return str(value)


def print_header():

    print()
    print("=" * 190)
    print("MARKET PULSE")
    print("=" * 190)


def print_summary(repo):

    print()
    print("=" * 190)
    print("MARKET SUMMARY")
    print("=" * 190)

    print(
        f"{'Asset':20}"
        f"{'Price':>12}"
        f"{'15m RSI':>10}"
        f"{'1H RSI':>10}"
        f"{'1D RSI':>10}"
        f"{'15m Trend':>15}"
        f"{'1H Trend':>15}"
        f"{'1D Trend':>15}"
    )

    print("-" * 190)

    for asset in repo.all():

        SummaryService.build(asset)
        IndicatorService.build(asset)

        print(
            f"{asset.name:20}"
            f"{fmt(asset.summary.price):>12}"
            f"{fmt(asset.indicators.m15.rsi14):>10}"
            f"{fmt(asset.indicators.h1.rsi14):>10}"
            f"{fmt(asset.indicators.d1.rsi14):>10}"
            f"{asset.indicators.m15.trend:>15}"
            f"{asset.indicators.h1.trend:>15}"
            f"{asset.indicators.d1.trend:>15}"
        )

    print()
    print(f"Assets Loaded : {repo.count()}")


def main():

    print_header()

    loader = AssetLoader()

    assets = loader.all_assets()

    print(f"Assets Found : {len(assets)}")

    service = MarketService()

    repository = service.load_market(assets)

    print_summary(repository)


if __name__ == "__main__":
    main()