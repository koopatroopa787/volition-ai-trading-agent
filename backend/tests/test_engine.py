from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config import Settings
from engine import VolitionEngine
from ledger import DecisionLedger, ExecutionLedger
from models import DecisionStatus, StrategyKind
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
        with tempfile.TemporaryDirectory(prefix="volition-rotation-") as directory:
            self.isolate(engine, directory)
            first = await engine.run_cycle()
            shortlist, cooling = await engine._trading_shortlist()
            second = await engine.run_cycle()

        self.assertIn(first.symbol, cooling)
        self.assertNotIn(first.symbol, shortlist)
        self.assertNotEqual(first.symbol, second.symbol)


if __name__ == "__main__":
    unittest.main()
