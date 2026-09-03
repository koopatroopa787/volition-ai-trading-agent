from __future__ import annotations

import unittest

from config import Settings
from fixtures import demo_account, demo_markets
from services.alpaca_rest import AlpacaPaperExecutor
from strategy import StrategyRouter


class ExecutionPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None)
        self.executor = AlpacaPaperExecutor(self.settings)
        self.account = demo_account()

    def test_credit_structure_uses_negative_alpaca_limit(self) -> None:
        market = next(item for item in demo_markets() if item.symbol == "AAPL")
        market = market.model_copy(update={"trend_score": 0.05, "iv_rank": 82.0})
        plan = StrategyRouter().research_candidate(market)
        self.assertEqual(plan.strategy.value, "iron_condor")
        payload = self.executor.order_payload(plan, "volition-test")
        self.assertLess(float(payload["limit_price"]), 0)

    def test_debit_structure_uses_positive_alpaca_limit(self) -> None:
        market = demo_markets()[0]
        plan = StrategyRouter().route(market, self.account, self.settings.max_risk_per_trade_pct)
        payload = self.executor.order_payload(plan, "volition-test")
        self.assertGreater(float(payload["limit_price"]), 0)

