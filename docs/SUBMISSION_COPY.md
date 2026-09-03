# Volition — submission copy

## Project title

Volition

## Tagline

The autonomous options desk that knows when not to trade.

## One-line description

Volition is an Alpaca-native autonomous options desk where private AI interprets market evidence, deterministic code owns risk and permission, and every decision is backed by a broker receipt or an explicit veto.

## Full project description

Most trading-agent demos end at a recommendation. Volition begins where the hard part starts: deciding whether an options structure is actually permitted, submitting it safely, reconciling the broker response, managing its exit, and learning only from verified outcomes.

Every 15 minutes during the trading window, Volition ranks a 20-symbol universe using Alpaca market data and cross-asset context, then loads full option evidence for the strongest candidates. A deterministic router selects a defined-risk structure—bull call spread, bear put spread, iron condor, long-volatility position, or cash—from real Alpaca contracts. Three adversarial AI roles then examine the same bounded evidence: a Regime Sentinel challenges direction, a Volatility Architect challenges structure, and an Adversarial Skeptic argues for no trade.

The deployed committee runs privately on Qwen 2.5 3B through Ollama. The model can interpret, support, oppose, or abstain, but it cannot invent a contract, set position size, or grant itself permission. Final authority belongs to a deterministic risk constitution covering account status, options approval, defined-risk geometry, expiry, liquidity, quote freshness, drawdown, concentration, duplicate exposure, market window, and Monte Carlo stress. Simulation can veto a proposal; it can never promote one.

Passing structures are designed for atomic multi-leg submission to a dedicated $100,000 Alpaca paper account. Volition then reconciles broker state and applies profit, loss, and expiration exit policies. Every review—including “no trade”—creates a hash-chained decision passport containing evidence, agent opinions, option legs, gate results, and execution status. A separate append-only lifecycle stream records submissions, fills, cancellations, and exits, so a preview is never mislabelled as realised performance.

The product makes the entire system inspectable. The Overview shows paper-account state, scheduler health, order authority, performance against SPY, proposals, and guardrails. Market Pulse tracks 40 indices, commodities, rates, sectors, and leaders. Strategy Lab runs reproducible 1,000–20,000-path simulations with price fans, expiry P&L, probability of profit, VaR, expected shortfall, and near-max-loss probability. Intelligence combines news, sentiment, SEC filings, and FRED macro evidence. The Decision Journal exposes model provenance, vetoes, hashes, and broker lifecycle receipts.

Volition’s learning system is deliberately honest. It records previews and rejections as evidence, but strategy promotion remains locked until at least five broker-verified closed paper outcomes exist. The current public judge build is read-only and order submission is disabled; autonomous reviews continue server-side while anonymous visitors cannot place orders. That safety boundary is visible in the interface instead of hidden in a disclaimer.

## What makes it different

- Options are the product, not a stock signal with an options label.
- AI interprets evidence; tested code alone controls structure, risk, sizing, and permission.
- “No trade” is treated as a successful autonomous decision with a visible reason.
- Monte Carlo is a downside gate, not a source of fabricated confidence.
- Alpaca is both the evidence plane and the paper execution/lifecycle plane.
- Learning is based on broker-verified closes, never simulated or previewed P&L.
- The public demo is connected and continuously reviewing, while operator actions fail closed.

## Alpaca usage

- Dedicated fresh Alpaca paper account with a $100,000 starting balance and Options Level 3.
- Alpaca CLI evidence for account, positions, market clock, news, and option-chain inspection.
- Alpaca Market Data for stock bars, option contracts, Greeks, implied volatility, and benchmark data.
- Alpaca Trading API atomic `mleg` paper-order path with explicit leg intents and client order IDs.
- Alpaca portfolio history for broker-sourced performance aligned with SPY.
- Broker reconciliation for submitted, filled, cancelled, and exited lifecycle evidence.

## AI and safety architecture

- Private Qwen 2.5 3B model served locally by Ollama.
- Three adversarial committee roles with per-opinion provenance.
- Deterministic fallback that remains visible if inference is unavailable.
- Sixteen deterministic risk checks with final veto authority.
- Hash-chained decision passports plus append-only lifecycle events.
- No live-trading endpoint; paper submission requires multiple explicit server-side switches.
- Paper-mode operator endpoints require a server-held operator key and fail closed when it is absent.

## Current evidence — keep these numbers date-labelled

At the final preparation check, the fresh paper account held $100,000 in cash with no open positions. The system had recorded 53 autonomous reviews and 43 risk vetoes. Alpaca portfolio history supplied four aligned observations: account return was 0.00% versus approximately −0.54% for SPY over the same dates. These figures are evidence of system operation and benchmark integrity, not a claim of trading alpha. The first broker-verified entry → fill → managed-exit lifecycle remains intentionally unexecuted until the team explicitly authorises paper order submission.

## Technology

Python, FastAPI, Next.js, TypeScript, Alpaca CLI, Alpaca Trading and Market Data APIs, Ollama, Qwen 2.5, NumPy Monte Carlo simulation, Docker Compose, Caddy, Tailscale Funnel, Oracle Cloud, GitHub Actions.

## Submission fields

- Hosted demo: https://instance-20260318-1838.tail042e87.ts.net/
- Repository: **ADD PUBLIC GITHUB URL**
- Demo video: **ADD VIDEO URL**
- Slide deck: `docs/Volition_Hackathon_Deck.pptx`
- Cover image: `docs/Volition_Hackathon_Cover.png`
- Alpaca paper account ID: **ADD ACCOUNT ID TO FORM — DO NOT COMMIT CREDENTIALS**
- Team members: **ADD FINAL TEAM NAMES**

## Disclaimer

Paper trading is simulated and does not involve real funds. Volition is an educational hackathon project, not investment advice. Past or simulated results do not guarantee future performance.
