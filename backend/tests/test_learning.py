from __future__ import annotations

import unittest

from learning import LearningEngine
from models import (
    DecisionPassport,
    DecisionStatus,
    ExecutionEvent,
    ExecutionReceipt,
    GateResult,
    StrategyKind,
    TradePlan,
)


def decision(status: DecisionStatus, strategy: StrategyKind, blocked: bool = False) -> DecisionPassport:
    return DecisionPassport(
        decision_id=f"decision-{status.value}-{strategy.value}",
        cycle_id="cycle-test",
        symbol="SPY",
        status=status,
        plan=TradePlan(symbol="SPY", strategy=strategy, thesis="Test", confidence=0.8),
        gates=[GateResult(name="market window", passed=not blocked, observed="closed", limit="open")],
        receipt=ExecutionReceipt(
            mode="preview",
            accepted=status == DecisionStatus.PREVIEWED,
            raw_status=status.value,
        ),
    )


class LearningEngineTests(unittest.TestCase):
    def test_summary_keeps_preview_evidence_separate_from_realized_results(self) -> None:
        state = LearningEngine().summarize(
            [
                decision(DecisionStatus.PREVIEWED, StrategyKind.BULL_CALL_SPREAD),
                decision(DecisionStatus.REJECTED, StrategyKind.IRON_CONDOR, blocked=True),
                decision(DecisionStatus.NO_TRADE, StrategyKind.NO_TRADE),
            ]
        )
        self.assertEqual(state.approved_previews, 1)
        self.assertEqual(state.risk_vetoes, 1)
        self.assertEqual(state.closed_outcomes, 0)
        self.assertEqual(state.stage, "collecting_evidence")
        self.assertEqual(state.common_vetoes[0].gate, "market window")

    def test_immediately_filled_submission_counts_once(self) -> None:
        state = LearningEngine().summarize(
            [],
            [
                ExecutionEvent(
                    event_id="entry-filled", symbol="SPY", kind="order_submitted",
                    status="filled", order_id="entry-1", message="filled",
                ),
                ExecutionEvent(
                    event_id="entry-confirmed", symbol="SPY", kind="order_update",
                    status="filled", order_id="entry-1", message="filled",
                ),
                ExecutionEvent(
                    event_id="exit-filled", symbol="SPY", kind="exit_submitted",
                    status="filled", order_id="exit-1", message="filled",
                ),
            ],
        )
        self.assertEqual(state.paper_executions, 1)
        self.assertEqual(state.closed_outcomes, 1)
        self.assertEqual(state.pending_outcomes, 0)


if __name__ == "__main__":
    unittest.main()
