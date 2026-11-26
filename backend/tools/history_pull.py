"""Download the history the long backtest needs from Alpaca (option bars exist from Feb 2024).

Usage (inside the backend container, where the Alpaca keys are set):
    python -m tools.history_pull /out [--start 2024-03-01] [--symbols SPY,QQQ,...]

Writes, per symbol, xz-compressed files that the backtest reads offline:
* ``stocks.csv.xz``           symbol,date,close,volume  (daily, split/dividend adjusted, IEX like the live bot)
* ``options/SYM.csv.xz``      date,occ,close,vwap,volume,trades  (daily bars of every contract in the band)
* ``news/SYM.jsonl.xz``       id,created_at,headline,summary

The option band copies the live chain request: the weekly expiry the bot targets on each day,
strikes within 16% of any close between 35 days before expiry and expiry.
Re-running skips symbols whose option file already exists.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import lzma
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

DATA = "https://data.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
           "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", "")}
SYMBOLS = "SPY,QQQ,IWM,DIA,NVDA,AAPL,AMD"


def get(url: str, **params) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    for attempt in range(6):
        time.sleep(0.32)  # ponytail: fixed pacing under the 200 requests/minute data limit
        try:
            request = urllib.request.Request(f"{url}?{query}", headers=HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 5:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def paged(url: str, key: str, **params):
    token = None
    while True:
        payload = get(url, **params, page_token=token)
        yield payload.get(key) or ({} if key == "bars" else [])
        token = payload.get("next_page_token")
        if not token:
            return


def stock_closes(symbol: str, start: date) -> list[tuple[str, float, float]]:
    rows = []
    for bars in paged(f"{DATA}/v2/stocks/{symbol}/bars", "bars", timeframe="1Day", start=start.isoformat(),
                      limit=10_000, adjustment="all", feed="iex"):
        rows += [(bar["t"][:10], bar["c"], bar["v"]) for bar in bars]
    return rows


def expiries(start: date, end: date) -> list[date]:
    friday = start + timedelta(days=(4 - start.weekday()) % 7)
    return [friday + timedelta(weeks=week) for week in range((end - friday).days // 7 + 1)]


def contracts(symbol: str, friday: date, low: float, high: float) -> list[str]:
    found: dict[str, str] = {}
    for status in ("inactive", "active"):
        for page in paged(f"{PAPER}/v2/options/contracts", "option_contracts", underlying_symbols=symbol,
                          status=status, expiration_date_gte=(friday - timedelta(days=1)).isoformat(),
                          expiration_date_lte=friday.isoformat(), strike_price_gte=f"{low:.2f}",
                          strike_price_lte=f"{high:.2f}", limit=10_000):
            found.update({item["symbol"]: item["expiration_date"] for item in page})
    if not found:
        return []
    last = max(found.values())  # the Friday, or Thursday when Friday is a holiday; skip Thursday dailies
    return sorted(occ for occ, expiry in found.items() if expiry == last)


def pull_symbol(symbol: str, closes: list[tuple[str, float, float]], start: date, out: Path) -> None:
    target = out / "options" / f"{symbol}.csv.xz"
    if target.exists():
        print(symbol, "options already pulled")
        return
    by_day = {day: close for day, close, _ in closes}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "occ", "close", "vwap", "volume", "trades"])
    today = date.today() - timedelta(days=1)  # the free data plan refuses same-day option data (HTTP 403)
    for friday in expiries(start + timedelta(days=21), today + timedelta(days=35)):
        window_start = friday - timedelta(days=35)
        window = [close for day, close in by_day.items() if window_start.isoformat() <= day <= friday.isoformat()]
        if not window:
            continue
        occs = contracts(symbol, friday, min(window) * 0.84, max(window) * 1.16)
        for chunk in range(0, len(occs), 100):
            for bars in paged(f"{DATA}/v1beta1/options/bars", "bars", symbols=",".join(occs[chunk:chunk + 100]),
                              timeframe="1Day", start=max(window_start, start).isoformat(),
                              end=min(friday, today).isoformat(), limit=10_000):
                for occ, rows in bars.items():
                    writer.writerows((bar["t"][:10], occ, bar["c"], bar.get("vw"), bar["v"], bar.get("n"))
                                     for bar in rows)
        print(symbol, friday, len(occs), "contracts", flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    with lzma.open(partial, "wt", encoding="utf-8", preset=6) as handle:
        handle.write(buffer.getvalue())
    partial.replace(target)


def pull_news(symbol: str, start: date, out: Path) -> None:
    target = out / "news" / f"{symbol}.jsonl.xz"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    count = 0
    with lzma.open(partial, "wt", encoding="utf-8") as handle:
        for page in paged(f"{DATA}/v1beta1/news", "news", symbols=symbol, start=f"{start.isoformat()}T00:00:00Z",
                          limit=50, sort="asc"):
            for item in page:
                handle.write(json.dumps({key: item.get(key) for key in ("id", "created_at", "headline", "summary")})
                             + "\n")
                count += 1
    partial.replace(target)
    print(symbol, count, "headlines", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 3, 1))
    parser.add_argument("--symbols", default=SYMBOLS)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    symbols = args.symbols.split(",")
    # 70 calendar days of warm-up so the 20-day trend and volatility exist on the first trading day.
    closes = {symbol: stock_closes(symbol, args.start - timedelta(days=70)) for symbol in symbols}
    with lzma.open(args.out / "stocks.csv.xz", "wt", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "date", "close", "volume"])
        for symbol, rows in closes.items():
            writer.writerows((symbol, *row) for row in rows)
    for symbol in symbols:
        pull_news(symbol, args.start - timedelta(days=14), args.out)
        pull_symbol(symbol, closes[symbol], args.start, args.out)


if __name__ == "__main__":
    main()
