"""Clearly labelled synthetic fixtures for the credential-free demo."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from models import (
    AccountSnapshot,
    EquityPoint,
    MarketSnapshot,
    OptionContract,
    PricePoint,
    PositionView,
    StrategyKind,
)


def _price_history(spot: float, trend: float) -> list[PricePoint]:
    """Deterministic intraday-like points for the conspicuously labelled demo."""

    start = datetime.now(timezone.utc) - timedelta(hours=31 * 3)
    values: list[PricePoint] = []
    for index in range(32):
        progress = index / 31
        wave = ((index % 7) - 3) * 0.0015
        close = spot * (1 + trend * 0.035 * (progress - 1) + wave)
        values.append(
            PricePoint(
                timestamp=start + timedelta(hours=index * 3),
                close=round(close, 2),
                volume=1_000_000 + index * 17_500,
            )
        )
    values[-1].close = spot
    return values


def _expiry(days_out: int = 19) -> date:
    candidate = date.today() + timedelta(days=days_out)
    while candidate.weekday() != 4:
        candidate += timedelta(days=1)
    return candidate


def _occ_symbol(underlying: str, expiry: date, option_type: str, strike: float) -> str:
    marker = "C" if option_type == "call" else "P"
    strike_code = f"{int(round(strike * 1000)):08d}"
    return f"{underlying}{expiry:%y%m%d}{marker}{strike_code}"


def _contract(
    underlying: str,
    expiry: date,
    strike: float,
    option_type: str,
    bid: float,
    ask: float,
    delta: float,
    iv: float,
    oi: int,
    volume: int,
) -> OptionContract:
    return OptionContract(
        symbol=_occ_symbol(underlying, expiry, option_type, strike),
        underlying=underlying,
        expiration=expiry.isoformat(),
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        bid=bid,
        ask=ask,
        delta=delta,
        gamma=0.011 if option_type == "call" else 0.010,
        theta=-0.16,
        vega=0.31,
        implied_volatility=iv,
        open_interest=oi,
        volume=volume,
    )


def _chain(underlying: str, spot: float, iv: float) -> list[OptionContract]:
    expiry = _expiry()
    step = 5 if spot < 300 else 10
    center = round(spot / step) * step
    calls = [
        (center - 2 * step, 0.72, 13.40, 13.74, 2200, 980),
        (center - step, 0.61, 9.10, 9.38, 4700, 1850),
        (center, 0.51, 6.20, 6.42, 8100, 3400),
        (center + step, 0.36, 3.65, 3.82, 6600, 2400),
        (center + 2 * step, 0.23, 1.95, 2.08, 5200, 1900),
        (center + 3 * step, 0.12, 0.92, 1.02, 3100, 870),
    ]
    puts = [
        (center - 3 * step, -0.11, 0.84, 0.94, 3600, 920),
        (center - 2 * step, -0.21, 1.72, 1.84, 5700, 1800),
        (center - step, -0.35, 3.31, 3.48, 7200, 2700),
        (center, -0.50, 5.92, 6.16, 8600, 3800),
        (center + step, -0.62, 9.04, 9.34, 4400, 1500),
        (center + 2 * step, -0.74, 12.80, 13.18, 2600, 760),
    ]
    return [
        *[
            _contract(underlying, expiry, strike, "call", bid, ask, delta, iv, oi, volume)
            for strike, delta, bid, ask, oi, volume in calls
        ],
        *[
            _contract(underlying, expiry, strike, "put", bid, ask, delta, iv + 0.018, oi, volume)
            for strike, delta, bid, ask, oi, volume in puts
        ],
    ]


def demo_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_id="DEMO-PAPER-ACCOUNT",
        equity=103_842.17,
        last_equity=102_658.03,
        cash=89_140.22,
        options_buying_power=72_420.00,
        portfolio_value=103_842.17,
        options_trading_level=3,
        open_positions=3,
        open_risk=3_480.0,
        starting_balance=100_000.0,
        source="demo-simulated",
    )


def demo_markets() -> list[MarketSnapshot]:
    return [
        MarketSnapshot(
            symbol="SPY",
            underlying_price=651.42,
            trend_score=0.66,
            realized_volatility=0.171,
            iv_rank=41.0,
            catalyst_score=0.24,
            regime="risk-on trend",
            contracts=_chain("SPY", 651.42, 0.196),
            price_history=_price_history(651.42, 0.66),
        ),
        MarketSnapshot(
            symbol="QQQ",
            underlying_price=589.18,
            trend_score=0.08,
            realized_volatility=0.184,
            iv_rank=72.0,
            catalyst_score=0.18,
            regime="high-IV range",
            contracts=_chain("QQQ", 589.18, 0.244),
            price_history=_price_history(589.18, 0.08),
        ),
        MarketSnapshot(
            symbol="NVDA",
            underlying_price=189.64,
            trend_score=-0.58,
            realized_volatility=0.382,
            iv_rank=53.0,
            catalyst_score=0.41,
            regime="bearish volatility expansion",
            contracts=_chain("NVDA", 189.64, 0.438),
            price_history=_price_history(189.64, -0.58),
        ),
        MarketSnapshot(
            symbol="AAPL",
            underlying_price=237.85,
            trend_score=0.17,
            realized_volatility=0.229,
            iv_rank=34.0,
            catalyst_score=0.12,
            regime="indecisive",
            contracts=_chain("AAPL", 237.85, 0.252),
            price_history=_price_history(237.85, 0.17),
        ),
        MarketSnapshot(
            symbol="AMD",
            underlying_price=168.31,
            trend_score=0.12,
            realized_volatility=0.411,
            iv_rank=19.0,
            catalyst_score=0.86,
            regime="pre-catalyst compression",
            contracts=_chain("AMD", 168.31, 0.327),
            price_history=_price_history(168.31, 0.12),
        ),
    ]


def demo_positions() -> list[PositionView]:
    expiry = _expiry().isoformat()
    return [
        PositionView(
            symbol="SPY",
            strategy=StrategyKind.BULL_CALL_SPREAD,
            opened_at="Today · 10:11 ET",
            expiration=expiry,
            quantity=2,
            cost_basis=548.0,
            market_value=782.0,
            unrealized_pnl=234.0,
            max_loss=548.0,
            risk_used_pct=0.53,
            thesis_health=86,
            legs=["+2 650C", "−2 670C"],
        ),
        PositionView(
            symbol="NVDA",
            strategy=StrategyKind.BEAR_PUT_SPREAD,
            opened_at="Fri · 14:42 ET",
            expiration=expiry,
            quantity=2,
            cost_basis=426.0,
            market_value=608.0,
            unrealized_pnl=182.0,
            max_loss=426.0,
            risk_used_pct=0.41,
            thesis_health=74,
            legs=["+2 190P", "−2 180P"],
        ),
        PositionView(
            symbol="QQQ",
            strategy=StrategyKind.IRON_CONDOR,
            opened_at="Fri · 11:06 ET",
            expiration=expiry,
            quantity=1,
            cost_basis=-182.0,
            market_value=-131.0,
            unrealized_pnl=51.0,
            max_loss=818.0,
            risk_used_pct=0.79,
            thesis_health=68,
            legs=["+1 560P", "−1 570P", "−1 610C", "+1 620C"],
        ),
    ]


def demo_equity_curve() -> list[EquityPoint]:
    values = [100000, 99880, 100340, 100210, 101180, 100920, 101640, 102410, 102080, 102658, 103842]
    benchmark = [100000, 100120, 99890, 100310, 100470, 100210, 100580, 100920, 100640, 100940, 101120]
    labels = ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "Now"]
    return [EquityPoint(label=label, equity=equity, benchmark=base) for label, equity, base in zip(labels, values, benchmark)]
