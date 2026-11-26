from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from config import Settings
from fixtures import demo_account, demo_markets, demo_positions
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


class UncertainSubmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.executor = AlpacaPaperExecutor(
            Settings(
                _env_file=None,
                volition_mode="paper",
                execution_mode="paper",
                allow_order_submission=True,
                alpaca_api_key="test-key",
                alpaca_secret_key="test-secret",
            )
        )
        self.plan = StrategyRouter().route(demo_markets()[0], demo_account(), 1.25)

    async def test_timeout_recovers_order_by_client_id(self) -> None:
        self.executor.order_by_client_order_id = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "alpaca-order-1", "status": "new"}
        )
        with patch("services.alpaca_rest.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("response lost")
            )
            receipt = await self.executor.execute(self.plan, "cycle-timeout-1")
        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.order_id, "alpaca-order-1")
        self.executor.order_by_client_order_id.assert_awaited_once_with(receipt.client_order_id)

    async def test_unresolved_timeout_is_not_reported_as_rejection(self) -> None:
        self.executor.order_by_client_order_id = AsyncMock(  # type: ignore[method-assign]
            side_effect=httpx.ReadTimeout("lookup failed")
        )
        with patch("services.alpaca_rest.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("response lost")
            )
            receipt = await self.executor.execute(self.plan, "cycle-timeout-2")
        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.raw_status, "submission_unknown")
        self.assertIsNotNone(receipt.client_order_id)

    async def test_exit_timeout_preserves_client_id_for_reconciliation(self) -> None:
        self.executor.order_by_client_order_id = AsyncMock(  # type: ignore[method-assign]
            side_effect=httpx.ReadTimeout("lookup failed")
        )
        with patch("services.alpaca_rest.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("response lost")
            )
            position = demo_positions()[0].model_copy(update={"raw_legs": [
                {"symbol": "SPY261016C00650000", "side": "long", "ratio_qty": 1, "mid": 5.0},
                {"symbol": "SPY261016C00670000", "side": "short", "ratio_qty": 1, "mid": 2.0},
            ]})
            receipt = await self.executor.close_position(
                position, "exit:SPY:2026-10-16", "risk limit"
            )
        self.assertEqual(receipt.raw_status, "submission_unknown")
        self.assertTrue(receipt.client_order_id.startswith("volition-exit-"))
