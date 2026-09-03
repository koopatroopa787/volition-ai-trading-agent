"""Resilient market-intelligence adapters for free and official APIs.

Alpaca remains the source of truth for prices, options, and execution. These
providers add contextual evidence only; an outage can never authorize a trade
or take down the core trading loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from config import Settings
from models import (
    DataSourceStatus,
    FilingEvent,
    IntelligenceState,
    MacroSignal,
    NewsInsight,
)
from services.alpaca_cli import AlpacaCli


T = TypeVar("T")

POSITIVE_TERMS = {
    "beat": 1.0,
    "beats": 1.0,
    "bullish": 0.8,
    "growth": 0.5,
    "higher": 0.35,
    "profit": 0.55,
    "profits": 0.55,
    "raises": 0.75,
    "raised": 0.65,
    "record": 0.55,
    "rally": 0.75,
    "surge": 0.85,
    "upgrade": 0.9,
    "upgrades": 0.9,
    "approval": 0.7,
    "approved": 0.7,
    "buyback": 0.7,
    "expands": 0.55,
    "partnership": 0.4,
}

NEGATIVE_TERMS = {
    "bearish": -0.8,
    "cuts": -0.7,
    "cut": -0.6,
    "decline": -0.55,
    "downgrade": -0.9,
    "downgrades": -0.9,
    "fall": -0.6,
    "falls": -0.65,
    "fraud": -1.0,
    "investigation": -0.75,
    "layoffs": -0.65,
    "lawsuit": -0.75,
    "miss": -0.9,
    "misses": -0.9,
    "probe": -0.75,
    "recall": -0.65,
    "risk": -0.35,
    "slump": -0.7,
    "weak": -0.55,
    "warning": -0.65,
}

CATALYST_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings", ("earnings", "revenue", "guidance", "profit", "quarter")),
    ("regulatory", ("sec", "fda", "doj", "regulator", "lawsuit", "probe", "approval")),
    ("corporate action", ("merger", "acquisition", "buyback", "dividend", "split", "offering")),
    ("product", ("launch", "product", "chip", "model", "partnership", "contract")),
    ("macro", ("fed", "inflation", "rates", "jobs", "gdp", "treasury")),
)


def score_sentiment(text: str) -> float:
    """Small deterministic fallback; provider scores take precedence when present."""

    tokens = [token.strip(".,:;!?()[]{}\"'").lower() for token in text.split()]
    raw = sum(POSITIVE_TERMS.get(token, 0.0) + NEGATIVE_TERMS.get(token, 0.0) for token in tokens)
    if not raw:
        return 0.0
    return round(max(-1.0, min(1.0, raw / max(1.8, math.sqrt(len(tokens))))), 3)


def sentiment_label(score: float) -> str:
    if score >= 0.18:
        return "positive"
    if score <= -0.18:
        return "negative"
    return "neutral"


def classify_catalyst(text: str) -> str:
    lowered = text.lower()
    for label, terms in CATALYST_TERMS:
        if any(term in lowered for term in terms):
            return label
    return "market"


def headline_metrics(raw: Any) -> tuple[float, list[str]]:
    """Return aggregate sentiment and distinct catalyst types from a news payload."""

    items = _news_rows(raw)
    if not items:
        return 0.0, []
    scores: list[float] = []
    catalysts: list[str] = []
    for item in items:
        text = f"{item.get('headline') or item.get('title') or ''} {item.get('summary') or item.get('description') or ''}"
        scores.append(score_sentiment(text))
        catalyst = classify_catalyst(text)
        if catalyst not in catalysts:
            catalysts.append(catalyst)
    return round(sum(scores) / len(scores), 3), catalysts


def _news_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        rows = raw.get("news") or raw.get("data") or raw.get("items") or []
        return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    return []


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class IntelligenceProvider:
    ALPACA_DATA_URL = "https://data.alpaca.markets/v1beta1/news"
    MARKETAUX_URL = "https://api.marketaux.com/v1/news/all"
    SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
    FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

    FRED_SERIES = {
        "VIXCLS": ("VIX volatility", "index"),
        "DGS10": ("10Y Treasury yield", "%"),
        "BAMLH0A0HYM2": ("High-yield credit spread", "%"),
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cli = AlpacaCli(settings)
        self._cache: dict[str, tuple[float, Any]] = {}

    async def snapshot(self, symbols: list[str]) -> IntelligenceState:
        normalized = sorted({symbol.upper() for symbol in symbols if symbol})
        if self.settings.volition_mode == "demo":
            return self._demo_snapshot(normalized)

        news_task = self._safe(self._news(normalized), [])
        filings_task = self._safe(self._filings(normalized), [])
        macro_task = self._safe(self._macro(), [])
        news, filings, macro = await asyncio.gather(news_task, filings_task, macro_task)

        aggregate = round(sum(item.sentiment_score for item in news) / len(news), 3) if news else 0.0
        macro_risk = round(
            min(1.0, max(0.0, 0.5 - (sum(item.risk_impulse for item in macro) / max(1, len(macro))) / 2)),
            3,
        ) if macro else 0.0
        return IntelligenceState(
            news=news,
            filings=filings,
            macro=macro,
            aggregate_sentiment=aggregate,
            macro_risk_score=macro_risk,
            sources=self._source_statuses(news, filings, macro),
        )

    async def _safe(self, awaitable: Awaitable[T], fallback: T) -> T:
        try:
            return await awaitable
        except Exception:
            return fallback

    async def _cached(self, key: str, ttl_seconds: int, factory: Callable[[], Awaitable[T]]) -> T:
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl_seconds:
            return cached[1]
        value = await factory()
        self._cache[key] = (time.monotonic(), value)
        return value

    async def _news(self, symbols: list[str]) -> list[NewsInsight]:
        ttl = self.settings.intelligence_cache_minutes * 60
        key = f"news:{','.join(symbols)}:{bool(self.settings.marketaux_api_token)}"

        async def fetch() -> list[NewsInsight]:
            if self.settings.marketaux_api_token:
                try:
                    rows = await self._marketaux_rows(symbols)
                    parsed = self._parse_marketaux(rows, symbols)
                    if parsed:
                        return parsed
                except Exception:
                    pass
            rows = await self._alpaca_rows(symbols)
            return self._parse_alpaca(rows, symbols)

        return await self._cached(key, ttl, fetch)

    async def _marketaux_rows(self, symbols: list[str]) -> Any:
        params = {
            "api_token": self.settings.marketaux_api_token,
            "symbols": ",".join(symbols),
            "filter_entities": "true",
            "must_have_entities": "true",
            "group_similar": "true",
            "language": "en",
            "limit": 3,
        }
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(self.MARKETAUX_URL, params=params)
            response.raise_for_status()
            return response.json()

    async def _alpaca_rows(self, symbols: list[str]) -> Any:
        joined = ",".join(symbols)
        if self.cli.available:
            return await asyncio.to_thread(self.cli.news, joined)
        params = {"symbols": joined, "limit": 12, "sort": "desc"}
        headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(self.ALPACA_DATA_URL, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _parse_marketaux(raw: Any, symbols: list[str]) -> list[NewsInsight]:
        insights: list[NewsInsight] = []
        for row in _news_rows(raw):
            entities = [entity for entity in row.get("entities", []) if isinstance(entity, dict)]
            entity_symbols = [str(entity.get("symbol", "")).upper() for entity in entities]
            # Never label broad market news as the first watchlist ticker. That
            # creates false provenance in the UI when MarketAux returns a story
            # without a matching entity despite the requested entity filter.
            matched = [symbol for symbol in symbols if symbol in entity_symbols] or ["MARKET"]
            for symbol in matched:
                entity = next((item for item in entities if str(item.get("symbol", "")).upper() == symbol), {})
                score = float(entity.get("sentiment_score") or 0.0)
                match_score = float(entity.get("match_score") or 0.0)
                headline = str(row.get("title") or "Untitled market update")
                summary = str(row.get("description") or row.get("snippet") or "")
                insights.append(
                    NewsInsight(
                        id=f"{row.get('uuid') or hashlib.sha1(headline.encode()).hexdigest()[:16]}:{symbol}",
                        symbol=symbol,
                        headline=headline,
                        summary=summary,
                        url=str(row.get("url") or ""),
                        source=str(row.get("source") or "MarketAux"),
                        published_at=_parse_datetime(row.get("published_at")),
                        sentiment_score=max(-1.0, min(1.0, score)),
                        sentiment_label=sentiment_label(score),  # type: ignore[arg-type]
                        relevance=(
                            max(0.0, min(1.0, match_score / 100))
                            if symbol != "MARKET"
                            else 0.35
                        ),
                        catalyst_type=classify_catalyst(f"{headline} {summary}"),
                        provider="MarketAux",
                    )
                )
        return IntelligenceProvider._dedupe_news(insights)

    @staticmethod
    def _parse_alpaca(raw: Any, symbols: list[str]) -> list[NewsInsight]:
        insights: list[NewsInsight] = []
        for row in _news_rows(raw):
            headline = str(row.get("headline") or row.get("title") or "Untitled market update")
            summary = str(row.get("summary") or row.get("description") or "")
            row_symbols = [str(symbol).upper() for symbol in row.get("symbols", []) if symbol]
            matched = [symbol for symbol in symbols if symbol in row_symbols] or row_symbols[:1] or ["MARKET"]
            score = score_sentiment(f"{headline} {summary}")
            for symbol in matched:
                insights.append(
                    NewsInsight(
                        id=f"{row.get('id') or hashlib.sha1(headline.encode()).hexdigest()[:16]}:{symbol}",
                        symbol=symbol,
                        headline=headline,
                        summary=summary,
                        url=str(row.get("url") or ""),
                        source=str(row.get("source") or row.get("author") or "Alpaca News"),
                        published_at=_parse_datetime(row.get("created_at") or row.get("updated_at")),
                        sentiment_score=score,
                        sentiment_label=sentiment_label(score),  # type: ignore[arg-type]
                        relevance=0.72 if symbol != "MARKET" else 0.45,
                        catalyst_type=classify_catalyst(f"{headline} {summary}"),
                        provider="Alpaca News + local sentiment",
                    )
                )
        return IntelligenceProvider._dedupe_news(insights)[:18]

    @staticmethod
    def _dedupe_news(insights: list[NewsInsight]) -> list[NewsInsight]:
        """Keep one stable row per provider story and matched symbol."""

        unique: list[NewsInsight] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in sorted(
            insights,
            key=lambda insight: insight.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        ):
            fingerprint = (
                item.provider,
                item.symbol,
                item.url.strip().lower(),
                item.headline.strip().lower(),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(item)
        return unique

    async def _filings(self, symbols: list[str]) -> list[FilingEvent]:
        if not self.settings.sec_user_agent:
            return []
        key = f"sec:{','.join(symbols)}"
        return await self._cached(key, 6 * 60 * 60, lambda: self._fetch_filings(symbols))

    async def _fetch_filings(self, symbols: list[str]) -> list[FilingEvent]:
        headers = {"User-Agent": self.settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            ticker_response = await client.get(self.SEC_TICKERS_URL)
            ticker_response.raise_for_status()
            ticker_rows = ticker_response.json()
            mapping = {
                str(item.get("ticker", "")).upper(): item
                for item in ticker_rows.values()
                if isinstance(item, dict)
            }

            async def one(symbol: str) -> list[FilingEvent]:
                company = mapping.get(symbol)
                if not company:
                    return []
                cik = int(company.get("cik_str"))
                response = await client.get(f"{self.SEC_SUBMISSIONS_URL}/CIK{cik:010d}.json")
                response.raise_for_status()
                recent = response.json().get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                cutoff = date.today() - timedelta(days=45)
                events: list[FilingEvent] = []
                for index, form in enumerate(forms):
                    if form not in {"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F"}:
                        continue
                    filed_at = str(recent.get("filingDate", [""])[index])
                    try:
                        if date.fromisoformat(filed_at) < cutoff:
                            continue
                    except ValueError:
                        continue
                    accession = str(recent.get("accessionNumber", [""])[index])
                    document = str(recent.get("primaryDocument", [""])[index])
                    archive_accession = accession.replace("-", "")
                    events.append(
                        FilingEvent(
                            symbol=symbol,
                            company=str(company.get("title") or symbol),
                            form=form,
                            filed_at=filed_at,
                            report_date=str(recent.get("reportDate", [""])[index]),
                            description=str(recent.get("primaryDocDescription", [""])[index]),
                            url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{archive_accession}/{document}",
                            material=form in {"8-K", "10-Q", "10-K", "6-K"},
                        )
                    )
                    if len(events) >= 4:
                        break
                return events

            rows = await asyncio.gather(*(one(symbol) for symbol in symbols))
        events = [event for group in rows for event in group]
        return sorted(events, key=lambda item: item.filed_at, reverse=True)[:16]

    async def _macro(self) -> list[MacroSignal]:
        if not self.settings.fred_api_key:
            return []
        return await self._cached("fred:macro", 60 * 60, self._fetch_macro)

    async def _fetch_macro(self) -> list[MacroSignal]:
        async with httpx.AsyncClient(timeout=25) as client:
            async def one(series_id: str, label: str, unit: str) -> MacroSignal | None:
                response = await client.get(
                    self.FRED_URL,
                    params={
                        "series_id": series_id,
                        "api_key": self.settings.fred_api_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 8,
                    },
                )
                response.raise_for_status()
                values = [
                    (str(item.get("date", "")), float(item["value"]))
                    for item in response.json().get("observations", [])
                    if item.get("value") not in {None, "."}
                ]
                if not values:
                    return None
                observed_at, current = values[0]
                previous = values[min(len(values) - 1, 4)][1]
                change = current - previous
                if series_id == "VIXCLS":
                    impulse = max(-1.0, min(1.0, -((current - 18) / 16 + change / 8)))
                elif series_id == "BAMLH0A0HYM2":
                    impulse = max(-1.0, min(1.0, -((current - 3.5) / 3 + change)))
                else:
                    impulse = max(-1.0, min(1.0, -(change / 0.6)))
                return MacroSignal(
                    series_id=series_id,
                    label=label,
                    value=round(current, 3),
                    previous=round(previous, 3),
                    change=round(change, 3),
                    unit=unit,
                    observed_at=observed_at,
                    risk_impulse=round(impulse, 3),
                )

            rows = await asyncio.gather(
                *(one(series_id, label, unit) for series_id, (label, unit) in self.FRED_SERIES.items())
            )
        return [row for row in rows if row is not None]

    def _source_statuses(
        self,
        news: list[NewsInsight],
        filings: list[FilingEvent],
        macro: list[MacroSignal],
    ) -> list[DataSourceStatus]:
        marketaux_configured = bool(self.settings.marketaux_api_token)
        news_provider = news[0].provider if news else "unavailable"
        return [
            DataSourceStatus(
                name="Alpaca News",
                configured=self.settings.paper_credentials_configured,
                available=bool(news),
                detail=f"Current news evidence via {news_provider}",
                url="https://docs.alpaca.markets/reference/news-3",
            ),
            DataSourceStatus(
                name="MarketAux sentiment",
                configured=marketaux_configured,
                available=any(item.provider == "MarketAux" for item in news),
                detail="Optional free ticker sentiment; Alpaca + local scoring is the fallback",
                url="https://www.marketaux.com/documentation",
            ),
            DataSourceStatus(
                name="SEC EDGAR",
                configured=bool(self.settings.sec_user_agent),
                available=bool(filings),
                detail="Official keyless 8-K, 10-Q and 10-K catalyst feed",
                url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            ),
            DataSourceStatus(
                name="FRED macro",
                configured=bool(self.settings.fred_api_key),
                available=bool(macro),
                detail="VIX, Treasury yield and credit-spread regime overlay",
                url="https://fred.stlouisfed.org/docs/api/fred/",
            ),
        ]

    @staticmethod
    def _demo_snapshot(symbols: list[str]) -> IntelligenceState:
        now = datetime.now(timezone.utc)
        demo_rows = [
            (symbols[0] if symbols else "SPY", "Markets hold trend as rate expectations stabilize", 0.34, "macro"),
            (symbols[1] if len(symbols) > 1 else "QQQ", "Technology earnings guidance lifts growth outlook", 0.62, "earnings"),
            (symbols[2] if len(symbols) > 2 else "NVDA", "Chip sector faces export-rule review", -0.48, "regulatory"),
        ]
        news = [
            NewsInsight(
                id=f"demo-news-{index}",
                symbol=symbol,
                headline=headline,
                summary="Synthetic demo evidence; live providers are labelled separately in paper mode.",
                source="Volition demo",
                published_at=now - timedelta(minutes=index * 24),
                sentiment_score=score,
                sentiment_label=sentiment_label(score),  # type: ignore[arg-type]
                relevance=0.82,
                catalyst_type=catalyst,
                provider="demo-simulated",
            )
            for index, (symbol, headline, score, catalyst) in enumerate(demo_rows)
        ]
        return IntelligenceState(
            news=news,
            filings=[],
            macro=[],
            aggregate_sentiment=round(sum(item.sentiment_score for item in news) / len(news), 3),
            sources=[
                DataSourceStatus(
                    name="Demo intelligence",
                    configured=True,
                    available=True,
                    detail="Clearly labelled synthetic news evidence",
                    url="",
                )
            ],
        )
