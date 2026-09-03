from __future__ import annotations

import unittest

from fixtures import demo_account, demo_markets
from models import StrategyKind
from strategy import StrategyRouter


class StrategyRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = StrategyRouter()
        self.account = demo_account()
        self.markets = {market.symbol: market for market in demo_markets()}

    def route(self, symbol: str):
        return self.router.route(self.markets[symbol], self.account, 1.25)

    def test_routes_each_regime_to_an_options_or_cash_decision(self) -> None:
        self.assertEqual(self.route("SPY").strategy, StrategyKind.BULL_CALL_SPREAD)
        self.assertEqual(self.route("NVDA").strategy, StrategyKind.BEAR_PUT_SPREAD)
        self.assertEqual(self.route("QQQ").strategy, StrategyKind.IRON_CONDOR)
        self.assertEqual(self.route("AMD").strategy, StrategyKind.LONG_STRADDLE)
        self.assertEqual(self.route("AAPL").strategy, StrategyKind.NO_TRADE)

    def test_all_trade_routes_have_finite_loss_and_real_occ_legs(self) -> None:
        for symbol in ("SPY", "NVDA", "QQQ", "AMD"):
            plan = self.route(symbol)
            self.assertGreater(plan.max_loss, 0)
            self.assertLessEqual(plan.max_loss / self.account.equity * 100, 1.25)
            self.assertGreaterEqual(len(plan.legs), 2)
            self.assertTrue(all(leg.symbol.startswith(symbol) for leg in plan.legs))

    def test_no_trade_places_no_capital_at_risk(self) -> None:
        plan = self.route("AAPL")
        self.assertEqual(plan.quantity, 0)
        self.assertEqual(plan.max_loss, 0)
        self.assertEqual(plan.legs, [])
        self.assertIsNotNone(plan.no_trade_reason)

    def test_strongly_conflicting_news_blocks_directional_trade(self) -> None:
        conflicted = self.markets["SPY"].model_copy(update={"news_sentiment": -0.82})
        plan = self.router.route(conflicted, self.account, 1.25)
        self.assertEqual(plan.strategy, StrategyKind.NO_TRADE)
        self.assertIn("news evidence", plan.no_trade_reason or "")

    def test_lower_cost_strangle_replaces_unaffordable_straddle(self) -> None:
        smaller_account = self.account.model_copy(update={"equity": 80_000.0})
        plan = self.router.route(self.markets["AMD"], smaller_account, 1.25)
        self.assertEqual(plan.strategy, StrategyKind.LONG_STRANGLE)
        self.assertLessEqual(plan.max_loss, 1_000.0)

    def test_router_preserves_cash_when_one_contract_exceeds_budget(self) -> None:
        small_account = self.account.model_copy(update={"equity": 5_000.0})
        plan = self.router.route(self.markets["SPY"], small_account, 1.25)
        self.assertEqual(plan.strategy, StrategyKind.NO_TRADE)
        self.assertIn("per-trade budget", plan.no_trade_reason or "")


if __name__ == "__main__":
    unittest.main()
