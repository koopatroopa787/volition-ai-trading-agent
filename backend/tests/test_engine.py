from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from config import Settings
from engine import VolitionEngine
from ledger import DecisionLedger, ExecutionLedger
from fixtures import demo_account, demo_positions
from models import AgentOpinion, DecisionStatus, ExecutionEvent, ExecutionReceipt, StrategyKind
from runtime_state import RuntimeStateStore


class EngineSelectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def isolate(engine: VolitionEngine, directory: str) -> None:
        engine.ledger = DecisionLedger(Path(directory) / "decisions.jsonl")
        engine.execution_ledger = ExecutionLedger(Path(directory) / "order_events.jsonl")
        engine.runtime_store = RuntimeStateStore(Path(directory) / "runtime_state.json")
        engine.kill_switch = engine.runtime_store.state.kill_switch

    async def test_scheduler_ranks_only_risk_eligible_candidates(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))
        engine.provider.positions = AsyncMock(return_value=[])  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory(prefix="volition-engine-") as directory:
            self.isolate(engine, directory)
            decision = await engine.run_cycle()
        self.assertEqual(decision.symbol, "SPY")
        self.assertEqual(decision.plan.strategy, StrategyKind.BULL_CALL_SPREAD)
        self.assertEqual(decision.status, DecisionStatus.EXECUTED)
        stress = next(gate for gate in decision.gates if gate.name == "strategy stress test")
        self.assertTrue(stress.passed)
        self.assertGreater(decision.evidence["simulation_probability_profit"], 0)

    async def test_explicit_symbol_run_preserves_risk_veto_evidence(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))
        with tempfile.TemporaryDirectory(prefix="volition-engine-") as directory:
            self.isolate(engine, directory)
            decision = await engine.run_cycle("QQQ")
        self.assertEqual(decision.status, DecisionStatus.REJECTED)
        liquidity = next(gate for gate in decision.gates if gate.name == "chain liquidity")
        self.assertFalse(liquidity.passed)
        self.assertFalse(decision.receipt.accepted)

    async def test_model_opposition_is_advisory_when_deterministic_gates_pass(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))
        engine.provider.positions = AsyncMock(return_value=[])  # type: ignore[method-assign]
        engine.committee.deliberate = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                AgentOpinion(
                    agent=name,
                    verdict="oppose",
                    confidence=0.85,
                    summary="Advisory concern without a deterministic gate failure.",
                    evidence=["model concern"],
                    model="test-model",
                )
                for name in ("Regime Sentinel", "Volatility Architect", "Adversarial Skeptic")
            ]
        )
        with tempfile.TemporaryDirectory(prefix="volition-advisory-") as directory:
            self.isolate(engine, directory)
            decision = await engine.run_cycle("SPY")

        committee_gate = next(gate for gate in decision.gates if gate.name == "AI committee consensus")
        self.assertFalse(committee_gate.passed)
        self.assertEqual(committee_gate.severity, "warning")
        self.assertEqual(decision.status, DecisionStatus.EXECUTED)

    async def test_scheduled_cycle_skips_closed_market_without_ledger_spam(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))

        async def closed_market() -> bool:
            return False

        engine.provider.market_open = closed_market  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory(prefix="volition-engine-") as directory:
            self.isolate(engine, directory)
            decision = await engine.run_scheduled_cycle()
            self.assertIsNone(decision)
            self.assertEqual(engine.ledger.recent(), [])

    async def test_strategy_lab_supports_every_watchlist_symbol(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))
        for symbol in engine.settings.watchlist:
            result = await engine.strategy_lab(symbol, 1_000)
            self.assertEqual(result.symbol, symbol)
            self.assertNotEqual(result.strategy, StrategyKind.NO_TRADE)
            self.assertGreater(len(result.underlying_bands), 8)

    async def test_market_pulse_is_broad_but_strategy_watchlist_stays_focused(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None))
        pulse = await engine.market_pulse()

        self.assertGreaterEqual(pulse.tracked_assets, 30)
        self.assertEqual(pulse.strategy_eligible, len(engine.settings.watchlist))
        self.assertEqual(
            pulse.breadth.advancing + pulse.breadth.declining + pulse.breadth.unchanged,
            pulse.tracked_assets,
        )

    async def test_recent_winner_is_rotated_out_when_fresh_alternatives_exist(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None, deep_scan_limit=2, symbol_cooldown_minutes=60))
        engine.provider.positions = AsyncMock(return_value=[])  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory(prefix="volition-rotation-") as directory:
            self.isolate(engine, directory)
            first = await engine.run_cycle()
            shortlist, cooling = await engine._trading_shortlist()
            second = await engine.run_cycle()

        self.assertIn(first.symbol, cooling)
        self.assertNotIn(first.symbol, shortlist)
        self.assertNotEqual(first.symbol, second.symbol)

    async def test_filled_exit_does_not_permanently_block_future_position_management(self) -> None:
        engine = VolitionEngine(
            Settings(
                _env_file=None,
                volition_mode="paper",
                execution_mode="paper",
                allow_order_submission=True,
                alpaca_api_key="test-key",
                alpaca_secret_key="test-secret",
            )
        )
        position = demo_positions()[0].model_copy(update={"unrealized_pnl": -200.0})
        engine.provider.account = AsyncMock(return_value=demo_account())  # type: ignore[method-assign]
        engine.provider.positions = AsyncMock(return_value=[position])  # type: ignore[method-assign]
        engine.executor.close_position = AsyncMock(  # type: ignore[method-assign]
            return_value=ExecutionReceipt(
                mode="paper",
                accepted=True,
                order_id="new-exit",
                raw_status="pending_new",
                message="accepted",
            )
        )
        with tempfile.TemporaryDirectory(prefix="volition-exit-reentry-") as directory:
            self.isolate(engine, directory)
            engine.execution_ledger.append(
                ExecutionEvent(
                    event_id="old-pending-exit",
                    cycle_id=f"exit:{position.symbol}:{position.expiration}",
                    symbol=position.symbol,
                    kind="exit_submitted",
                    status="pending_new",
                    order_id="old-exit",
                    message="accepted",
                )
            )
            engine.execution_ledger.append(
                ExecutionEvent(
                    event_id="old-filled-exit",
                    cycle_id=f"exit:{position.symbol}:{position.expiration}",
                    symbol=position.symbol,
                    kind="exit_update",
                    status="filled",
                    order_id="old-exit",
                    message="filled",
                )
            )
            submitted = await engine.manage_positions()

        self.assertEqual(len(submitted), 1)
        engine.executor.close_position.assert_awaited_once()

    async def test_active_exit_still_blocks_duplicate_close_order(self) -> None:
        engine = VolitionEngine(
            Settings(
                _env_file=None,
                volition_mode="paper",
                execution_mode="paper",
                allow_order_submission=True,
                alpaca_api_key="test-key",
                alpaca_secret_key="test-secret",
            )
        )
        position = demo_positions()[0].model_copy(update={"unrealized_pnl": -200.0})
        engine.provider.account = AsyncMock(return_value=demo_account())  # type: ignore[method-assign]
        engine.provider.positions = AsyncMock(return_value=[position])  # type: ignore[method-assign]
        engine.executor.close_position = AsyncMock()  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory(prefix="volition-exit-active-") as directory:
            self.isolate(engine, directory)
            engine.execution_ledger.append(
                ExecutionEvent(
                    event_id="active-exit",
                    cycle_id=f"exit:{position.symbol}:{position.expiration}",
                    symbol=position.symbol,
                    kind="exit_submitted",
                    status="new",
                    order_id="active-order",
                    message="working",
                )
            )
            submitted = await engine.manage_positions()

        self.assertEqual(submitted, [])
        engine.executor.close_position.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
