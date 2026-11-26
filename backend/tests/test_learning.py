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
        from models import TradeLeg

        leg = TradeLeg(symbol="SPY260925C00500000", option_type="call", side="buy",
                       position_intent="buy_to_open", strike=500, expiration="2026-09-25")
        entry = DecisionPassport(
            decision_id="d-spy", cycle_id="c", symbol="SPY", status=DecisionStatus.SUBMITTED,
            plan=TradePlan(symbol="SPY", strategy=StrategyKind.BULL_CALL_SPREAD, thesis="t",
                           confidence=0.8, legs=[leg]),
            receipt=ExecutionReceipt(mode="paper", accepted=True, raw_status="filled"),
        )
        state = LearningEngine().summarize(
            [entry],
            [
                ExecutionEvent(
                    event_id="entry-filled", decision_id="d-spy", symbol="SPY", kind="order_submitted",
                    status="filled", order_id="entry-1", message="filled",
                ),
                ExecutionEvent(
                    event_id="entry-confirmed", decision_id="d-spy", symbol="SPY", kind="order_update",
                    status="filled", order_id="entry-1", message="filled",
                ),
                ExecutionEvent(
                    event_id="exit-filled", cycle_id="exit:SPY:2026-09-25", symbol="SPY", kind="exit_submitted",
                    status="filled", order_id="exit-1", message="filled",
                ),
            ],
        )
        self.assertEqual(state.paper_executions, 1)
        self.assertEqual(state.closed_outcomes, 1)
        self.assertEqual(state.pending_outcomes, 0)



class ClosedTradeAttributionTests(unittest.TestCase):
    def test_exit_without_decision_id_closes_entries_with_the_same_symbol_and_expiry(self) -> None:
        from datetime import datetime, timezone

        from learning import closed_trades
        from models import TradeLeg

        def at(minute: int) -> datetime:
            return datetime(2026, 9, 4, 14, minute, tzinfo=timezone.utc)

        leg = TradeLeg(symbol="TSLA260925C00350000", option_type="call", side="buy",
                       position_intent="buy_to_open", strike=350, expiration="2026-09-25")
        entries = [
            DecisionPassport(
                decision_id=f"d{i}", cycle_id="c", symbol="TSLA", status=DecisionStatus.SUBMITTED,
                plan=TradePlan(symbol="TSLA", strategy=StrategyKind.BULL_CALL_SPREAD, thesis="t",
                               confidence=0.8, legs=[leg]),
                receipt=ExecutionReceipt(mode="paper", accepted=True, raw_status="filled"),
            )
            for i in (1, 2)
        ]
        events = [
            # The same broker order reported filled twice must count once.
            ExecutionEvent(event_id="a", recorded_at=at(1), decision_id="d1", symbol="TSLA",
                           kind="order_update", status="filled", order_id="o1",
                           filled_qty=1, filled_avg_price=12.0, message="m"),
            ExecutionEvent(event_id="b", recorded_at=at(2), decision_id="d1", symbol="TSLA",
                           kind="order_update", status="filled", order_id="o1",
                           filled_qty=1, filled_avg_price=12.0, message="m"),
            ExecutionEvent(event_id="c", recorded_at=at(3), decision_id="d2", symbol="TSLA",
                           kind="order_update", status="filled", order_id="o2",
                           filled_qty=1, filled_avg_price=10.0, message="m"),
            # Multi-leg exit reports the whole position as one unit at a summed credit.
            ExecutionEvent(event_id="d", recorded_at=at(9), cycle_id="exit:TSLA:2026-09-25", symbol="TSLA",
                           kind="exit_update", status="filled", order_id="x1",
                           filled_qty=1, filled_avg_price=-26.0, message="m"),
        ]
        trades = closed_trades(entries, events)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["decision_ids"], ["d1", "d2"])
        self.assertEqual(trades[0]["pnl"], 400.0)  # paid 2,200, received 2,600

        state = LearningEngine().summarize(entries, events)
        self.assertEqual(state.closed_outcomes, 1)
        self.assertEqual(state.pending_outcomes, 0)
        bull = next(c for c in state.candidates if c.strategy == StrategyKind.BULL_CALL_SPREAD)
        self.assertEqual((bull.realized_trades, bull.realized_pnl, bull.win_rate), (1, 400.0, 1.0))


if __name__ == "__main__":
    unittest.main()
