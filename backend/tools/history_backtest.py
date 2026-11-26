"""Long-history backtest: rebuild the bot's daily market view from Alpaca bars and replay it.

Usage (backend directory; history from ``tools.history_pull``):
    VOLITION_SENTIMENT_MODEL=../research/news_sentiment/onnx python -m tools.history_backtest HISTORY_DIR --output report.json

For every trading day since March 2024 and every symbol, it rebuilds what the live bot sees at
the close, then runs the real router, risk gates and Monte Carlo stress test through
``tools.replay_router.replay``:
* trend, realized volatility, IV rank and regime come from the live formulas on daily closes;
* the chain is the weekly expiry the bot would target that day (strikes within 16% of spot).
  Implied volatility and greeks are solved with Black-Scholes from each contract's daily close;
* Alpaca keeps no historical option quotes, so bid/ask = close -/+ half the median live spread
  for that symbol and price level (measured on archived Oct-Nov 2026 quotes). Open interest is
  unknown historically, so the open-interest gate is neutral. Any contract that traded that day counts;
* news is the 10 latest Alpaca headlines before the close, scored like the live bot.
Positions still open at expiry settle at intrinsic value. Equity is held at $100k.

Evolution is walk-forward. Every configuration (exit rule x target DTE x bearish switch) runs over
the full period. In each expanding fold, the configuration with the best P&L on trades opened
before the fold is scored only on trades opened inside it.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import lzma
import re
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from config import Settings
from models import MarketSnapshot, OptionContract
from services import intelligence
from services.market_provider import MarketProvider
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter
from tools.replay_router import replay, summary

RATE = 0.045  # ponytail: flat risk-free rate (T-bills averaged ~4.5% over 2024-26); use a curve if it ever matters
# Median live (ask - bid) / mid by mid price: <$0.50, <$2, <$5, $5+. Archived Oct-Nov 2026 indicative quotes.
SPREAD = {"SPY": (0.093, 0.043, 0.022, 0.018), "QQQ": (0.154, 0.045, 0.023, 0.019),
          "IWM": (0.227, 0.037, 0.021, 0.017), "NVDA": (0.108, 0.035, 0.030, 0.021),
          "AAPL": (0.286, 0.119, 0.071, 0.058), "AMD": (0.154, 0.043, 0.095, 0.041)}
SPREAD_DEFAULT = (0.154, 0.043, 0.023, 0.019)  # cross-symbol median; DIA and AMD's cheap buckets were never sampled
OCC = re.compile(r"([A-Z]+)(\d{6})([CP])(\d{8})")
FOLDS = ("2025-03-01", "2025-09-01", "2026-03-01")
EXITS = list(itertools.product((0.25, 0.5, 0.75, None), (0.20, 0.35, 0.50, None)))


def _cdf(x: np.ndarray) -> np.ndarray:
    # Abramowitz-Stegun 7.1.26 (error < 1.5e-7); numpy has no erf.
    z = np.abs(x) / np.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1 - poly * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * erf)


def _bs(spot, strike, years, vol, call):
    root = vol * np.sqrt(years)
    d1 = (np.log(spot / strike) + (RATE + vol * vol / 2) * years) / root
    d2 = d1 - root
    discount = np.exp(-RATE * years)
    price = np.where(call, spot * _cdf(d1) - strike * discount * _cdf(d2),
                     strike * discount * _cdf(-d2) - spot * _cdf(-d1))
    return price, d1, d2


def greeks(spot: float, strike, years, price, call):
    """Vectorised implied volatility (bisection) and greeks; NaN where the close is below intrinsic."""
    low, high = np.full(price.shape, 0.01), np.full(price.shape, 5.0)
    for _ in range(50):
        mid = (low + high) / 2
        value, _, _ = _bs(spot, strike, years, mid, call)
        low, high = np.where(value < price, mid, low), np.where(value < price, high, mid)
    vol = (low + high) / 2
    fitted, d1, d2 = _bs(spot, strike, years, vol, call)
    vol = np.where((np.abs(fitted - price) < 0.02 + 0.01 * price) & (vol > 0.011) & (vol < 4.9), vol, np.nan)
    pdf = np.exp(-d1 * d1 / 2) / np.sqrt(2 * np.pi)
    delta = np.where(call, _cdf(d1), _cdf(d1) - 1)
    gamma = pdf / (spot * vol * np.sqrt(years))
    carry = RATE * strike * np.exp(-RATE * years) * np.where(call, _cdf(d2), -_cdf(-d2))
    theta = (-spot * pdf * vol / (2 * np.sqrt(years)) - np.where(call, carry, -carry)) / 365
    vega = spot * pdf * np.sqrt(years) / 100
    return vol, delta, gamma, theta, vega


def spread_share(symbol: str, price: float) -> float:
    table = SPREAD.get(symbol, SPREAD_DEFAULT)
    return table[0 if price < 0.5 else 1 if price < 2 else 2 if price < 5 else 3]


def quote(symbol: str, close: float, scale: float) -> tuple[float, float]:
    half = max(0.005, close * spread_share(symbol, close) / 2) * scale
    bid, ask = round(max(0.0, close - half), 2), round(close + half, 2)
    return bid, max(ask, bid + (0.01 if scale else 0.0))


class DayQuotes:
    """Bid/ask for every contract that traded that day, priced on demand from its close."""

    def __init__(self, closes: dict[str, float], scale: float) -> None:
        self.closes, self.scale = closes, scale

    def get(self, occ: str):  # noqa: ANN201
        close = self.closes.get(occ)
        if close is None:
            return None
        bid, ask = quote(OCC.match(occ).group(1), close, self.scale)
        return {"bid": bid, "ask": ask}

    def __bool__(self) -> bool:
        return True


def target_expiry(day: date, target_days: int, settings: Settings) -> date:
    """MarketProvider._target_expiry with a configurable target."""
    target_days = min(settings.max_dte, max(settings.min_dte, target_days))
    candidate = day + timedelta(days=target_days)
    while candidate.weekday() != 4:
        candidate += timedelta(days=1)
    if (candidate - day).days > settings.max_dte:
        candidate -= timedelta(days=7)
    return candidate


class History:
    def __init__(self, folder: Path) -> None:
        self.closes: dict[str, list[tuple[str, float]]] = defaultdict(list)
        with lzma.open(folder / "stocks.csv.xz", "rt", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.closes[row["symbol"]].append((row["date"], float(row["close"])))
        self.close_map = {symbol: dict(rows) for symbol, rows in self.closes.items()}
        self.bars: dict[str, dict[str, dict[str, float]]] = {}
        for path in sorted((folder / "options").glob("*.csv.xz")):
            days: dict[str, dict[str, float]] = defaultdict(dict)
            with lzma.open(path, "rt", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    days[row["date"]][row["occ"]] = float(row["close"])
            self.bars[path.name.split(".")[0]] = days
        self.news: dict[str, list[dict]] = {}
        for symbol in self.bars:
            path = folder / "news" / f"{symbol}.jsonl.xz"
            with lzma.open(path, "rt", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.news[symbol] = sorted(rows, key=lambda item: item["created_at"])
        self.scores = self._scores(folder / "sentiment_scores.json")

    def _scores(self, cache: Path) -> dict[str, tuple[float, float]]:
        """(model, lexicon) score for every headline that is ever among a day's 10 latest."""
        scores = {str(k): tuple(v) for k, v in json.loads(cache.read_text()).items()} if cache.exists() else {}
        if intelligence._sentiment_model() is None:
            raise SystemExit("Set VOLITION_SENTIMENT_MODEL to the folder with model.onnx (research/news_sentiment/onnx).")
        needed = {str(item["id"]): item for symbol in self.bars for day in self.trading_days(symbol)
                  for item in self.headlines(symbol, day)}
        missing = [item for key, item in needed.items() if key not in scores]
        for index, item in enumerate(missing):
            text = self.text(item)
            scores[str(item["id"])] = (intelligence.score_sentiment(text), intelligence.lexicon_sentiment(text))
            if index % 2000 == 1999:
                print(f"scored {index + 1}/{len(missing)} headlines", flush=True)
                cache.write_text(json.dumps(scores))
        cache.write_text(json.dumps(scores))
        return scores

    @staticmethod
    def text(item: dict) -> str:
        return f"{item.get('headline') or ''} {item.get('summary') or ''}"

    def trading_days(self, symbol: str) -> list[str]:
        return sorted(day for day in self.bars.get(symbol, {}) if day in self.close_map[symbol])

    def headlines(self, symbol: str, day: str) -> list[dict]:
        cutoff = f"{day}T20:00:00Z"
        rows = self.news.get(symbol, [])
        low, high = 0, len(rows)
        while low < high:  # last index with created_at < cutoff
            middle = (low + high) // 2
            if rows[middle]["created_at"] < cutoff:
                low = middle + 1
            else:
                high = middle
        return rows[max(0, low - 10):low]

    def market(self, symbol: str, day: str, target_days: int, scorer: str, scale: float,
               settings: Settings) -> MarketSnapshot | None:
        dates = [when for when, _ in self.closes[symbol]]
        end = bisect_right(dates, day)
        history = [close for _, close in self.closes[symbol][max(0, end - 50):end]]
        spot = history[-1]
        expiry = target_expiry(date.fromisoformat(day), target_days, settings)
        wanted = {expiry.strftime("%y%m%d"), (expiry - timedelta(days=1)).strftime("%y%m%d")}
        rows = []
        for occ, close in self.bars[symbol][day].items():
            match = OCC.match(occ)
            strike = int(match.group(4)) / 1000
            if match.group(2) in wanted and abs(strike / spot - 1) <= 0.16 and close > 0:
                rows.append((occ, match.group(2), match.group(3) == "C", strike, close))
        if len(rows) < 4:
            return None
        expiry_code = max(row[1] for row in rows)  # Friday over a Thursday daily
        rows = [row for row in rows if row[1] == expiry_code]
        expiration = datetime.strptime(expiry_code, "%y%m%d").date()
        years = max(1, (expiration - date.fromisoformat(day)).days) / 365
        call = np.array([row[2] for row in rows])
        strike = np.array([row[3] for row in rows])
        price = np.array([row[4] for row in rows])
        vol, delta, gamma, theta, vega = greeks(spot, strike, years, price, call)
        at = datetime.fromisoformat(f"{day}T20:00:00+00:00")
        contracts = []
        for index, (occ, _, is_call, strike_value, close) in enumerate(rows):
            if np.isnan(vol[index]):
                continue
            bid, ask = quote(symbol, close, scale)
            contracts.append(OptionContract(
                symbol=occ, underlying=symbol, expiration=expiration.isoformat(), strike=strike_value,
                option_type="call" if is_call else "put", bid=bid, ask=ask, delta=round(float(delta[index]), 4),
                gamma=float(gamma[index]), theta=float(theta[index]), vega=float(vega[index]),
                implied_volatility=float(vol[index]), open_interest=1_000_000, quote_at=at))
        if not contracts:
            return None
        trend = MarketProvider._trend_score(history)
        realized = MarketProvider._realized_volatility(history)
        iv_ratio = statistics.median(c.implied_volatility for c in contracts) / max(realized, 0.05)
        iv_score = max(0.0, min(100.0, (iv_ratio - 0.7) / 1.1 * 100))
        news = self.headlines(symbol, day)
        if scorer == "none" or not news:
            sentiment = 0.0
        else:
            column = 0 if scorer == "model" else 1
            sentiment = round(sum(self.scores[str(item["id"])][column] for item in news) / len(news), 3)
        catalysts = list(dict.fromkeys(intelligence.classify_catalyst(self.text(item)) for item in news))
        catalyst = min(1.0, len(news) / 8 + (0.12 if any(item != "market" for item in catalysts) else 0.0))
        return MarketSnapshot(
            symbol=symbol, captured_at=at, underlying_price=spot, trend_score=trend, realized_volatility=realized,
            iv_rank=iv_score, catalyst_score=catalyst, news_sentiment=sentiment, catalyst_types=catalysts,
            regime=MarketProvider._regime(trend, iv_score), contracts=contracts, source="history-backtest")

    def cycles(self, target_days: int, scorer: str, scale: float, settings: Settings) -> list[dict]:
        days = sorted({day for symbol in self.bars for day in self.trading_days(symbol)})
        built = []
        for day in days:
            markets = [market for symbol in self.bars if day in self.bars[symbol] and day in self.close_map[symbol]
                       for market in [self.market(symbol, day, target_days, scorer, scale, settings)] if market]
            closes = {occ: close for symbol in self.bars for occ, close in self.bars[symbol].get(day, {}).items()}
            built.append({"recorded_at": f"{day}T20:00:00+00:00", "equity": 100_000, "markets": markets,
                          "quotes": DayQuotes(closes, scale)})
        return built

    def settle(self, result) -> None:  # noqa: ANN001
        """Trades never quoted again before expiry settle at intrinsic value on the expiry close."""
        last_day = max(day for symbol in self.closes for day, _ in self.closes[symbol])
        for trade in result.trades:
            if trade.pnl is not None or trade.expiration >= last_day:
                continue
            spot = [close for when, close in self.closes[trade.symbol] if when <= trade.expiration][-1]
            value = 0.0
            for occ, direction in trade.legs:
                match = OCC.match(occ)
                strike = int(match.group(4)) / 1000
                value += direction * max(0.0, (spot - strike) if match.group(3) == "C" else (strike - spot)) * 100
            trade.closed_at, trade.pnl, trade.reason = trade.expiration, round((trade.entry_cash + value) * trade.quantity, 2), "settled"


