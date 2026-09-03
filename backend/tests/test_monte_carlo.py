from __future__ import annotations

import unittest

from fixtures import demo_account, demo_markets
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter


class MonteCarloTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = demo_account()
        self.markets = {market.symbol: market for market in demo_markets()}
        self.router = StrategyRouter()
        self.simulator = MonteCarloSimulator()

    def test_vertical_simulation_is_reproducible_and_ordered(self) -> None:
        market = self.markets["SPY"]
        plan = self.router.route(market, self.account, 1.0)
        first = self.simulator.simulate(market, plan, 2_000)
        second = self.simulator.simulate(market, plan, 2_000)

        self.assertEqual(first.seed, second.seed)
        self.assertEqual(first.pnl_percentiles, second.pnl_percentiles)
        self.assertLessEqual(first.pnl_percentiles["p5"], first.pnl_percentiles["p50"])
        self.assertLessEqual(first.pnl_percentiles["p50"], first.pnl_percentiles["p95"])
        self.assertGreaterEqual(first.probability_profit, 0.0)
        self.assertLessEqual(first.probability_profit, 1.0)
        self.assertEqual(sum(item.count for item in first.distribution), 2_000)

    def test_condor_expiry_payoff_respects_defined_risk_before_slippage(self) -> None:
        market = self.markets["QQQ"]
        plan = self.router.route(market, self.account, 1.0)
        for terminal in (market.underlying_price * 0.5, market.underlying_price, market.underlying_price * 1.5):
            pnl = self.simulator._expiry_pnl(plan, terminal)
            self.assertGreaterEqual(pnl, -plan.max_loss - 1.0)
            self.assertLessEqual(pnl, (plan.max_profit or 0.0) + 1.0)

    def test_scenario_probabilities_are_exhaustive_and_timed(self) -> None:
        market = self.markets["AMD"]
        plan = self.router.route(market, self.account, 1.0)
        base = self.simulator.simulate(market, plan, 2_000)
        shock = self.simulator.simulate(market, plan, 2_000, scenario="volatility_shock")

        self.assertGreater(shock.annual_volatility, base.annual_volatility)
        self.assertNotEqual(shock.seed, base.seed)
        self.assertAlmostEqual(sum(item.probability for item in base.outcome_scenarios), 1.0, places=3)
        self.assertEqual(base.probability_checkpoints[-1].day, base.horizon_days)
        self.assertAlmostEqual(
            base.probability_checkpoints[-1].probability_profitable_if_expiry,
            base.probability_profit,
            places=4,
        )


if __name__ == "__main__":
    unittest.main()
