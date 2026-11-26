"""Replay archived market snapshots through router variants and mark trades with later real quotes.

Usage (inside the backend directory or container):
    python -m tools.replay_router snapshots.jsonl.xz

``snapshots.jsonl.xz`` holds one JSON object per cycle: ``recorded_at``, ``equity``
and ``markets`` (the ``market_snapshots`` audit payload). Build it from
``data/archive/*/audit.jsonl.xz``.

Method, per variant:
* each cycle routes every scanned market, applies the real risk gates and the
  real Monte Carlo stress test, and opens the highest-confidence survivor;
* one open structure per symbol (the live position lock);
* entries fill at the natural price (pay the ask, receive the bid) and exits
  at the natural price too, so spreads are charged twice;
* a position is marked whenever all of its legs appear in a later snapshot and
  exits on the live rules: +25% / -20% of defined risk, or 5 days to expiry;
* positions never quoted again are reported as unresolved, not guessed.
"""

from __future__ import annotations

import argparse
import json
import lzma
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import risk as risk_module
import strategy as strategy_module
from config import Settings
from models import AccountSnapshot, MarketSnapshot, StrategyKind, TradePlan
from risk import RiskConstitution
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter


class _ReplayDatetime(datetime):
    current: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return cls.current or datetime.now(tz)


class _ReplayDate(date):
    current: date | None = None

    @classmethod
    def today(cls):  # noqa: ANN206
        return cls.current or date.today()


# The gates read the wall clock (quote age, days to expiry); replay pins it to each snapshot.
risk_module.datetime = _ReplayDatetime
strategy_module.date = _ReplayDate


@dataclass
class Trade:
    symbol: str
    strategy: str
    opened_at: str
    expiration: str
    legs: list[tuple[str, int]]           # (OCC symbol, +1 bought / -1 sold)
    quantity: int
    entry_cash: float                     # negative = paid
    max_loss: float
    closed_at: str | None = None
    pnl: float | None = None
    reason: str = ""


@dataclass
class Result:
    name: str
    outcomes: dict[str, int] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    def count(self, key: str) -> None:
        self.outcomes[key] = self.outcomes.get(key, 0) + 1


def natural(quotes: dict, legs: list[tuple[str, int]], opening: bool) -> float | None:
    """Cash per structure: opening buys pay the ask, closing them receives the bid."""
    cash = 0.0
    for symbol, direction in legs:
        quote = quotes.get(symbol)
        if not quote:
            return None
        buying = (direction > 0) == opening
        cash += (-quote["ask"] if buying else quote["bid"]) * 100
    return cash


