# Volition — AI logic, risk gates, and Alpaca infrastructure

## Product

Volition is an autonomous options risk desk running on a dedicated $100,000 Alpaca paper account. Every cycle gathers account state, existing exposure, stock bars, news, and an option chain; classifies the market regime; constructs a defined-risk options strategy; asks three independent AI roles to support or oppose it; applies deterministic risk gates; and either submits one atomic multi-leg paper order or records why cash was the better decision.

## AI logic

The Regime Sentinel tests directional evidence, the Volatility Architect tests implied-versus-realized volatility and structure fit, and the Adversarial Skeptic searches for a reason not to trade. A private Qwen 2.5 3B model served by Ollama runs these opinions independently on the deployment host. A deterministic strategy router—not the language model—selects actual OCC contracts from Alpaca chain data and calculates quantity, debit/credit, maximum loss, maximum profit, delta, expiry, and liquidity. The model can support, oppose, or abstain; it cannot invent a contract or bypass a gate.

Supported decisions are bull call spreads, bear put spreads, iron condors, long straddles, and no trade. Regime rules deliberately choose cash when direction and volatility do not provide a sufficient edge.

## Risk constitution

The final authority is unit-tested Python with no model in the loop. It blocks a proposal when any of these conditions fail:

- Paper environment and active account.
- Options approval level required by the structure.
- Finite maximum loss and no naked short option legs.
- Maximum risk per trade of 1.25% of equity.
- Maximum aggregate open risk of 8% of equity.
- Daily loss circuit breaker at −2%.
- Maximum four open structures.
- Expiration between 7 and 35 days.
- Minimum open interest of 500 per leg.
- Maximum bid/ask spread of 8% per leg.
- Market open and kill switch clear. AI committee opinions are recorded as advisory evidence; they cannot replace or bypass deterministic permission checks.

Every accepted, rejected, or no-trade outcome produces a hash-chained decision passport with the evidence snapshot, three opinions, all gate results, proposed legs, and the execution receipt. This makes the agent's behavior explainable and tamper-evident.

## Alpaca infrastructure

Alpaca is both the evidence and execution plane. The Alpaca CLI returns structured JSON for account state, positions, news, and option chains with Greeks/IV. Historical bars come from Alpaca Market Data. Passing structures are submitted to the Alpaca paper Trading API as atomic `mleg` orders with explicit `buy_to_open` / `sell_to_open` intents and client order IDs. Order IDs and statuses are written back to the passport.

The build has no live-trading endpoint. It defaults to a conspicuously labelled simulated demo. Paper submission requires a dedicated configuration switch, valid paper keys, the hackathon account ID, and every risk gate to pass.

Paper trading is simulated and does not involve real funds. This project is for educational purposes only and is not investment advice.
