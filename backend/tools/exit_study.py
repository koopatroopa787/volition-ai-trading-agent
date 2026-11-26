"""Walk-forward study of exit rules on every gate-approved plan in archived snapshots.

Usage: python -m tools.exit_study snapshots.jsonl.xz [--split 2026-11-05]

Each plan the live router and risk gates would approve becomes one sample (at
most one per symbol per day, ignoring portfolio caps so the sample is not
shaped by earlier trades). Its legs are re-marked in every later snapshot that
quotes all of them. An exit rule (take-profit, stop-loss as fractions of the
defined risk, plus the live 5-day expiry exit) is applied to that path; a path
that never triggers is closed at its last mark. Paths with no later marks are
excluded and counted. Rules are ranked on samples entered before ``--split``
and the winner is reported on the later, unseen samples.

Two fill models bracket reality: ``mid`` (live limit orders at mid, optimistic)
and ``natural`` (pay the ask, sell the bid, pessimistic).
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from datetime import date, datetime

from config import Settings
from models import AccountSnapshot, MarketSnapshot, StrategyKind
from risk import RiskConstitution
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter
from tools.replay_router import _ReplayDate, _ReplayDatetime, load

PORTFOLIO_GATES = {"position count", "open structure count", "portfolio open risk", "existing position lock"}
TAKES = (0.25, 0.5, 0.75, None)
STOPS = (0.2, 0.35, 0.5, None)


def cash(quotes: dict, legs: list[tuple[str, int]], opening: bool, fill: str) -> float | None:
    total = 0.0
    for symbol, direction in legs:
        quote = quotes.get(symbol)
        if not quote:
            return None
        buying = (direction > 0) == opening
        price = (quote["bid"] + quote["ask"]) / 2 if fill == "mid" else quote["ask"] if buying else quote["bid"]
        total += (-price if buying else price) * 100
    return total


def collect(cycles: list[dict], router: StrategyRouter, settings: Settings) -> list[dict]:
    risk = RiskConstitution(settings)
    simulator = MonteCarloSimulator(settings.credit_vol_factor)
    samples, seen = [], set()
    for cycle in cycles:
        now = cycle["recorded_at"]
        _ReplayDatetime.current = datetime.fromisoformat(now)
        _ReplayDate.current = _ReplayDate.fromisoformat(now[:10])
        equity = float(cycle.get("equity") or 100_000)
        account = AccountSnapshot(account_id="study", equity=equity, last_equity=equity, cash=equity,
                                  options_buying_power=equity, portfolio_value=equity)
        quotes = {c["symbol"]: c for m in cycle["markets"] for c in m["contracts"]}
        for raw in cycle["markets"]:
            if (raw["symbol"], now[:10]) in seen:
                continue
            market = MarketSnapshot.model_validate(raw)
            plan = router.route(market, account, settings.max_risk_per_trade_pct)
            if plan.strategy == StrategyKind.NO_TRADE:
                continue
            if any(not g.passed and g.severity == "block" and g.name not in PORTFOLIO_GATES
                   for g in risk.evaluate(plan, account, market, False)):
                continue
            sim = simulator.simulate(market, plan, 1_000)
            if not (sim.expected_pnl > settings.expected_pnl_floor(plan.strategy.value, plan.symbol, plan.max_loss)
                    and sim.probability_profit >= 0.35 and sim.probability_near_max_loss <= 0.60):
                continue
            legs = [(leg.symbol, 1 if leg.side == "buy" else -1) for leg in plan.legs]
            seen.add((raw["symbol"], now[:10]))
            samples.append({"symbol": raw["symbol"], "strategy": plan.strategy.value, "opened_at": now,
                            "expiration": plan.legs[0].expiration, "legs": legs,
                            "entry": {fill: cash(quotes, legs, True, fill) for fill in ("mid", "natural")},
                            "path": []})
        for sample in samples:
            if sample["opened_at"] < now:
                marks = {fill: cash(quotes, sample["legs"], False, fill) for fill in ("mid", "natural")}
                if marks["mid"] is not None:
                    sample["path"].append((now, marks))
    return [s for s in samples if s["entry"]["mid"] is not None]


def outcome(sample: dict, take: float | None, stop: float | None, fill: str) -> float:
    entry = sample["entry"][fill]
    risk = -entry if entry < 0 else abs(entry)          # debit structures: risk is the premium paid
    pnl = 0.0
    for when, marks in sample["path"]:
        pnl = entry + marks[fill]
        dte = (date.fromisoformat(sample["expiration"]) - date.fromisoformat(when[:10])).days
        if (take is not None and pnl >= take * risk) or (stop is not None and pnl <= -stop * risk) or dte <= 5:
            break
    return pnl / risk                                   # return on defined risk, so trades of any size compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshots")
    parser.add_argument("--split", default="2026-11-05")
    args = parser.parse_args()
    settings = Settings()
    router = StrategyRouter(settings.bearish_spreads_enabled, settings.condor_symbols,
                            settings.min_open_interest, settings.max_bid_ask_spread_pct, fit_budget=True)
    samples = collect(load(args.snapshots), router, settings)
    marked = [s for s in samples if s["path"]]
    train = [s for s in marked if s["opened_at"] < args.split]
    test = [s for s in marked if s["opened_at"] >= args.split]
    report = {"samples": len(samples), "with_later_marks": len(marked), "train": len(train), "test": len(test),
              "split": args.split, "fills": {}}
    for fill in ("mid", "natural"):
        rows = []
        for take, stop in itertools.product(TAKES, STOPS):
            train_r = [outcome(s, take, stop, fill) for s in train]
            test_r = [outcome(s, take, stop, fill) for s in test]
            rows.append({"take": take, "stop": stop,
                         "train_mean": round(statistics.mean(train_r), 4) if train_r else None,
                         "test_mean": round(statistics.mean(test_r), 4) if test_r else None,
                         "test_win": round(sum(r > 0 for r in test_r) / len(test_r), 3) if test_r else None})
        live = next(r for r in rows if r["take"] == 0.25 and r["stop"] == 0.2)
        best = max(rows, key=lambda r: r["train_mean"] if r["train_mean"] is not None else -9)
        report["fills"][fill] = {"live_rule": live, "best_on_train": best,
                                 "grid": sorted(rows, key=lambda r: -(r["train_mean"] or -9))}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
