# Volition — AI logic, risk gates, and Alpaca infrastructure

## The problem

Most trading agents can recommend a trade, but a production-style agent must also know when to do nothing, prove where its data came from, keep a model from bypassing risk, reconcile what the broker actually did, and manage the position after entry. Volition is a paper-only options risk desk built around that full lifecycle.

## How the autonomous agent works

Every 15 minutes during the regular US session, Volition ranks a 20-symbol liquid universe using lightweight Alpaca market evidence. It deep-scans five names, loads their option chains, classifies trend and volatility, and routes each market to a concrete defined-risk structure: bull call spread, bear put spread, iron condor, long straddle/strangle, or cash.

Three independent committee roles then challenge the proposal: Regime Sentinel, Volatility Architect, and Adversarial Skeptic. The hosted build runs Qwen 2.5 3B privately through an OpenAI-compatible Ollama endpoint; each role receives only bounded market and structure evidence and returns a structured support/oppose opinion. A deterministic fallback preserves the risk loop if inference fails, and the interface labels which source produced every opinion. The model cannot invent option symbols, resize the order, or override a gate.

A reproducible Monte Carlo stress test generates 2,500 underlying paths for the live loop (up to 20,000 in Strategy Lab), settles every proposed leg at expiry, includes estimated spread cost, and measures probability of profit, expected P&L, 95% value at risk, expected shortfall, and near-max-loss probability. It is a stress test—not a price forecast—and may veto but never approve a trade by itself.

## Deterministic risk constitution

Before an order can reach Alpaca, code verifies:

- operator kill switch is clear and the regular market is open;
- dedicated paper account is active with the required options level;
- every short leg has mathematically valid protective geometry at the same expiry and ratio;
- maximum loss is finite and within per-trade and portfolio risk budgets;
- daily drawdown and open-position limits are clear;
- expiry is 7–35 DTE, quotes are fresh, open interest is sufficient, and spreads are within tolerance;
- no active entry order already exists for that symbol;
- Monte Carlo thresholds pass and at least two committee roles support with no more than one opposing.

Any failure becomes a visible, hash-chained decision passport. “No trade” is an intended autonomous result.

## Alpaca infrastructure and lifecycle

Alpaca CLI is the observable evidence plane for the paper account, positions, option chains, Greeks, implied volatility, market clock, and news. The Alpaca Trading API submits one atomic `mleg` order only when paper execution is explicitly unlocked. Debit orders use positive limits and credit orders use Alpaca’s required negative signed limit.

An accepted order is labelled **submitted**, never filled. Volition polls broker order state and appends changes to a separate immutable lifecycle ledger. Filled positions are synced from Alpaca and checked for three bounded exit conditions: +25% of defined risk, −20% of defined risk, or five DTE. A qualifying exit reverses every leg with close intent in one atomic paper order. Active-order checks and an in-process lock prevent duplicate cycles and duplicate entry orders.

The scheduler’s heartbeat, next check, last result, failure count, order authority, reasoning source, decisions, and broker lifecycle are visible in the interface. Alpaca portfolio history is plotted against an aligned SPY benchmark with return, P&L, and drawdown. Runtime controls and the kill switch survive restarts. Public operator actions require a separate key and fail closed if it is absent. Paper submission is disabled by default and live trading is not implemented.

## Learning without pretending

Volition compares strategies using gate coverage while evidence accumulates. It does not call previews or rejected proposals “performance.” Promotion requires at least five broker-verified closed paper outcomes. Filled entries and filled exits are derived only from lifecycle receipts; until those exist, the product says that outcome learning is pending.

## Judge-visible proof

The demo shows a fresh $100,000 Alpaca paper account, Level 3 options approval, Alpaca CLI evidence, 40-instrument cross-asset pulse, a risk-blocked candidate, repeatable Strategy Lab scenarios, one safe autonomous review, the exact veto, scheduler activity, audit hash, and paper-order authority state.
