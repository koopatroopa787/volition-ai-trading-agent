# Volition demo script (2½ minutes)

## 0:00–0:20 — The promise

“Volition is an autonomous options risk desk. It does not stop at a recommendation: it decides, can refuse, submits only atomic paper structures, reconciles the broker, manages exits, and remembers only verified outcomes.”

Show the paper account, $100,000 portfolio value, Level 3 approval, **Order authority**, scheduler heartbeat, and **Private model** label.

## 0:20–0:45 — Broad evidence, focused computation

Open **Market pulse**. Show S&P 500 and Nasdaq proxies, commodities, rates, sectors, and leaders. Explain that 40 instruments provide context while a 20-symbol options universe is reranked and only five full option chains are loaded each cycle.

## 0:45–1:20 — A proposal that is allowed to fail

Open **Strategy Lab**, choose JPM, and run the current-evidence scenario. Point to:

- the price fan and probability regions;
- expected P&L, value at risk, expected shortfall, and spread cost;
- the red **Risk blocked** label;
- the exact liquidity and stress-test failures.

Say: “The simulation never grants permission. It can only add another veto.”

Briefly show **Performance**: the broker-sourced equity line, aligned SPY
benchmark, and honest zero-P&L starting state. “This is account evidence, not a
backtest substituted for real results.”

## 1:20–1:45 — The autonomous decision

Return to **Overview** and run one market review. Explain that submission is intentionally locked for the demonstration account. Show the selected structure, committee support, deterministic gate count, and the precise reason no order was sent.

## 1:45–2:10 — Lifecycle, not a fake fill

Open **Decision journal**. Show the new activity event, decision passport, per-role model source, and audit hash. Explain:

“If execution is enabled, acceptance becomes submitted—not filled. Volition then reconciles Alpaca status, prevents duplicate entries, syncs positions, and sends an atomic close at the profit, loss, or expiration boundary.”

## 2:10–2:30 — Honest learning and close

Show **Closed outcomes 0 / 5** and the learning statement.

“Volition does not learn from imaginary returns. Strategy promotion starts only after five broker-verified closes. The product is designed to be judged on safe autonomy, traceability, and actual paper results—not a lucky backtest screenshot.”
