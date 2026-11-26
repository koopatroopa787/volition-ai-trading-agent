"""Backtest-driven changes: bearish spreads off, condors limited to index ETFs, realistic volatility for credit stress tests."""
from __future__ import annotations

import statistics
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from agents.committee import ROLES, ReasoningCommittee, limit_checks
from config import Settings
from engine import VolitionEngine
from fixtures import demo_account, demo_markets
from models import StrategyKind
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter


class SettingsTests(unittest.TestCase):
    def test_defaults_encode_the_backtest_findings(self) -> None:
        settings = Settings(_env_file=None)
        self.assertFalse(settings.bearish_spreads_enabled)
        self.assertEqual(settings.condor_symbols, ["SPY", "QQQ", "IWM", "DIA"])
        self.assertEqual(settings.credit_vol_factor, 0.85)

    def test_environment_style_values_parse(self) -> None:
        settings = Settings(_env_file=None, condor_symbols="spy, aapl", bearish_spreads_enabled="true", credit_vol_factor="1.0")
        self.assertEqual(settings.condor_symbols, ["spy", "aapl"])
        self.assertTrue(settings.bearish_spreads_enabled)
        self.assertEqual(settings.credit_vol_factor, 1.0)
        self.assertEqual(Settings(_env_file=None, condor_symbols="").condor_symbols, [])

    def test_volatility_factor_is_bounded(self) -> None:
        for bad in (0.4, 1.2):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, credit_vol_factor=bad)

    def test_engine_passes_the_settings_to_the_router_and_the_simulator(self) -> None:
        engine = VolitionEngine(Settings(_env_file=None, bearish_spreads_enabled=True, condor_symbols="spy", credit_vol_factor=0.9))
        self.assertTrue(engine.router.bearish_enabled)
        self.assertEqual(engine.router.condor_symbols, {"SPY"})
        self.assertEqual(engine.simulator.credit_vol_factor, 0.9)
        default = VolitionEngine(Settings(_env_file=None))
        self.assertFalse(default.router.bearish_enabled)
        self.assertEqual(default.router.condor_symbols, {"SPY", "QQQ", "IWM", "DIA"})
        self.assertEqual(default.simulator.credit_vol_factor, 0.85)


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = demo_account()
        self.markets = {market.symbol: market for market in demo_markets()}

    def route(self, symbol: str, **router_args):
        return StrategyRouter(**router_args).route(self.markets[symbol], self.account, 1.25)

    def test_bearish_switch_turns_the_bear_put_spread_into_cash(self) -> None:
        self.assertEqual(self.route("NVDA").strategy, StrategyKind.BEAR_PUT_SPREAD)          # original behaviour is the class default
        off = self.route("NVDA", bearish_enabled=False)
        self.assertEqual(off.strategy, StrategyKind.NO_TRADE)
        self.assertEqual((off.quantity, off.max_loss, off.legs), (0, 0, []))
        self.assertIn("Bearish spreads are switched off", off.no_trade_reason or "")

    def test_bearish_switch_leaves_the_other_branches_alone(self) -> None:
        self.assertEqual(self.route("SPY", bearish_enabled=False).strategy, StrategyKind.BULL_CALL_SPREAD)
        self.assertEqual(self.route("QQQ", bearish_enabled=False).strategy, StrategyKind.IRON_CONDOR)
        self.assertEqual(self.route("AMD", bearish_enabled=False).strategy, StrategyKind.LONG_STRADDLE)

    def test_condors_are_limited_to_the_listed_symbols(self) -> None:
        self.assertEqual(self.route("QQQ").strategy, StrategyKind.IRON_CONDOR)               # no list = any symbol
        self.assertEqual(self.route("QQQ", condor_symbols=["spy", "qqq"]).strategy, StrategyKind.IRON_CONDOR)
        blocked = self.route("QQQ", condor_symbols=["SPY", "IWM"])
        self.assertEqual(blocked.strategy, StrategyKind.NO_TRADE)
        self.assertEqual((blocked.quantity, blocked.max_loss), (0, 0))
        self.assertIn("IWM, SPY", blocked.no_trade_reason or "")

    def test_condor_limit_does_not_touch_directional_routes(self) -> None:
        self.assertEqual(self.route("SPY", condor_symbols=["IWM"]).strategy, StrategyKind.BULL_CALL_SPREAD)
        self.assertEqual(self.route("NVDA", condor_symbols=["IWM"]).strategy, StrategyKind.BEAR_PUT_SPREAD)


class CreditVolatilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = demo_account()
        self.markets = {market.symbol: market for market in demo_markets()}
        router = StrategyRouter()
        self.condor_market, self.condor = self.markets["QQQ"], router.route(self.markets["QQQ"], self.account, 1.0)
        self.vertical_market, self.vertical = self.markets["SPY"], router.route(self.markets["SPY"], self.account, 1.0)
        ivs = [c.implied_volatility for c in self.condor_market.contracts if c.implied_volatility > 0]
        self.implied = statistics.median(ivs)

    def test_condor_is_simulated_at_the_scaled_implied_volatility(self) -> None:
        old = MonteCarloSimulator().simulate(self.condor_market, self.condor, 3_000)
        new = MonteCarloSimulator(0.85).simulate(self.condor_market, self.condor, 3_000)
        self.assertAlmostEqual(old.annual_volatility, self.implied, places=3)
        expected = max(self.condor_market.realized_volatility, 0.85 * self.implied)
        self.assertAlmostEqual(new.annual_volatility, expected, places=3)
        self.assertLess(new.annual_volatility, old.annual_volatility)
        self.assertGreater(new.expected_pnl, old.expected_pnl)                      # selling richly priced premium now has an edge
        self.assertTrue(any("scaled to 85%" in line for line in new.assumptions))

    def test_the_volatility_floor_is_realized_volatility(self) -> None:
        tiny = MonteCarloSimulator(0.5).simulate(self.condor_market, self.condor, 2_000)
        self.assertGreaterEqual(tiny.annual_volatility, self.condor_market.realized_volatility - 1e-4)

    def test_factor_of_one_reproduces_the_original_test_exactly(self) -> None:
        a = MonteCarloSimulator().simulate(self.condor_market, self.condor, 2_000)
        b = MonteCarloSimulator(1.0).simulate(self.condor_market, self.condor, 2_000)
        self.assertEqual((a.seed, a.annual_volatility, a.expected_pnl, a.assumptions), (b.seed, b.annual_volatility, b.expected_pnl, b.assumptions))

    def test_other_structures_are_untouched(self) -> None:
        a = MonteCarloSimulator().simulate(self.vertical_market, self.vertical, 2_000)
        b = MonteCarloSimulator(0.85).simulate(self.vertical_market, self.vertical, 2_000)
        self.assertEqual((a.seed, a.annual_volatility, a.expected_pnl), (b.seed, b.annual_volatility, b.expected_pnl))

    def test_the_volatility_shock_scenario_still_stresses_upwards(self) -> None:
        sim = MonteCarloSimulator(0.85)
        base = sim.simulate(self.condor_market, self.condor, 2_000)
        shock = sim.simulate(self.condor_market, self.condor, 2_000, scenario="volatility_shock")
        self.assertAlmostEqual(shock.annual_volatility / base.annual_volatility, 1.35, places=2)