def replay(cycles, router: StrategyRouter, settings: Settings, name: str,
           take: float | None = 0.25, stop: float | None = 0.20, exit_dte: int = 5) -> Result:
    """``cycles`` may be a generator; a cycle may carry prebuilt MarketSnapshots and its own ``quotes``."""
    risk = RiskConstitution(settings)
    simulator = MonteCarloSimulator(settings.credit_vol_factor)
    result = Result(name)
    open_trades: dict[str, Trade] = {}
    for cycle in cycles:
        now = cycle["recorded_at"]
        markets = [item if isinstance(item, MarketSnapshot) else MarketSnapshot.model_validate(item)
                   for item in cycle["markets"]]
        quotes = cycle.get("quotes") or {c.symbol: {"bid": c.bid, "ask": c.ask} for m in markets for c in m.contracts}
        today = date.fromisoformat(now[:10])
        _ReplayDatetime.current = datetime.fromisoformat(now)
        _ReplayDate.current = _ReplayDate.fromisoformat(now[:10])

        for symbol, trade in list(open_trades.items()):
            exit_cash = natural(quotes, trade.legs, opening=False)
            if exit_cash is None:
                continue
            pnl = (trade.entry_cash + exit_cash) * trade.quantity
            dte = (date.fromisoformat(trade.expiration) - today).days
            reason = ("profit" if take is not None and pnl >= take * trade.max_loss else
                      "loss" if stop is not None and pnl <= -stop * trade.max_loss else
                      "expiry" if dte <= exit_dte else "")
            if reason:
                trade.closed_at, trade.pnl, trade.reason = now, round(pnl, 2), reason
                del open_trades[symbol]

        equity = float(cycle.get("equity") or 100_000)
        account = AccountSnapshot(account_id="replay", equity=equity, last_equity=equity, cash=equity,
                                  options_buying_power=equity, portfolio_value=equity,
                                  open_positions=len(open_trades),
                                  open_structures=sum(t.quantity for t in open_trades.values()),
                                  open_risk=sum(t.max_loss for t in open_trades.values()))
        survivors: list[tuple[TradePlan, MarketSnapshot]] = []
        for market in markets:
            plan = router.route(market, account, settings.max_risk_per_trade_pct)
            if plan.strategy == StrategyKind.NO_TRADE:
                reason = (plan.no_trade_reason or "").lower()
                result.count("no trade: " + ("bearish off" if "bearish" in reason else
                                             "over budget" if "budget" in reason else
                                             "no liquid structure" if "chain could not" in reason else "other"))
                continue
            if market.symbol in open_trades:
                result.count("blocked: position lock")
                continue
            failed = [g.name for g in risk.evaluate(plan, account, market, False)
                      if not g.passed and g.severity == "block"]
            if failed:
                result.count("blocked: " + failed[0])
                continue
            simulation = simulator.simulate(market, plan, 1_000)
            floor = settings.expected_pnl_floor(plan.strategy.value, plan.symbol, plan.max_loss)
            if not (simulation.expected_pnl > floor and simulation.probability_profit >= 0.35
                    and simulation.probability_near_max_loss <= 0.60):
                result.count("blocked: stress test")
                continue
            survivors.append((plan, market))
        if not survivors:
            continue
        plan, market = max(survivors, key=lambda item: item[0].confidence)
        legs = [(leg.symbol, 1 if leg.side == "buy" else -1) for leg in plan.legs]
        entry = natural(quotes, legs, opening=True)
        if entry is None:
            continue
        result.count("opened")
        trade = Trade(market.symbol, plan.strategy.value, now, plan.legs[0].expiration, legs,
                      plan.quantity, entry, plan.max_loss)
        result.trades.append(trade)
        open_trades[market.symbol] = trade
    return result


def summary(result: Result) -> dict:
    closed = [t for t in result.trades if t.pnl is not None]
    pnls = [t.pnl for t in closed]
    by_strategy: dict[str, list[float]] = {}
    for trade in closed:
        by_strategy.setdefault(trade.strategy, []).append(trade.pnl)
    return {
        "variant": result.name,
        "outcomes": dict(sorted(result.outcomes.items(), key=lambda item: -item[1])),
        "opened": len(result.trades),
        "closed": len(closed),
        "unresolved": len(result.trades) - len(closed),
        "total_pnl": round(sum(pnls), 2),
        "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 3) if pnls else None,
        "mean_pnl": round(statistics.mean(pnls), 2) if pnls else None,
        "by_strategy": {k: {"n": len(v), "pnl": round(sum(v), 2)} for k, v in by_strategy.items()},
        "exit_reasons": {r: sum(t.reason == r for t in closed) for r in ("profit", "loss", "expiry")},
    }


def load(path: Path) -> list[dict]:
    with lzma.open(path, "rt", encoding="utf-8") as handle:
        cycles = [json.loads(line) for line in handle if line.strip()]
    return sorted(cycles, key=lambda item: item["recorded_at"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshots", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    cycles = load(args.snapshots)
    variants = {
        "baseline (Oct 27 router)": StrategyRouter(settings.bearish_spreads_enabled, settings.condor_symbols),
        "liquidity-aware legs + budget fit": StrategyRouter(
            settings.bearish_spreads_enabled, settings.condor_symbols,
            settings.min_open_interest, settings.max_bid_ask_spread_pct, fit_budget=True),
    }
    report = {"cycles": len(cycles), "first": cycles[0]["recorded_at"], "last": cycles[-1]["recorded_at"],
              "generated_at": datetime.now().isoformat(timespec="seconds"),
              "variants": [summary(replay(cycles, router, settings, name)) for name, router in variants.items()]}
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
