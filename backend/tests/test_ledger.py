from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import demo_account, demo_markets
from ledger import DecisionLedger, ExecutionLedger
from models import DecisionPassport, DecisionStatus, ExecutionEvent, ExecutionReceipt
from strategy import StrategyRouter


class DecisionLedgerTests(unittest.TestCase):
    def test_hash_chain_detects_no_mutation(self) -> None:
        market = demo_markets()[0]
        plan = StrategyRouter().route(market, demo_account(), 1.25)
        with tempfile.TemporaryDirectory(prefix="volition-ledger-") as directory:
            ledger = DecisionLedger(Path(directory) / "decisions.jsonl")
            first = ledger.append(
                DecisionPassport(
                    decision_id="decision-1",
                    cycle_id="cycle-1",
                    symbol="SPY",
                    status=DecisionStatus.PREVIEWED,
                    plan=plan,
                    receipt=ExecutionReceipt(mode="preview", accepted=True),
                )
            )
            second = ledger.append(
                DecisionPassport(
                    decision_id="decision-2",
                    cycle_id="cycle-2",
                    symbol="SPY",
                    status=DecisionStatus.NO_TRADE,
                    plan=plan.model_copy(update={"quantity": 0}),
                    receipt=ExecutionReceipt(mode="preview", accepted=False),
                )
            )
            self.assertEqual(second.previous_hash, first.record_hash)
            valid, count = ledger.verify()
            self.assertTrue(valid)
            self.assertEqual(count, 2)

    def test_verification_rejects_malformed_trailing_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-ledger-corrupt-") as directory:
            ledger = DecisionLedger(Path(directory) / "decisions.jsonl")
            market = demo_markets()[0]
            ledger.append(
                DecisionPassport(
                    decision_id="decision-1",
                    cycle_id="cycle-1",
                    symbol="SPY",
                    status=DecisionStatus.PREVIEWED,
                    plan=StrategyRouter().route(market, demo_account(), 1.25),
                    receipt=ExecutionReceipt(mode="preview", accepted=True),
                )
            )
            with ledger.path.open("a", encoding="utf-8") as handle:
                handle.write('{"truncated":')
            self.assertEqual(ledger.verify(), (False, 1))

    def test_old_pending_order_survives_more_than_two_thousand_events(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-order-history-") as directory:
            ledger = ExecutionLedger(Path(directory) / "order_events.jsonl")
            ledger.append(
                ExecutionEvent(
                    event_id="pending-entry",
                    symbol="SPY",
                    kind="order_submitted",
                    status="new",
                    order_id="broker-pending",
                    message="working",
                )
            )
            for index in range(2_001):
                ledger.append(
                    ExecutionEvent(
                        event_id=f"review-{index}",
                        symbol="QQQ",
                        kind="review",
                        status="risk_veto",
                        message="no order",
                    )
                )
            self.assertEqual([event.order_id for event in ledger.active_orders()], ["broker-pending"])

    def test_definite_broker_rejection_clears_write_ahead_intent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="volition-rejected-intent-") as directory:
            ledger = ExecutionLedger(Path(directory) / "order_events.jsonl")
            ledger.append(ExecutionEvent(
                event_id="intent", symbol="SPY", kind="order_intent",
                status="submission_unknown", client_order_id="volition-test-intent",
                message="pending",
            ))
            ledger.append(ExecutionEvent(
                event_id="rejected", symbol="SPY", kind="order_update",
                status="http_422", client_order_id="volition-test-intent",
                message="rejected",
            ))
            self.assertEqual(ledger.active_orders(), [])


if __name__ == "__main__":
    unittest.main()