def _cache_simulations() -> None:
    """Every configuration re-tests the same plans on the same days; simulate each once."""
    original = MonteCarloSimulator.simulate
    memo: dict = {}

    def simulate(self, market, plan, paths=1_000):  # noqa: ANN001, ANN202
        key = (market.symbol, market.captured_at, market.news_sentiment, plan.quantity,
               tuple((leg.symbol, leg.side, leg.mid, leg.spread_pct) for leg in plan.legs), paths)
        if key not in memo:
            memo[key] = original(self, market, plan, paths)
        return memo[key]

    MonteCarloSimulator.simulate = simulate


def period(trades, start: str | None, end: str | None) -> dict:  # noqa: ANN001
    chosen = [t for t in trades if t.pnl is not None and (start is None or t.opened_at >= start)
              and (end is None or t.opened_at < end)]
    pnls = [t.pnl for t in chosen]
    curve, peak, drawdown = 0.0, 0.0, 0.0
    for trade in sorted(chosen, key=lambda t: t.closed_at):
        curve += trade.pnl
        peak, drawdown = max(peak, curve), max(drawdown, peak - curve)
    return {"n": len(pnls), "pnl": round(sum(pnls), 2), "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 3)
            if pnls else None, "mean": round(statistics.mean(pnls), 2) if pnls else None,
            "max_drawdown": round(drawdown, 2)}