class CondorStressFloorTests(unittest.TestCase):
    """Index-ETF condors may pass the stress test slightly below zero expected P&L; nothing else may."""

    def setUp(self) -> None:
        self.settings = Settings(_env_file=None)
        self.engine = VolitionEngine(self.settings)
        self.account = demo_account()
        self.markets = {market.symbol: market for market in demo_markets()}
        router = StrategyRouter()
        self.condor = router.route(self.markets["QQQ"], self.account, 1.0)         # QQQ condor
        self.vertical = router.route(self.markets["SPY"], self.account, 1.0)

    @staticmethod
    def sim(expected_pnl: float, pop: float = 0.5, near: float = 0.3):
        return SimpleNamespace(expected_pnl=expected_pnl, probability_profit=pop, probability_near_max_loss=near)

    def test_floor_applies_to_listed_index_condors_only(self) -> None:
        s = self.settings
        self.assertAlmostEqual(s.expected_pnl_floor("iron_condor", "qqq", 1000.0), -50.0)
        self.assertEqual(s.expected_pnl_floor("iron_condor", "AAPL", 1000.0), 0.0)
        self.assertEqual(s.expected_pnl_floor("bull_call_spread", "SPY", 1000.0), 0.0)
        self.assertEqual(Settings(_env_file=None, condor_expected_pnl_floor_pct=0).expected_pnl_floor("iron_condor", "SPY", 1000.0), 0.0)
        self.assertEqual(Settings(_env_file=None, condor_symbols="").expected_pnl_floor("iron_condor", "SPY", 1000.0), 0.0)

    def test_the_gate_uses_the_floor_for_index_condors(self) -> None:
        limit = self.condor.max_loss
        self.assertTrue(self.engine._stress_passed(self.sim(-0.04 * limit), self.condor))
        self.assertFalse(self.engine._stress_passed(self.sim(-0.06 * limit), self.condor))
        self.assertIn("allowance", self.engine._stress_limit(self.condor))

    def test_everything_else_keeps_the_strict_rule(self) -> None:
        self.assertFalse(self.engine._stress_passed(self.sim(-1.0), self.vertical))
        self.assertTrue(self.engine._stress_passed(self.sim(1.0), self.vertical))
        self.assertEqual(self.engine._stress_limit(self.vertical), "positive expected P&L; PoP ≥ 35%; near-max-loss probability ≤ 60%")
        other = self.condor.model_copy(update={"symbol": "AAPL"})
        self.assertFalse(self.engine._stress_passed(self.sim(-1.0), other))

    def test_the_other_stress_limits_still_bind_for_condors(self) -> None:
        self.assertFalse(self.engine._stress_passed(self.sim(10.0, pop=0.30), self.condor))
        self.assertFalse(self.engine._stress_passed(self.sim(10.0, near=0.65), self.condor))

    def test_the_skeptic_reads_the_same_floor(self) -> None:
        committee = ReasoningCommittee(self.settings)
        skeptic = next(r for r in ROLES if r.name == "Adversarial Skeptic")
        loss = self.condor.max_loss
        ok = committee._evidence(skeptic, self.markets["QQQ"], self.condor, self.account, self.sim(-0.04 * loss))
        bad = committee._evidence(skeptic, self.markets["QQQ"], self.condor, self.account, self.sim(-0.06 * loss))
        self.assertEqual(ok["stress_test"]["expected_pnl_floor_dollars"], round(-0.05 * loss, 2))
        pnl = lambda ev: next(c for c in ev["limit_checks"] if c["limit"].startswith("expected_pnl"))
        self.assertFalse(pnl(ok)["breached"])
        self.assertTrue(pnl(bad)["breached"])
        self.assertNotEqual(pnl(ok)["limit"], "expected_pnl_dollars > 0")

    def test_prompts_for_other_structures_are_unchanged(self) -> None:
        committee = ReasoningCommittee(self.settings)
        skeptic = next(r for r in ROLES if r.name == "Adversarial Skeptic")
        ev = committee._evidence(skeptic, self.markets["SPY"], self.vertical, self.account, self.sim(5.0))
        self.assertNotIn("expected_pnl_floor_dollars", ev["stress_test"])
        self.assertEqual(ev["stress_test"]["pass_policy"]["expected_pnl_dollars"], "> 0")
        self.assertEqual([c["limit"] for c in ev["limit_checks"]][2], "expected_pnl_dollars > 0")

    def test_limit_checks_without_a_floor_behave_as_before(self) -> None:
        ev = {"proposal": {"max_loss_pct_of_equity": 0.9, "risk_budget_pct_of_equity": 1.25, "router_confidence_0_to1": 0.9},
              "stress_test": {"expected_pnl_dollars": 0.0, "probability_profit_pct": 50.0, "probability_near_max_loss_pct": 30.0}}
        self.assertTrue(limit_checks(ev)[2]["breached"])                               # exactly zero is still a breach


if __name__ == "__main__":
    unittest.main()
