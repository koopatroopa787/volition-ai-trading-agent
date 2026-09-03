from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from agents.committee import ROLES, ReasoningCommittee
from config import Settings
from fixtures import demo_account, demo_markets
from strategy import StrategyRouter


class ReasoningConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_local_openai_compatible_runtime_is_enabled_without_api_key(self) -> None:
        settings = Settings(
            _env_file=None,
            reasoning_base_url="http://ollama:11434/v1",
            reasoning_model="qwen2.5:3b",
            reasoning_provider="Private Qwen",
            reasoning_allow_unauthenticated=True,
        )
        self.assertTrue(settings.reasoning_enabled)
        self.assertEqual(settings.reasoning_mode, "local_model")

    def test_hosted_runtime_requires_api_key(self) -> None:
        locked = Settings(
            _env_file=None,
            reasoning_base_url="https://models.example/v1",
            reasoning_model="example-model",
        )
        enabled = locked.model_copy(update={"reasoning_api_key": "test-key"})
        self.assertFalse(locked.reasoning_enabled)
        self.assertTrue(enabled.reasoning_enabled)
        self.assertEqual(enabled.reasoning_mode, "hosted_model")

    async def test_enabled_runtime_routes_all_committee_roles_to_model(self) -> None:
        settings = Settings(
            _env_file=None,
            reasoning_base_url="http://ollama:11434/v1",
            reasoning_model="qwen2.5:3b",
            reasoning_allow_unauthenticated=True,
        )
        committee = ReasoningCommittee(settings)
        market = demo_markets()[0]
        plan = StrategyRouter().route(market, demo_account(), 1.25)
        committee._ask = AsyncMock(  # type: ignore[method-assign]
            side_effect=(
                lambda role, selected_market, selected_plan, account, simulation: committee._fallback(
                    role,
                    selected_market,
                    selected_plan,
                )
            )
        )

        opinions = await committee.deliberate(market, plan, demo_account())

        self.assertEqual(len(opinions), 3)
        self.assertEqual(committee._ask.await_count, 3)

    def test_model_evidence_has_explicit_units_and_account_relative_risk(self) -> None:
        settings = Settings(_env_file=None)
        committee = ReasoningCommittee(settings)
        account = demo_account()
        market = demo_markets()[0]
        plan = StrategyRouter().route(market, account, settings.max_risk_per_trade_pct)

        evidence = committee._evidence(ROLES[0], market, plan, account, None)
        market_evidence = evidence["market"]
        proposal_evidence = evidence["proposal"]

        self.assertEqual(market_evidence["realized_volatility_pct_annualized"], 17.1)
        self.assertEqual(market_evidence["iv_rank_percentile_0_to100"], 41.0)
        self.assertNotIn("iv_score", market_evidence)
        self.assertAlmostEqual(
            proposal_evidence["max_loss_pct_of_equity"],
            plan.max_loss / account.equity * 100,
            places=3,
        )
        self.assertEqual(
            evidence["committee_authority"],
            "advisory_only; deterministic risk gates control execution",
        )


if __name__ == "__main__":
    unittest.main()
