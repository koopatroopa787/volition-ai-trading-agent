"""Curated, liquid market proxies for the lightweight market pulse."""

from __future__ import annotations


MARKET_UNIVERSE: tuple[dict[str, object], ...] = (
    # Broad US benchmarks. Alpaca stock data provides these tradable ETFs, not cash indices.
    {"symbol": "SPY", "label": "S&P 500", "group": "Indices", "proxy_for": "S&P 500 ETF proxy", "demo_price": 648.7},
    {"symbol": "QQQ", "label": "Nasdaq-100", "group": "Indices", "proxy_for": "Nasdaq-100 ETF proxy", "demo_price": 584.2},
    {"symbol": "DIA", "label": "Dow 30", "group": "Indices", "proxy_for": "Dow Jones ETF proxy", "demo_price": 456.8},
    {"symbol": "IWM", "label": "US small caps", "group": "Indices", "proxy_for": "Russell 2000 ETF proxy", "demo_price": 238.4},
    # Liquid exchange-traded commodity proxies.
    {"symbol": "GLD", "label": "Gold", "group": "Commodities", "proxy_for": "Gold bullion ETF proxy", "demo_price": 312.3},
    {"symbol": "SLV", "label": "Silver", "group": "Commodities", "proxy_for": "Silver bullion ETF proxy", "demo_price": 35.7},
    {"symbol": "USO", "label": "Crude oil", "group": "Commodities", "proxy_for": "Oil futures ETF proxy", "demo_price": 77.6},
    {"symbol": "UNG", "label": "Natural gas", "group": "Commodities", "proxy_for": "Natural-gas futures ETF proxy", "demo_price": 14.2},
    {"symbol": "DBA", "label": "Agriculture", "group": "Commodities", "proxy_for": "Agricultural commodity ETF proxy", "demo_price": 26.1},
    # Cross-asset context that often explains equity moves.
    {"symbol": "TLT", "label": "Long Treasuries", "group": "Macro & risk", "proxy_for": "20+ year Treasury ETF", "demo_price": 88.5},
    {"symbol": "IEF", "label": "Treasuries", "group": "Macro & risk", "proxy_for": "7–10 year Treasury ETF", "demo_price": 96.8},
    {"symbol": "HYG", "label": "High-yield credit", "group": "Macro & risk", "proxy_for": "High-yield bond ETF", "demo_price": 80.4},
    {"symbol": "UUP", "label": "US dollar", "group": "Macro & risk", "proxy_for": "US Dollar Index ETF proxy", "demo_price": 27.9},
    {"symbol": "VIXY", "label": "Volatility", "group": "Macro & risk", "proxy_for": "Short-term VIX futures ETF proxy", "demo_price": 31.6},
    # Sector breadth.
    {"symbol": "XLK", "label": "Technology", "group": "Sectors", "proxy_for": "S&P technology sector ETF", "demo_price": 282.1},
    {"symbol": "XLF", "label": "Financials", "group": "Sectors", "proxy_for": "S&P financial sector ETF", "demo_price": 52.4},
    {"symbol": "XLE", "label": "Energy", "group": "Sectors", "proxy_for": "S&P energy sector ETF", "demo_price": 91.7},
    {"symbol": "XLV", "label": "Health care", "group": "Sectors", "proxy_for": "S&P health-care sector ETF", "demo_price": 142.9},
    {"symbol": "XLI", "label": "Industrials", "group": "Sectors", "proxy_for": "S&P industrial sector ETF", "demo_price": 146.3},
    {"symbol": "XLY", "label": "Consumer discretionary", "group": "Sectors", "proxy_for": "S&P discretionary sector ETF", "demo_price": 218.6},
    {"symbol": "XLP", "label": "Consumer staples", "group": "Sectors", "proxy_for": "S&P staples sector ETF", "demo_price": 83.2},
    {"symbol": "XLU", "label": "Utilities", "group": "Sectors", "proxy_for": "S&P utilities sector ETF", "demo_price": 85.4},
    {"symbol": "XLB", "label": "Materials", "group": "Sectors", "proxy_for": "S&P materials sector ETF", "demo_price": 95.5},
    {"symbol": "XLRE", "label": "Real estate", "group": "Sectors", "proxy_for": "S&P real-estate sector ETF", "demo_price": 43.6},
    # Highly liquid leaders used to read market leadership and concentration.
    {"symbol": "NVDA", "label": "Nvidia", "group": "Leaders", "proxy_for": "Semiconductor leader", "demo_price": 182.4},
    {"symbol": "AAPL", "label": "Apple", "group": "Leaders", "proxy_for": "Mega-cap technology", "demo_price": 231.9},
    {"symbol": "MSFT", "label": "Microsoft", "group": "Leaders", "proxy_for": "Mega-cap technology", "demo_price": 517.2},
    {"symbol": "AMZN", "label": "Amazon", "group": "Leaders", "proxy_for": "Consumer and cloud leader", "demo_price": 228.5},
    {"symbol": "META", "label": "Meta", "group": "Leaders", "proxy_for": "Digital advertising leader", "demo_price": 751.8},
    {"symbol": "GOOGL", "label": "Alphabet", "group": "Leaders", "proxy_for": "Search and cloud leader", "demo_price": 211.6},
    {"symbol": "TSLA", "label": "Tesla", "group": "Leaders", "proxy_for": "High-beta consumer leader", "demo_price": 349.3},
    {"symbol": "AMD", "label": "AMD", "group": "Leaders", "proxy_for": "Semiconductor leader", "demo_price": 168.7},
    {"symbol": "AVGO", "label": "Broadcom", "group": "Leaders", "proxy_for": "Semiconductor and infrastructure leader", "demo_price": 331.5},
    {"symbol": "JPM", "label": "JPMorgan", "group": "Leaders", "proxy_for": "Money-center banking leader", "demo_price": 301.2},
    {"symbol": "BAC", "label": "Bank of America", "group": "Leaders", "proxy_for": "Large-cap banking leader", "demo_price": 52.1},
    {"symbol": "XOM", "label": "Exxon Mobil", "group": "Leaders", "proxy_for": "Integrated energy leader", "demo_price": 113.8},
    {"symbol": "UNH", "label": "UnitedHealth", "group": "Leaders", "proxy_for": "Managed-care leader", "demo_price": 298.6},
    {"symbol": "LLY", "label": "Eli Lilly", "group": "Leaders", "proxy_for": "Large-cap pharmaceutical leader", "demo_price": 744.3},
    {"symbol": "NFLX", "label": "Netflix", "group": "Leaders", "proxy_for": "Streaming-media leader", "demo_price": 1208.4},
    {"symbol": "COIN", "label": "Coinbase", "group": "Leaders", "proxy_for": "Crypto-market equity proxy", "demo_price": 312.7},
)

MARKET_GROUPS = ("Indices", "Commodities", "Macro & risk", "Sectors", "Leaders")