def label(config: tuple) -> str:
    take, stop, dte, bearish = config
    show = lambda value: "none" if value is None else f"{value:.0%}"  # noqa: E731
    return f"take {show(take)} / stop {show(stop)} / {dte} DTE / bearish {'on' if bearish else 'off'}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("history", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    _cache_simulations()
    data = History(args.history)
    live = (0.25, 0.20, 21, False)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "symbols": sorted(data.bars), "folds": FOLDS, "configs": {}, "variants": {}}

    def run(cycles, take, stop, bearish, name):  # noqa: ANN001, ANN202
        router = StrategyRouter(bearish, settings.condor_symbols)
        result = replay(cycles, router, settings, name, take=take, stop=stop)
        data.settle(result)
        return result

    for dte in (14, 21, 28):
        cycles = data.cycles(dte, "model", 1.0, settings)
        report.setdefault("first_day", cycles[0]["recorded_at"][:10])
        report["last_day"] = cycles[-1]["recorded_at"][:10]
        for (take, stop), bearish in itertools.product(EXITS, (False, True)):
            config = (take, stop, dte, bearish)
            result = run(cycles, take, stop, bearish, label(config))
            entry = {"all": period(result.trades, None, None), "summary": summary(result),
                     "trades": result.trades}
            report["configs"][label(config)] = entry
            print(label(config), entry["all"], flush=True)
        del cycles

    # Walk-forward: pick on everything before the fold, score inside it.
    edges = [*FOLDS, None]
    folds = []
    for start, end in zip(edges, edges[1:]):
        def train_pnl(name: str) -> float:
            stats = period(report["configs"][name]["trades"], None, start)
            return stats["pnl"] if stats["n"] >= 20 else float("-inf")
        best = max(report["configs"], key=train_pnl)
        if train_pnl(best) == float("-inf"):  # nothing has 20 trades yet: keep the live rules
            best = label(live)
        folds.append({"fold": f"{start} to {end or report['last_day']}", "chosen": best,
                      "chosen_train": period(report["configs"][best]["trades"], None, start),
                      "chosen_test": period(report["configs"][best]["trades"], start, end),
                      "live_test": period(report["configs"][label(live)]["trades"], start, end),
                      "all_configs_test_median_pnl": statistics.median(
                          period(c["trades"], start, end)["pnl"] for c in report["configs"].values())})
    report["walk_forward"] = folds
    report["walk_forward_total"] = {"chosen": round(sum(f["chosen_test"]["pnl"] for f in folds), 2),
                                    "live": round(sum(f["live_test"]["pnl"] for f in folds), 2)}

    # Sensitivity of the live configuration: news scorer and fill assumptions.
    for name, scorer, scale in (("lexicon sentiment", "lexicon", 1.0), ("no sentiment", "none", 1.0),
                                ("mid fills (no spread)", "model", 0.0), ("double spreads", "model", 2.0)):
        result = run(data.cycles(21, scorer, scale, settings), 0.25, 0.20, False, name)
        report["variants"][name] = {"all": period(result.trades, None, None), "summary": summary(result)}
        print(name, report["variants"][name]["all"], flush=True)

    for entry in report["configs"].values():
        del entry["trades"]
    text = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"walk_forward": folds, "total": report["walk_forward_total"],
                      "live": report["configs"][label(live)]["all"], "variants": {
                          k: v["all"] for k, v in report["variants"].items()}}, indent=2, default=str))


if __name__ == "__main__":
    main()
