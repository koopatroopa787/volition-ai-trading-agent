# Free API strategy map

This document keeps Volition's data architecture narrow and auditable. The
community-maintained Public APIs directory is a discovery source, not a data
quality guarantee. Every production adapter below is checked against the
provider's own documentation and must fail closed.

## Source-of-truth hierarchy

1. **Alpaca Trading API and CLI** — account, positions, stock bars, option
   contracts, option snapshots, orders, and paper execution. No other service
   may replace Alpaca for a tradable price or contract symbol.
2. **Official public data** — SEC EDGAR filings and FRED macro series. These
   provide context and risk vetoes, never executable prices.
3. **Optional free enrichment** — MarketAux sentiment and Portfolio Optimizer.
   The application remains functional when either service is missing or down.
4. **Local deterministic analysis** — sentiment fallback, indicators,
   backtests, Monte Carlo, calibration, and learning history. Calculations that
   can be performed reproducibly in-process should not depend on a remote API.

## Strategy families and evidence

| Strategy family | Options structure | Primary evidence | Free enrichment | Promotion test |
|---|---|---|---|---|
| Trend continuation | Bull call or bear put debit spread | Alpaca bars, option chain, Greeks | News sentiment confirmation | Walk-forward return, drawdown, calibration |
| High-IV mean reversion | Iron condor or one-sided credit spread | Alpaca IV/quotes, realized volatility | FRED risk overlay | Tail loss, CVaR, fill/slippage stress |
| Pre-catalyst volatility | Long straddle or strangle | Alpaca option chain and news cadence | MarketAux sentiment dispersion, SEC filing events | Realized move versus implied move |
| Filing catalyst | Directional debit spread or no-trade | SEC 8-K/10-Q/10-K plus Alpaca price reaction | MarketAux entity sentiment | Event-study out-of-sample replay |
| Macro risk transition | Defined-risk index vertical or cash | SPY/QQQ bars and options | FRED VIX, 10Y yield, high-yield spread | Regime precision and worst-decile loss |
| Correlation/concentration control | Resize, substitute, or reject | Alpaca positions and returns | Portfolio Optimizer | Portfolio CVaR and marginal risk contribution |

Every family includes an explicit no-trade outcome. The learner may propose
parameter changes or a challenger version, but cannot alter hard risk limits.

## Provider plan

### Alpaca

- Existing and mandatory for the hackathon.
- Use IEX stock data and indicative options data within the account's
  entitlements.
- Cache option chains briefly, but refresh before any order preview.

### MarketAux

- Optional free account: currently 100 requests per day and three articles per
  response.
- One batched watchlist request every 15 minutes uses at most 26 requests in a
  regular market day.
- Entity sentiment is evidence only. If unavailable, use Alpaca News plus the
  transparent local scorer.
- Environment variable: `MARKETAUX_API_TOKEN`.

### SEC EDGAR

- Official, keyless JSON APIs for company submissions and XBRL facts.
- Request an identifying `User-Agent`, respect SEC fair-access guidance, cache
  the ticker map for 24 hours, and cache submissions for six hours.
- Initially ingest 8-K, 10-Q, 10-K, 6-K, 20-F, and 40-F forms.
- Environment variable: `SEC_USER_AGENT`.

### FRED

- Official macroeconomic API with a free API key.
- Initial regime set: VIX (`VIXCLS`), 10-year Treasury yield (`DGS10`), and US
  high-yield spread (`BAMLH0A0HYM2`).
- Cache hourly and display the required FRED attribution. Macro signals can
  reduce size or veto a trade; they cannot create a trade alone.
- Environment variable: `FRED_API_KEY`.

### Portfolio Optimizer

- Free and does not require registration.
- Planned use: send only an anonymous covariance matrix and constraints, never
  account identifiers, positions, keys, or decision history.
- Keep a local optimizer fallback because external portfolio math must not be
  a trading-loop dependency.

## Reliability requirements

- All external adapters use strict timeouts, bounded response sizes, caching,
  typed parsing, and graceful fallback.
- Provider errors are shown as degraded evidence, not hidden.
- No optional API can bypass liquidity, max-loss, drawdown, permission, market
  window, concentration, or kill-switch gates.
- API keys remain server-side and are never returned to the browser, logged,
  embedded in URLs shown to users, or written into decision passports.

## Delivery sequence

1. News Intelligence and sentiment confirmation — implemented.
2. SEC filing catalysts and FRED macro adapters — implemented, activate by
   adding the free provider identity/key.
3. Live four-market board using cached Alpaca IEX bars, with freshness and
   source labels — implemented.
4. Monte Carlo distribution engine with probability of profit, expected P&L,
   CVaR, risk-limit hit probability, percentile outcomes, scenario conditions,
   break-even regions, and milestone probabilities — implemented for every
   watchlist symbol.
5. Risk-aware construction that falls back to a lower-cost structure or cash
   when one contract exceeds the per-trade budget — implemented.
   Research-only lab challengers remain available when execution says cash.
6. Grouped option-position monitor and lifecycle surface — implemented; exit
   and roll actions remain locked until a verified paper fill exists.
7. Evidence-only Learning Journal — collection and gate-calibration foundation
   implemented. Verified close-out attribution and champion/challenger
   promotion remain.
8. Historical walk-forward Strategy Lab using Alpaca bars and explicit option
   payoff/fill assumptions — remaining.
9. Correlation-aware portfolio guard, with Portfolio Optimizer as optional
   verification and a local deterministic implementation as fallback —
   remaining.
