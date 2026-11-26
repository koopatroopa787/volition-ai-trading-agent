# Volition's rules on 33 months of option history

**Question.** Do the live trading rules make money over a long period? Would a walk-forward
search over exit rule, expiry and the bearish switch find better ones?

**Answer.**
* The live rules lost **−$9,965 over 135 trades** (Mar 2024 – Nov 2026, $100k of equity) once
  bid/ask costs are paid.
* At mid prices the same trades **break even (+$379)**. The rules have about zero edge before
  costs, and the spread is what loses the money.
* The walk-forward search lost less out of sample than the live rules (−$836 against −$3,766).
  The gap is not statistically significant, so the live rules stay unchanged.

## Data and method

`backend/tools/history_pull.py` downloads the data from Alpaca. `backend/tools/history_backtest.py`
rebuilds the bot's view at every close and replays it.

| | |
|---|---|
| Period | 2024-03-01 to 2026-11-25 (Alpaca option bars start Feb 2024) |
| Universe | SPY, QQQ, IWM, DIA, NVDA, AAPL, AMD |
| Option data | daily bars for every contract of the targeted weekly expiry, ±16% strikes (25 MB xz) |
| Decisions | the real `StrategyRouter`, risk gates and Monte Carlo stress test, clock pinned to each close |
| Greeks | Black-Scholes implied vol and greeks solved from each contract's close |
| Fills | close ± half the median live spread for that symbol and price level (measured on archived Oct–Nov 2026 quotes) |
| Exits | take-profit / stop on defined risk, else close at ≤5 DTE; never-quoted legs settle at intrinsic |

**Known gaps.** Each one is stated in the tool's docstring.
* **No historical quotes:** the spread is a model, not a recorded price.
* **No historical open interest:** that gate is neutral.
* **News only from 2026-04-29:** Alpaca's news archive starts then. Before that, sentiment and
  the catalyst score are 0, so long straddles cannot trigger.
* **One decision per day:** the live bot decides every 15 minutes; the backtest decides once per day.

## Results: live rules

The live rules are take +25%, stop −20%, 21 DTE target, bearish spreads off.

| Strategy | Trades | P&L |
|---|---|---|
| Bull call spread | 85 | −$5,703 |
| Iron condor (index ETFs) | 37 | −$2,120 |
| Long strangle | 13 | −$2,142 |
| **All** | **135** | **−$9,965** (win rate 39%, max drawdown $10,106) |

Most candidate trades never open:
* "no aligned edge": 1,471
* bearish switch off: 968
* stress test: 946
* over budget: 494
* position lock: 409
* chain liquidity: 297

## Results: sensitivity of the live rules

| Variant | Trades | P&L |
|---|---|---|
| Fine-tuned sentiment model (deployed) | 135 | −$9,965 |
| Old keyword lexicon | 129 | −$8,601 |
| No sentiment | 126 | −$8,647 |
| **Mid fills (no spread)** | 207 | **+$379** |
| Double spreads | 56 | −$5,081 |

* **Costs decide the outcome.** At mid prices, bull call spreads turn positive (+$1,988 over
  158 trades). More trades also pass the spread gate.
* **The news scorer barely matters here.** News exists only for the last 7 months. The
  $1.4k gap between the three scorers is a handful of trades, so this test cannot tell them
  apart.
* **Fills are the real lever.** Live orders already go out as limit orders at mid
  (`services/alpaca_rest.py`). So the number to measure next is how often those limits
  actually fill, and how far from mid.

## Results: evolution (96 configurations)

The grid covers:
* take-profit: 25 / 50 / 75% / none;
* stop: 20 / 35 / 50% / none;
* target DTE: 14 / 21 / 28;
* bearish spreads: off / on.

What the grid shows:
* **Bearish spreads lose in every one of the 48 pairs.** Over the full period they lose
  $5k–$32k more than the matching bearish-off configuration. This confirms the switch-off.
* **Only 3 of 96 configurations end positive.** All three use 21 DTE with no take-profit. The
  best in-sample, "hold to 5 DTE, no take or stop", made +$1,886.

**Walk-forward.** Each fold picks the best training P&L among configurations with ≥20 trades,
using only trades opened before the fold. It is then scored only on trades opened inside it.

| Fold | Chosen | Chosen P&L | Live P&L | Median config |
|---|---|---|---|---|
| 2025-03 → 2025-09 | take 25 / stop 20 / 14 DTE | −$156 | +$2,976 | +$1,190 |
| 2025-09 → 2026-03 | hold, no take/stop, 21 DTE | +$25 | −$3,168 | −$2,117 |
| 2026-03 → 2026-11 | hold, no take/stop, 21 DTE | −$705 | −$3,574 | −$3,088 |
| **Total** | | **−$836** | **−$3,766** | |

**Is "hold to 5 DTE" really better?** Out of sample (from 2025-03) it averages +$138.5 per
trade (n=48, sd $828) against −$36.9 for the live rules (n=102, sd $371). Bootstrapping the
per-trade mean difference gives:
* +$175;
* 95% CI −$62 to +$423;
* P(no improvement) = 0.077.

That is suggestive, not established. Most of the gain comes from the 2025 rebound, and
holding roughly doubles the per-trade spread of outcomes. With 96 configurations tried, the
live rules stay as they are. The candidate to watch is the hold rule, ideally in a paper A/B test.

## Reproduce

```bash
# on the server (Alpaca keys in the env file), about 55 minutes
docker run --rm --env-file /opt/volition/.env -v $PWD/history:/out deploy-backend python -m tools.history_pull /out
# locally, from backend/, about 30 minutes
VOLITION_SENTIMENT_MODEL=../research/news_sentiment/onnx python -m tools.history_backtest history --output report.json
```

`report.json` here is the run behind these numbers: every configuration's summary, the folds
and the variants.
