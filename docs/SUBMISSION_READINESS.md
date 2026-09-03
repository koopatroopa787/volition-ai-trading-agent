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
- [x] Private Qwen reasoning through an OpenAI-compatible local endpoint
- [x] Broker-sourced portfolio history with an aligned SPY benchmark
- [x] Paper-mode operator endpoints fail closed without an operator key
- [x] Automated backend tests, frontend typecheck, and production build
- [x] Required one-page architecture explanation
- [x] HTTPS deployment with persistent backend and private-model storage
- [x] Finished cover image and nine-slide presentation
- [x] Final logged-out desktop and mobile visual pass with no console errors

## Must be completed before final submission

- [x] Configure and verify a private reasoning model; capture the UI showing **Private model**
- [ ] Explicitly enable paper submission only when the team is ready, then capture at least one broker-verified submitted → filled → managed-exit lifecycle
- [ ] Rotate every credential ever pasted into chat before final public submission
- [ ] Switch the staged GitHub repository to public after explicit approval; the private repository is uploaded, secret-checked, and passing CI
- [x] Deploy backend and frontend over HTTPS with production CORS and persistent backend storage
- [ ] Add the hosted URL, repository URL, Alpaca paper account ID, cover image, video, and finished slide deck to the hackathon submission form
- [x] Record the narrated walkthrough and commit the script plus 1280×720 MP4

## Evidence to capture

1. Alpaca account page: account ID, creation date, $100,000 start, Level 3.
2. Terminal: `alpaca account get` and one option-chain command; redact credentials.
3. Volition Overview: order authority, heartbeat, cross-asset tape, shortlist, and guardrails.
4. Performance: Alpaca equity history, SPY benchmark, P&L, and drawdown.
5. Strategy Lab: one eligible candidate and one risk-blocked candidate.
6. Journal: private-model labels, veto, audit hash, and lifecycle event.
7. Alpaca Orders: matching client order ID and broker status for any submitted paper order.

Do not claim live autonomy, realised P&L, or self-learning performance until those exact broker receipts exist.
