from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import demo_account, demo_markets
from ledger import DecisionLedger
from models import DecisionPassport, DecisionStatus, ExecutionReceipt
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


if __name__ == "__main__":
    unittest.main()
