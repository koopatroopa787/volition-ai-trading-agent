# Alpaca AI Trading Agents Hackathon — research and product strategy

Research snapshot: 30 August 2026

## What is actually required

The official challenge is narrower than a general trading dashboard. A valid entry must:

- Run an **autonomous AI trading agent** on Alpaca's Trading API.
- Use **Alpaca MCP or Alpaca CLI**.
- Incorporate **options trading in every strategy**.
- Develop and test in **paper trading**, not with real capital.
- Submit a **brand-new Alpaca paper account**, dedicated to the hackathon, with an initial balance of exactly **$100,000**. Reused accounts are ineligible.
- Include the paper account ID and a **one-page write-up** explaining AI logic, risk gates, and Alpaca infrastructure.
- Submit a public repository, hosted app URL, video, slide deck, cover image, and project descriptions.

The official judging dimensions are P&L performance, technology implementation, creativity/originality, and presentation/execution. The event runs 28 August–4 September 2026 and submissions close at 11:00 ET on 4 September. Source: [official hackathon page](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon).

## What the judges are likely to notice

These are evidence-backed product inferences, not claims about private judging preferences.

| Judge | Public evidence | Likely product signal |
| --- | --- | --- |
| Pawel Czech, CEO of NativelyAI | Publicly emphasizes intent-driven orchestration, event-driven AI-native systems, model/compute efficiency, and production-ready agentic products. [theCUBE interview](https://www.thecube.net/events/nyse/ai-factory-data-center-of-the-future/content/Videos/c55ec3e1-d594-4b3d-a382-309426a358e7) | A working, production-shaped system with clear orchestration, cost/resilience fallbacks, and more substance than a prompt wrapper. |
| Chiranjeev Shah, Alpaca technical content | Authored a detailed multi-agent trading system with separate research, policy, execution, monitoring, kill switch, approval tokens, and an observable dashboard. [NightWatcher V2](https://alpaca.markets/learn/building-nightwatcher-v2-a-multi-agent-trading-system-with-alpaca) | Clean boundaries, explicit “no trade,” strong risk controls, readable architecture, and a demo that explains itself. A copy of the familiar sentiment-agent pattern will not stand out. |
| Tony Lee, Alpaca Chief Brokerage Officer | Public profile centers on brokerage, derivatives, banking, fintech, and data; Alpaca quotes him on expanding a one-stop platform across markets. [Alpaca announcement](https://alpaca.markets/blog/alpaca-registers-as-futures-commission-merchant-with-the-cftc-and-nfa-to-advance-launch-of-prediction-markets/) | Correct options mechanics, account permissions, buying power, assignment/expiry handling, and operational discipline—not just an attractive forecast. |
| Grace Gao, Alpaca Product Manager | Co-led an end-to-end “AI-powered hedge fund” workshop covering paper-account setup, strategy choice, deployment, running the algo, and the next step. [Alpaca Techweek thread](https://x.com/AlpacaHQ/status/2062584379659161651) | A short path from setup to visible value, understandable autonomous controls, and an end-to-end product story. |
| Brandon Meyerowitz, Team Lead, Trading API | Co-led the same live build, including Alpaca CLI authentication, connecting an agent, placing a paper trade, and deploying it. He leads the Trading API team. [Alpaca Techweek thread](https://x.com/AlpacaHQ/status/2062584379659161651) | Real API/CLI depth, structured receipts, correct order lifecycle, idempotency, error handling, and proof that Alpaca is central rather than bolted on. |

## The crowded baseline

Alpaca already showcases multi-agent systems with specialist analysts, a critic, deterministic risk checks, position monitoring, and an approval gate. One published reference explicitly says its next planned layer is a dedicated options agent. [Alpaca multi-agent reference](https://alpaca.markets/learn/building-a-multi-agent-ai-trading-system-on-alpaca).

That makes these ideas insufficiently distinctive on their own:

- “Five agents debate a stock.”
- A generic sentiment/news scanner.
- A chat UI that recommends a call or put.
- Backtests without paper-account execution evidence.
- An LLM directly calling an order endpoint with a prompt-based stop loss.
- A dashboard that hides rejected trades and only shows winners.

## Chosen concept: Volition

**Volition is an autonomous, audit-first options risk desk.** It uses market regime, realized volatility, Alpaca option-chain Greeks/IV, catalysts, liquidity, account state, and existing exposure to select a concrete defined-risk structure—or cash.

The initial strategy router supports:

- Bull call debit spread for strong positive trends.
- Bear put debit spread for strong negative trends.
- Iron condor for high-implied-volatility, range-bound regimes.
- Long straddle for low-volatility pricing ahead of a high-confidence catalyst.
- No trade when the evidence, liquidity, account permissions, expiry window, or risk budget is inadequate.

This is feasible because Alpaca's option chain exposes quotes, implied volatility, and Greeks through its SDK/API, while its MCP server supports single- and multi-leg option orders. Sources: [Alpaca Python option-data reference](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html), [Trading MCP Server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server), and [Options Level 3 Trading](https://docs.alpaca.markets/us/docs/options-level-3-trading).

## Differentiators that map to the scorecard

### P&L performance

- The agent can express directional, range, and long-volatility regimes rather than forcing one strategy.
- Risk is normalized by maximum possible loss, not by premium or contract count alone.
- “No trade” and a portfolio-wide daily loss circuit breaker defend the short seven-day competition window.
- A counterfactual record of rejected alternatives makes it possible to show whether the router—not luck—added value.

### Technology implementation

- Alpaca CLI supplies observable, structured account and option-chain evidence.
- Alpaca Trading API submits an atomic `mleg` paper order with concrete OCC symbols and position intents.
- A private Qwen 2.5 model served by Ollama runs independent regime, volatility, and adversarial opinions with visible per-opinion provenance.
- The deterministic constitution has final veto authority and cannot be overridden by a model response.
- Decision passports bind evidence, agent opinions, gates, order IDs, and hashes into an auditable ledger.

### Creativity and originality

- The unit of product value is a **decision passport**, not a chat answer.
- Rejections and cash decisions are first-class outcomes.
- The model never invents option contracts; it judges a structure built from live chain evidence.
- Every short option leg must be paired into a finite-risk structure.

### Presentation and execution

- The dashboard opens with competition P&L, open risk, agent state, and whether data is demo or live paper evidence.
- One click runs the full autonomous cycle and opens the resulting passport.
- A kill switch and risk constitution are visible on the main screen.
- The demo can run without credentials, but simulated values are conspicuously labelled and cannot be mistaken for competition results.

## Non-negotiable options mechanics

Alpaca documents that options have separate approval levels, options buying power, integer quantities, day time-in-force, expiry/assignment behaviors, and multi-leg validation. Expiring positions may be liquidated or exercised depending on moneyness and buying power. The product must therefore include expiry windows, account-level checks, position intent, and an explicit close/roll policy. Source: [Options Trading Overview](https://docs.alpaca.markets/us/docs/options-trading-overview).

## Build order for the remaining hackathon window

1. Complete the paper-safe execution loop and deterministic risk tests.
2. Complete the dashboard and decision-passport walkthrough.
3. Connect the brand-new $100,000 paper account and confirm Level 3 options.
4. Run small-risk cycles, collect genuine Alpaca receipts, and tune only with out-of-sample/live-paper evidence.
5. Freeze core strategy logic before the final day; spend the remaining time on resilience, the one-page write-up, video, deck, and public build posts.

The strategy should not maximize trade frequency. In a seven-day judged competition, one catastrophic options mistake is more damaging than several disciplined no-trades.
