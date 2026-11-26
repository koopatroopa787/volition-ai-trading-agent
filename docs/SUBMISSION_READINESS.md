# Submission readiness

## Already evidenced in the product

- [x] Dedicated competition-period Alpaca paper account and $100,000 configured start
- [x] Options Level 3 account evidence
- [x] Alpaca CLI evidence plane and Trading API integration
- [x] Options-only autonomous research/decision architecture
- [x] 20-symbol trading universe plus 40-instrument cross-asset context
- [x] Atomic multi-leg payloads with correct debit/credit signing
- [x] Deterministic structural, account, drawdown, sizing, liquidity, expiry, freshness, duplicate, simulation, and committee gates
- [x] Persistent scheduler heartbeat and kill switch
- [x] Broker order reconciliation and managed-exit implementation
- [x] Hash-chained decisions and append-only lifecycle events
- [x] Monte Carlo scenario lab with explicit limitations
- [x] Honest reasoning-source and realised-outcome labels
- [x] Automated backend tests, frontend typecheck, and production build
- [x] Required one-page architecture explanation

## Must be completed before final submission

- [ ] Configure a supported external reasoning model and capture the UI showing **External model**
- [ ] Explicitly enable paper submission only when the team is ready, then capture at least one broker-verified submitted → filled → managed-exit lifecycle
- [ ] Rotate every credential ever pasted into chat before a public repository or hosted deployment
- [ ] Create the public GitHub repository and verify no `.env`, runtime state, or decision ledger is tracked
- [ ] Deploy backend and frontend over HTTPS with production CORS and persistent backend storage
- [ ] Add the hosted URL, repository URL, Alpaca paper account ID, cover image, video, and slide deck to the hackathon submission
- [ ] Record the 2½-minute demo using `docs/DEMO_SCRIPT.md`
- [ ] Verify the submission from a logged-out/incognito browser and a mobile viewport

## Evidence to capture

1. Alpaca account page: account ID, creation date, $100,000 start, Level 3.
2. Terminal: `alpaca account get` and one option-chain command; redact credentials.
3. Volition Overview: order authority, heartbeat, cross-asset tape, shortlist, and guardrails.
4. Strategy Lab: one eligible candidate and one risk-blocked candidate.
5. Journal: committee model labels, veto, audit hash, and lifecycle event.
6. Alpaca Orders: matching client order ID and broker status for any submitted paper order.

Do not claim live autonomy, realised P&L, or self-learning performance until those exact broker receipts exist.
