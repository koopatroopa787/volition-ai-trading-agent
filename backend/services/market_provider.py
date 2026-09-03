"""Demo and live-paper evidence providers."""

from __future__ import annotations

import asyncio
import math
import re
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from config import Settings
from fixtures import demo_account, demo_markets
from market_universe import MARKET_UNIVERSE
from models import (
    AccountSnapshot,
    EquityPoint,
    MarketClockState,
    MarketPulseItem,
    MarketSnapshot,
    OptionContract,
    PositionView,
    PricePoint,
    StrategyKind,
)
from services.alpaca_cli import AlpacaCli
from services.intelligence import headline_metrics


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict) and len(value) == 1 and next(iter(value)) in {"data", "account", "positions", "snapshots"}:
        value = next(iter(value.values()))
    return value


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MarketProvider:
    PAPER_BASE_URL = "https://paper-api.alpaca.markets"
    DATA_BASE_URL = "https://data.alpaca.markets"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cli = AlpacaCli(settings)
        self._bar_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._position_cache: tuple[float, Any] | None = None
        self._clock_cache: tuple[float, MarketClockState] | None = None
        self._pulse_cache: tuple[float, list[MarketPulseItem]] | None = None
        self._pulse_bar_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int | float] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=self._headers, params=params)
            response.raise_for_status()
            return response.json()

    async def account(self) -> AccountSnapshot:
        if self.settings.volition_mode == "demo":
            return demo_account()
        if self.cli.available:
            raw, position_raw = await asyncio.gather(
                asyncio.to_thread(self.cli.account),
                asyncio.to_thread(self.cli.positions),
            )
            source = "alpaca-paper-cli"
        else:
            raw, position_raw = await asyncio.gather(
                self._get_json(f"{self.PAPER_BASE_URL}/v2/account"),
                self._get_json(f"{self.PAPER_BASE_URL}/v2/positions"),
            )
            source = "alpaca-paper-api-fallback"
        body = _unwrap(raw)
        if not isinstance(body, dict):
            raise RuntimeError("Unexpected account payload from Alpaca")
        equity = _number(body.get("equity") or body.get("portfolio_value"))
        last_equity = _number(body.get("last_equity"), equity)
        position_body = _unwrap(position_raw)
        if isinstance(position_body, dict):
            position_body = position_body.get("positions", [])
        self._position_cache = (time.monotonic(), position_raw)
        position_views = self._parse_position_views(position_raw, equity)
        return AccountSnapshot(
            account_id=str(body.get("id") or body.get("account_id") or self.settings.hackathon_account_id),
            status=str(body.get("status", "ACTIVE")),
            created_at=body.get("created_at"),
            equity=equity,
            last_equity=last_equity,
            cash=_number(body.get("cash")),
            options_buying_power=_number(body.get("options_buying_power") or body.get("buying_power")),
            portfolio_value=_number(body.get("portfolio_value"), equity),
            options_trading_level=int(_number(body.get("options_trading_level") or body.get("options_approved_level"), 0)),
            open_positions=len(position_views),
            open_risk=round(sum(position.max_loss for position in position_views), 2),
            starting_balance=self.settings.hackathon_starting_balance,
            source=source,
        )

    async def positions(self, equity: float) -> list[PositionView]:
        if self.settings.volition_mode == "demo":
            from fixtures import demo_positions

            return demo_positions()
        if self._position_cache and time.monotonic() - self._position_cache[0] <= 30:
            raw = self._position_cache[1]
        elif self.cli.available:
            raw = await asyncio.to_thread(self.cli.positions)
        else:
            raw = await self._get_json(f"{self.PAPER_BASE_URL}/v2/positions")
        self._position_cache = (time.monotonic(), raw)
        return self._parse_position_views(raw, equity)

    async def portfolio_history(self, account: AccountSnapshot) -> list[EquityPoint]:
        """Return broker-recorded equity beside a date-aligned SPY benchmark."""

        if self.settings.volition_mode == "demo":
            from fixtures import demo_equity_curve

            return demo_equity_curve()

        start = date.fromisoformat(self.settings.hackathon_start_date)
        try:
            portfolio, benchmark_payload = await asyncio.gather(
                self._get_json(
                    f"{self.PAPER_BASE_URL}/v2/account/portfolio/history",
                    params={
                        "period": "1M",
                        "timeframe": "1D",
                        "intraday_reporting": "market_hours",
                        "pnl_reset": "per_day",
                    },
                ),
                self._get_json(
                    f"{self.DATA_BASE_URL}/v2/stocks/SPY/bars",
                    params={
                        "timeframe": "1Day",
                        "start": start.isoformat(),
                        "end": (date.today() + timedelta(days=1)).isoformat(),
                        "limit": 1_000,
                        "adjustment": "all",
                        "feed": "iex",
                    },
                ),
            )
        except Exception:  # portfolio evidence should not take down the desk
            return []

        return self._portfolio_points(portfolio, benchmark_payload, account, start)

    @staticmethod
    def _portfolio_points(
        portfolio: Any,
        benchmark_payload: Any,
        account: AccountSnapshot,
        start: date,
    ) -> list[EquityPoint]:
        timestamps = portfolio.get("timestamp", []) if isinstance(portfolio, dict) else []
        equities = portfolio.get("equity", []) if isinstance(portfolio, dict) else []
        account_rows: list[tuple[date, float]] = []
        for raw_timestamp, raw_equity in zip(timestamps, equities):
            try:
                observed = datetime.fromtimestamp(float(raw_timestamp), timezone.utc).date()
                equity = float(raw_equity)
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if observed >= start and equity > 0:
                account_rows.append((observed, equity))

        bars = benchmark_payload.get("bars", []) if isinstance(benchmark_payload, dict) else []
        benchmark_rows: list[tuple[date, float]] = []
        for bar in bars if isinstance(bars, list) else []:
            if not isinstance(bar, dict):
                continue
            try:
                observed = datetime.fromisoformat(str(bar.get("t", "")).replace("Z", "+00:00")).date()
                close = float(bar.get("c"))
            except (TypeError, ValueError):
                continue
            if observed >= start and close > 0:
                benchmark_rows.append((observed, close))

        if not account_rows or not benchmark_rows:
            return []
        account_rows.sort(key=lambda row: row[0])
        benchmark_rows.sort(key=lambda row: row[0])
        benchmark_base = benchmark_rows[0][1]
        benchmark_index = 0
        points: list[EquityPoint] = []
        for observed, equity in account_rows:
            while (
                benchmark_index + 1 < len(benchmark_rows)
                and benchmark_rows[benchmark_index + 1][0] <= observed
            ):
                benchmark_index += 1
            benchmark_close = benchmark_rows[benchmark_index][1]
            points.append(
                EquityPoint(
                    label=observed.isoformat(),
                    equity=round(equity, 2),
                    benchmark=round(
                        account.starting_balance * benchmark_close / benchmark_base,
                        2,
                    ),
                )
            )
        return points

    async def markets(
        self,
        market_open: bool | None = None,
        symbols: list[str] | None = None,
    ) -> list[MarketSnapshot]:
        requested = symbols or self.settings.watchlist
        if self.settings.volition_mode == "demo":
            available = {market.symbol: market for market in demo_markets()}
            return [available[symbol] for symbol in requested if symbol in available]
        if market_open is None:
            market_open = await self.market_open()
        return await asyncio.gather(
            *(self._live_market(symbol, market_open) for symbol in requested)
        )

    async def ranked_symbols(self, symbols: list[str]) -> list[str]:
        """Cheaply rank the broad universe before loading any option chains."""

        pulse = {item.symbol: item for item in await self.market_pulse()}
        order = {symbol: index for index, symbol in enumerate(symbols)}

        def score(symbol: str) -> tuple[float, float, int]:
            item = pulse.get(symbol)
            if not item:
                return (-1.0, -1.0, -order[symbol])
            recent_movement = abs(item.change_pct)
            sustained_movement = abs(item.change_30d_pct) / math.sqrt(30)
            closes = [point.close for point in item.bars[-15:] if point.close > 0]
            realized = 0.0
            if len(closes) > 2:
                returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:])]
                realized = statistics.pstdev(returns) * math.sqrt(252) * 100
            return (
                recent_movement * 0.55 + sustained_movement * 0.25 + realized * 0.02,
                math.log10(max(item.volume, 1)),
                -order[symbol],
            )

        return sorted(symbols, key=score, reverse=True)

    async def market_open(self) -> bool:
        return (await self.clock()).is_open

    async def market_pulse(self) -> list[MarketPulseItem]:
        """Return broad cross-asset context without loading option chains."""

        if self._pulse_cache and time.monotonic() - self._pulse_cache[0] <= 20:
            return self._pulse_cache[1]
        if self.settings.volition_mode == "demo":
            items = self._demo_market_pulse()
        else:
            items = await self._live_market_pulse()
        self._pulse_cache = (time.monotonic(), items)
        return items

    async def _live_market_pulse(self) -> list[MarketPulseItem]:
        symbols = ",".join(str(item["symbol"]) for item in MARKET_UNIVERSE)
        snapshots, history = await asyncio.gather(
            self._get_json(
                f"{self.DATA_BASE_URL}/v2/stocks/snapshots",
                params={"symbols": symbols, "feed": "iex"},
            ),
            self._batch_daily_bars(symbols),
        )
        snapshot_map = snapshots.get("snapshots", snapshots) if isinstance(snapshots, dict) else {}
        now = datetime.now(timezone.utc)
        items: list[MarketPulseItem] = []
        for definition in MARKET_UNIVERSE:
            symbol = str(definition["symbol"])
            snapshot = snapshot_map.get(symbol, {}) if isinstance(snapshot_map, dict) else {}
            latest_trade = snapshot.get("latestTrade") or snapshot.get("latest_trade") or {}
            minute_bar = snapshot.get("minuteBar") or snapshot.get("minute_bar") or {}
            daily_bar = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
            previous_bar = snapshot.get("prevDailyBar") or snapshot.get("prev_daily_bar") or {}
            rows = history.get(symbol, [])
            last_history = rows[-1] if rows else {}
            price = _number(
                latest_trade.get("p")
                or minute_bar.get("c")
                or daily_bar.get("c")
                or last_history.get("close")
            )
            if price <= 0:
                continue
            previous_close = _number(previous_bar.get("c"))
            if previous_close <= 0 and len(rows) > 1:
                previous_close = _number(rows[-2].get("close"))
            first_close = _number(rows[0].get("close"), price) if rows else price
            timestamp = latest_trade.get("t") or minute_bar.get("t") or daily_bar.get("t")
            points = [
                PricePoint(timestamp=row["timestamp"], close=row["close"], volume=row["volume"])
                for row in rows[-30:]
                if row.get("timestamp") and row.get("close")
            ]
            items.append(
                MarketPulseItem(
                    symbol=symbol,
                    label=str(definition["label"]),
                    group=str(definition["group"]),
                    proxy_for=str(definition["proxy_for"]),
                    price=round(price, 4),
                    change_pct=round(((price / previous_close) - 1) * 100, 2) if previous_close else 0.0,
                    change_30d_pct=round(((price / first_close) - 1) * 100, 2) if first_close else 0.0,
                    day_open=_number(daily_bar.get("o"), price),
                    day_high=_number(daily_bar.get("h"), price),
                    day_low=_number(daily_bar.get("l"), price),
                    volume=_number(daily_bar.get("v")),
                    updated_at=timestamp or now,
                    source="Alpaca IEX",
                    bars=points,
                )
            )
        return items

    async def _batch_daily_bars(self, symbols: str) -> dict[str, list[dict[str, Any]]]:
        if self._pulse_bar_cache and time.monotonic() - self._pulse_bar_cache[0] <= 300:
            return self._pulse_bar_cache[1]
        payload = await self._get_json(
            f"{self.DATA_BASE_URL}/v2/stocks/bars",
            params={
                "symbols": symbols,
                "timeframe": "1Day",
                "start": (date.today() - timedelta(days=50)).isoformat(),
                "limit": 10000,
                "adjustment": "all",
                "feed": "iex",
            },
        )
        bar_map = payload.get("bars", {}) if isinstance(payload, dict) else {}
        parsed = {
            symbol: [
                {
                    "timestamp": str(item.get("t") or ""),
                    "open": _number(item.get("o")),
                    "high": _number(item.get("h")),
                    "low": _number(item.get("l")),
                    "close": _number(item.get("c")),
                    "volume": _number(item.get("v")),
                }
                for item in rows
                if isinstance(item, dict)
            ]
            for symbol, rows in bar_map.items()
            if isinstance(rows, list)
        }
        self._pulse_bar_cache = (time.monotonic(), parsed)
        return parsed

    @staticmethod
    def _demo_market_pulse() -> list[MarketPulseItem]:
        now = datetime.now(timezone.utc)
        items: list[MarketPulseItem] = []
        for definition in MARKET_UNIVERSE:
            symbol = str(definition["symbol"])
            seed = sum((index + 1) * ord(character) for index, character in enumerate(symbol))
            base = float(definition["demo_price"])
            direction = ((seed % 19) - 9) / 900
            amplitude = 0.006 + (seed % 7) / 1000
            closes = [
                base * (1 + direction * (day - 29) / 3 + math.sin((seed % 11 + day) / 3.2) * amplitude)
                for day in range(30)
            ]
            points = [
                PricePoint(
                    timestamp=now - timedelta(days=29 - day),
                    close=round(close, 4),
                    volume=float(900_000 + (seed * (day + 5)) % 7_000_000),
                )
                for day, close in enumerate(closes)
            ]
            price = closes[-1]
            previous = closes[-2]
            day_swing = 0.004 + (seed % 5) / 1000
            items.append(
                MarketPulseItem(
                    symbol=symbol,
                    label=str(definition["label"]),
                    group=str(definition["group"]),
                    proxy_for=str(definition["proxy_for"]),
                    price=round(price, 4),
                    change_pct=round(((price / previous) - 1) * 100, 2),
                    change_30d_pct=round(((price / closes[0]) - 1) * 100, 2),
                    day_open=round(previous, 4),
                    day_high=round(max(price, previous) * (1 + day_swing), 4),
                    day_low=round(min(price, previous) * (1 - day_swing), 4),
                    volume=points[-1].volume,
                    updated_at=now,
                    source="Demo-labelled market path",
                    bars=points,
                )
            )
        return items

    async def clock(self) -> MarketClockState:
        if self._clock_cache and time.monotonic() - self._clock_cache[0] <= 15:
            return self._clock_cache[1]
        if self.settings.volition_mode == "demo":
            clock = self._demo_clock()
            self._clock_cache = (time.monotonic(), clock)
            return clock

        raw = _unwrap(
            await asyncio.to_thread(self.cli.clock)
            if self.cli.available
            else await self._get_json(f"{self.PAPER_BASE_URL}/v2/clock")
        )
        if isinstance(raw, dict) and len(raw) == 1 and "clock" in raw:
            raw = raw["clock"]
        if not isinstance(raw, dict):
            raise RuntimeError("Unexpected market clock payload from Alpaca")
        clock = MarketClockState(
            timestamp=raw.get("timestamp") or datetime.now(timezone.utc),
            is_open=bool(raw.get("is_open") if "is_open" in raw else raw.get("isOpen", False)),
            next_open=raw.get("next_open") or raw.get("nextOpen"),
            next_close=raw.get("next_close") or raw.get("nextClose"),
            source="alpaca-paper-cli" if self.cli.available else "alpaca-paper-api-fallback",
        )
        self._clock_cache = (time.monotonic(), clock)
        return clock

    @staticmethod
    def _demo_clock() -> MarketClockState:
        eastern = ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc)
        local_now = now.astimezone(eastern)
        session_open = datetime.combine(local_now.date(), clock_time(9, 30), eastern)
        session_close = datetime.combine(local_now.date(), clock_time(16, 0), eastern)
        is_open = local_now.weekday() < 5 and session_open <= local_now < session_close

        if local_now.weekday() < 5 and local_now < session_open:
            next_open = session_open
            next_close = session_close
        elif is_open:
            next_open = session_open
            next_close = session_close
        else:
            candidate = local_now.date() + timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
            next_open = datetime.combine(candidate, clock_time(9, 30), eastern)
            next_close = datetime.combine(candidate, clock_time(16, 0), eastern)
        return MarketClockState(
            timestamp=now,
            is_open=is_open,
            next_open=next_open,
            next_close=next_close,
            source="demo-simulated-clock",
        )

    async def _live_market(self, symbol: str, market_open: bool) -> MarketSnapshot:
        bars, intraday_bars = await asyncio.gather(
            self._stock_bars(symbol),
            self._intraday_bars(symbol),
        )
        closes = [item["close"] for item in bars if item.get("close")]
        spot = closes[-1] if closes else 0.0
        if self.cli.available:
            target_expiry = self._target_expiry().isoformat()
            chain_snapshots, contract_metadata, news_raw = await asyncio.gather(
                asyncio.to_thread(self.cli.option_chain, symbol, spot, target_expiry),
                self._option_contracts_api(symbol, spot),
                self._news_evidence(symbol),
            )
            chain_raw = self._join_chain_metadata(chain_snapshots, contract_metadata)
            source = "alpaca-paper-cli+market-data-api"
        else:
            chain_raw, news_raw = await asyncio.gather(
                self._option_chain_api(symbol, spot),
                self._news_api(symbol),
            )
            source = "alpaca-paper-api-fallback+market-data-api"
        contracts = self._parse_chain(chain_raw, symbol)
        spot = closes[-1] if closes else self._spot_from_chain(contracts)
        trend = self._trend_score(closes)
        realized = self._realized_volatility(closes)
        median_iv = statistics.median([contract.implied_volatility for contract in contracts]) if contracts else realized
        iv_ratio = median_iv / max(realized, 0.05)
        iv_score = max(0.0, min(100.0, (iv_ratio - 0.7) / 1.1 * 100))
        news_items = self._news_items(news_raw)
        news_sentiment, catalyst_types = headline_metrics(news_raw)
        catalyst = min(1.0, len(news_items) / 8 + (0.12 if any(item != "market" for item in catalyst_types) else 0.0))
        regime = self._regime(trend, iv_score)
        history_bars = intraday_bars if len(intraday_bars) >= 8 else bars[-50:]
        history_source = "Alpaca IEX · 15-minute bars" if len(intraday_bars) >= 8 else "Alpaca IEX · daily bars"
        return MarketSnapshot(
            symbol=symbol,
            underlying_price=spot,
            trend_score=trend,
            realized_volatility=realized,
            iv_rank=iv_score,
            catalyst_score=catalyst,
            news_sentiment=news_sentiment,
            catalyst_types=catalyst_types,
            regime=regime,
            market_open=market_open,
            contracts=contracts,
            price_history=[
                PricePoint(
                    timestamp=item["timestamp"],
                    close=item["close"],
                    volume=item["volume"],
                )
                for item in history_bars
                if item.get("timestamp") and item.get("close")
            ],
            price_history_source=history_source,
            source=source,
        )

    async def _news_evidence(self, symbol: str) -> Any:
        if self.cli.available:
            try:
                return await asyncio.to_thread(self.cli.news, symbol)
            except Exception:
                pass
        return await self._news_api(symbol)

    async def _option_chain_api(self, symbol: str, spot: float) -> dict[str, Any]:
        snapshots, contracts = await asyncio.gather(
            self._get_json(
                f"{self.DATA_BASE_URL}/v1beta1/options/snapshots/{symbol}",
                params={**self._option_filters(spot), "feed": "indicative"},
            ),
            self._option_contracts_api(symbol, spot),
        )
        return self._join_chain_metadata(snapshots, contracts)

    async def _option_contracts_api(self, symbol: str, spot: float) -> Any:
        return await self._get_json(
            f"{self.PAPER_BASE_URL}/v2/options/contracts",
            params={**self._option_filters(spot), "underlying_symbols": symbol},
        )

    def _option_filters(self, spot: float) -> dict[str, str | int | float]:
        return {
            "expiration_date": self._target_expiry().isoformat(),
            "strike_price_gte": round(spot * 0.84, 2) if spot > 0 else 0,
            "strike_price_lte": round(spot * 1.16, 2) if spot > 0 else 1_000_000,
            "limit": 1000,
        }

    @staticmethod
    def _join_chain_metadata(snapshots: Any, contracts: Any) -> dict[str, Any]:
        contract_items = contracts.get("option_contracts", []) if isinstance(contracts, dict) else []
        metadata = {
            item.get("symbol"): item
            for item in contract_items
            if isinstance(item, dict) and item.get("symbol")
        }
        snapshot_items = snapshots.get("snapshots", {}) if isinstance(snapshots, dict) else {}
        return {
            contract_symbol: {**snapshot, "contract": metadata.get(contract_symbol, {})}
            for contract_symbol, snapshot in snapshot_items.items()
            if isinstance(snapshot, dict)
        }

    async def _news_api(self, symbol: str) -> Any:
        return await self._get_json(
            f"{self.DATA_BASE_URL}/v1beta1/news",
            params={"symbols": symbol, "limit": 10},
        )

    def _target_expiry(self) -> date:
        target_days = min(self.settings.max_dte, max(self.settings.min_dte, 21))
        candidate = date.today() + timedelta(days=target_days)
        while candidate.weekday() != 4:
            candidate += timedelta(days=1)
        if (candidate - date.today()).days > self.settings.max_dte:
            candidate -= timedelta(days=7)
        return candidate

    async def _stock_bars(self, symbol: str) -> list[dict[str, Any]]:
        return await self._bars(
            symbol,
            timeframe="1Day",
            start=(date.today() - timedelta(days=70)).isoformat(),
            limit=50,
            cache_seconds=300,
        )

    async def _intraday_bars(self, symbol: str) -> list[dict[str, Any]]:
        start = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        return await self._bars(
            symbol,
            timeframe="15Min",
            start=start,
            limit=160,
            cache_seconds=60,
        )

    async def _bars(
        self,
        symbol: str,
        *,
        timeframe: str,
        start: str,
        limit: int,
        cache_seconds: int,
    ) -> list[dict[str, Any]]:
        cache_key = f"{symbol}:{timeframe}:{start[:10]}:{limit}"
        cached = self._bar_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] <= cache_seconds:
            return cached[1]
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }
        params = {
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            "adjustment": "all",
            "feed": "iex",
        }
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
        rows = [
            {
                "timestamp": str(item.get("t") or ""),
                "open": _number(item.get("o")),
                "high": _number(item.get("h")),
                "low": _number(item.get("l")),
                "close": _number(item.get("c")),
                "volume": _number(item.get("v")),
            }
            for item in payload.get("bars", [])
        ]
        self._bar_cache[cache_key] = (time.monotonic(), rows)
        return rows

    @staticmethod
    def _decode_occ(symbol: str) -> dict[str, Any] | None:
        match = re.fullmatch(r"([A-Z]{1,6})(\d{6})([CP])(\d{8})", symbol.upper())
        if not match:
            return None
        expiry = datetime.strptime(match.group(2), "%y%m%d").date().isoformat()
        return {
            "underlying": match.group(1),
            "expiration": expiry,
            "option_type": "call" if match.group(3) == "C" else "put",
            "strike": int(match.group(4)) / 1000,
        }

    @classmethod
    def _parse_position_views(cls, raw: Any, equity: float) -> list[PositionView]:
        body = _unwrap(raw)
        if isinstance(body, dict):
            body = body.get("positions", body.get("data", []))
        if not isinstance(body, list):
            return []

        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in body:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            decoded = cls._decode_occ(symbol)
            if not decoded:
                continue
            side = str(item.get("side") or "long").lower()
            quantity = abs(_number(item.get("qty") or item.get("quantity")))
            groups[(decoded["underlying"], decoded["expiration"])].append(
                {
                    **decoded,
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "cost_basis": _number(item.get("cost_basis")),
                    "market_value": _number(item.get("market_value")),
                    "unrealized_pnl": _number(item.get("unrealized_pl") or item.get("unrealized_pnl")),
                    "current_price": _number(item.get("current_price")),
                }
            )

        views: list[PositionView] = []
        for (underlying, expiration), rows in groups.items():
            strategy = cls._infer_position_strategy(rows)
            quantity = max(1, int(min((row["quantity"] for row in rows), default=1)))
            cost_basis = sum(row["cost_basis"] for row in rows)
            market_value = sum(row["market_value"] for row in rows)
            unrealized_pnl = sum(row["unrealized_pnl"] for row in rows)
            max_loss = cls._position_max_loss(strategy, rows, cost_basis, quantity)
            pnl_ratio = unrealized_pnl / max(max_loss, 1.0)
            dte = max(0, (date.fromisoformat(expiration) - date.today()).days)
            thesis_health = round(max(0, min(100, 58 + pnl_ratio * 35 - max(0, 5 - dte) * 4)))
            legs = [
                f"{'+' if row['side'] == 'long' else '−'}{int(row['quantity'])} "
                f"{row['strike']:g}{'C' if row['option_type'] == 'call' else 'P'}"
                for row in sorted(rows, key=lambda row: (row["option_type"], row["strike"]))
            ]
            raw_legs = [
                {
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "option_type": row["option_type"],
                    "strike": row["strike"],
                    "ratio_qty": max(1, int(round(row["quantity"] / quantity))),
                    "mid": round(
                        row["current_price"]
                        or abs(row["market_value"]) / max(row["quantity"] * 100, 1),
                        4,
                    ),
                }
                for row in sorted(rows, key=lambda row: (row["option_type"], row["strike"]))
            ]
            views.append(
                PositionView(
                    symbol=underlying,
                    strategy=strategy,
                    opened_at="Synced from Alpaca",
                    expiration=expiration,
                    quantity=quantity,
                    cost_basis=round(cost_basis, 2),
                    market_value=round(market_value, 2),
                    unrealized_pnl=round(unrealized_pnl, 2),
                    max_loss=round(max_loss, 2),
                    risk_used_pct=round((max_loss / equity * 100) if equity else 0.0, 3),
                    thesis_health=thesis_health,
                    legs=legs,
                    raw_legs=raw_legs,
                )
            )
        return sorted(views, key=lambda item: item.expiration)

    @staticmethod
    def _infer_position_strategy(rows: list[dict[str, Any]]) -> StrategyKind:
        calls = [row for row in rows if row["option_type"] == "call"]
        puts = [row for row in rows if row["option_type"] == "put"]
        longs = [row for row in rows if row["side"] == "long"]
        shorts = [row for row in rows if row["side"] == "short"]
        if len(rows) == 4 and len(calls) == 2 and len(puts) == 2 and len(longs) == 2 and len(shorts) == 2:
            return StrategyKind.IRON_CONDOR
        if len(rows) == 2 and len(longs) == 2 and len(calls) == 1 and len(puts) == 1:
            return (
                StrategyKind.LONG_STRADDLE
                if calls[0]["strike"] == puts[0]["strike"]
                else StrategyKind.LONG_STRANGLE
            )
        if len(rows) == 2 and len(calls) == 2 and len(longs) == 1 and len(shorts) == 1:
            return (
                StrategyKind.BULL_CALL_SPREAD
                if longs[0]["strike"] < shorts[0]["strike"]
                else StrategyKind.UNCLASSIFIED
            )
        if len(rows) == 2 and len(puts) == 2 and len(longs) == 1 and len(shorts) == 1:
            return (
                StrategyKind.BEAR_PUT_SPREAD
                if longs[0]["strike"] > shorts[0]["strike"]
                else StrategyKind.UNCLASSIFIED
            )
        if len(rows) == 1 and rows[0]["side"] == "long":
            return StrategyKind.LONG_CALL if calls else StrategyKind.LONG_PUT
        return StrategyKind.UNCLASSIFIED

    @staticmethod
    def _position_max_loss(
        strategy: StrategyKind,
        rows: list[dict[str, Any]],
        cost_basis: float,
        quantity: int,
    ) -> float:
        if strategy in {
            StrategyKind.LONG_CALL,
            StrategyKind.LONG_PUT,
            StrategyKind.LONG_STRADDLE,
            StrategyKind.LONG_STRANGLE,
        }:
            return abs(cost_basis)
        calls = [row["strike"] for row in rows if row["option_type"] == "call"]
        puts = [row["strike"] for row in rows if row["option_type"] == "put"]
        widths = []
        if len(calls) >= 2:
            widths.append(max(calls) - min(calls))
        if len(puts) >= 2:
            widths.append(max(puts) - min(puts))
        structural_cap = max(widths, default=0.0) * 100 * quantity
        if cost_basis >= 0:
            return min(structural_cap, cost_basis) if structural_cap else cost_basis
        return max(0.0, structural_cap - abs(cost_basis))

    @staticmethod
    def _news_items(raw: Any) -> list[Any]:
        raw = _unwrap(raw)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            value = raw.get("news") or raw.get("items") or []
            return value if isinstance(value, list) else []
        return []

    @staticmethod
    def _parse_chain(raw: Any, underlying: str) -> list[OptionContract]:
        raw = _unwrap(raw)
        items: list[tuple[str | None, dict[str, Any]]] = []
        if isinstance(raw, list):
            items = [(item.get("symbol"), item) for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            candidate = raw.get("option_chain") or raw.get("chain") or raw
            if isinstance(candidate, dict):
                items = [(symbol, item) for symbol, item in candidate.items() if isinstance(item, dict)]
            elif isinstance(candidate, list):
                items = [(item.get("symbol"), item) for item in candidate if isinstance(item, dict)]

        contracts: list[OptionContract] = []
        for symbol, item in items:
            details = item.get("contract") if isinstance(item.get("contract"), dict) else item
            quote = item.get("latest_quote") or item.get("latestQuote") or item.get("quote") or {}
            latest_trade = item.get("latest_trade") or item.get("latestTrade") or {}
            greeks = item.get("greeks") or {}
            symbol = symbol or details.get("symbol")
            if not symbol:
                continue
            option_type = str(details.get("type") or details.get("option_type") or ("call" if "C" in symbol[-9:-8] else "put")).lower()
            expiration = str(details.get("expiration_date") or details.get("expiration") or "")
            strike = _number(details.get("strike_price") or details.get("strike"))
            if not expiration or strike <= 0 or option_type not in {"call", "put"}:
                continue
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    underlying=underlying,
                    expiration=expiration[:10],
                    strike=strike,
                    option_type=option_type,  # type: ignore[arg-type]
                    bid=_number(quote.get("bid_price") or quote.get("bp") or item.get("bid")),
                    ask=_number(quote.get("ask_price") or quote.get("ap") or item.get("ask")),
                    delta=_number(greeks.get("delta") or item.get("delta")),
                    gamma=_number(greeks.get("gamma") or item.get("gamma")),
                    theta=_number(greeks.get("theta") or item.get("theta")),
                    vega=_number(greeks.get("vega") or item.get("vega")),
                    implied_volatility=_number(item.get("implied_volatility") or item.get("impliedVolatility") or item.get("iv")),
                    open_interest=int(_number(details.get("open_interest") or item.get("open_interest"))),
                    volume=int(_number(item.get("volume") or latest_trade.get("size") or latest_trade.get("s"))),
                )
            )
        return contracts

    @staticmethod
    def _spot_from_chain(contracts: list[OptionContract]) -> float:
        if not contracts:
            return 0.0
        atm = min(contracts, key=lambda contract: abs(abs(contract.delta) - 0.5))
        return atm.strike

    @staticmethod
    def _trend_score(closes: list[float]) -> float:
        if len(closes) < 20:
            return 0.0
        fast = statistics.mean(closes[-5:])
        slow = statistics.mean(closes[-20:])
        return max(-1.0, min(1.0, ((fast / slow) - 1) / 0.04))

    @staticmethod
    def _realized_volatility(closes: list[float]) -> float:
        if len(closes) < 3:
            return 0.20
        returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:]) if previous > 0 and current > 0]
        return statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 2 else 0.20

    @staticmethod
    def _regime(trend: float, iv_score: float) -> str:
        if trend > 0.35:
            return "risk-on trend"
        if trend < -0.35:
            return "bearish volatility expansion"
        if iv_score >= 58:
            return "high-IV range"
        return "indecisive"
