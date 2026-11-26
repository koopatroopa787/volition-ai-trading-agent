"""The Adversarial Skeptic's mandate must point at evidence keys that exist, and leave the other roles alone."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agents.committee import ROLES, ReasoningCommittee, limit_checks
from config import Settings
from fixtures import demo_account, demo_markets
from strategy import StrategyRouter

SKEPTIC = next(r for r in ROLES if r.name == "Adversarial Skeptic")
OLD_MANDATE = "Identify a concrete, quantified reason the proposal may be too fragile."


class SkepticPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None, reasoning_base_url="http://ollama:11434/v1", reasoning_model="qwen2.5:3b",
                                 reasoning_allow_unauthenticated=True)
        self.market, self.account = demo_markets()[0], demo_account()
        self.plan = StrategyRouter().route(self.market, self.account, 1.25)
        self.sim = SimpleNamespace(expected_pnl=12.0, probability_profit=0.5, probability_near_max_loss=0.3)

    def test_mandate_no_longer_asks_the_model_to_find_a_reason(self) -> None:
        self.assertNotIn("fragile", SKEPTIC.mandate)
        self.assertNotIn("concrete, quantified reason", SKEPTIC.mandate)
        self.assertIn("support", SKEPTIC.mandate)                       # a clean proposal has an explicit outcome
        self.assertIn("never invent a threshold", SKEPTIC.mandate)
        # the model copied limit_checks objects into the evidence list, which is not valid JSON (33 of 41 on the live box)
        self.assertIn("plain-text strings", SKEPTIC.mandate)
        self.assertIn("never copy limit_checks entries into evidence", SKEPTIC.mandate)

    def test_other_roles_are_unchanged(self) -> None:
        self.assertEqual({r.name: r.mandate for r in ROLES if r is not SKEPTIC}, {
            "Regime Sentinel": "Judge whether trend and market regime fit the proposed directional exposure.",
            "Volatility Architect": "Judge whether the volatility regime and convexity fit the structure."})

    def test_only_the_skeptic_gets_limit_checks_and_the_mandate_points_at_them(self) -> None:
        committee = ReasoningCommittee(self.settings)
        self.assertIn("limit_checks", SKEPTIC.mandate)
        for role in ROLES:
            evidence = committee._evidence(role, self.market, self.plan, self.account, self.sim)
            self.assertEqual("limit_checks" in evidence, role is SKEPTIC, role.name)

    def test_limit_checks_flag_exactly_the_breached_limits(self) -> None:
        def ev(loss=0.9, conf=0.9, pnl=10.0, pop=50.0, near=30.0):
            return {"proposal": {"max_loss_pct_of_equity": loss, "risk_budget_pct_of_equity": 1.25, "router_confidence_0_to1": conf},
                    "stress_test": {"expected_pnl_dollars": pnl, "probability_profit_pct": pop, "probability_near_max_loss_pct": near}}

        def breached(e):
            return [c["limit"].split(" ")[0] for c in limit_checks(e) if c["breached"]]

        self.assertEqual(breached(ev()), [])
        self.assertEqual(breached(ev(loss=1.26)), ["max_loss_pct_of_equity"])
        self.assertEqual(breached(ev(conf=0.67)), ["router_confidence_0_to_1"])
        self.assertEqual(breached(ev(pnl=0.0)), ["expected_pnl_dollars"])
        self.assertEqual(breached(ev(pop=34.9)), ["probability_profit_pct"])
        self.assertEqual(breached(ev(near=60.1)), ["probability_near_max_loss_pct"])
        # boundaries that pass: equal to the limit
        self.assertEqual(breached(ev(loss=1.25, conf=0.68, pop=35.0, near=60.0)), [])
        # without a stress test only the two proposal limits are checked
        no_stress = {**ev(), "stress_test": None}
        self.assertEqual(len(limit_checks(no_stress)), 2)
        self.assertEqual(breached({**ev(loss=None), "stress_test": None}), ["max_loss_pct_of_equity"])

    def test_limit_checks_agree_with_the_role_check_the_code_already_trusts(self) -> None:
        committee = ReasoningCommittee(self.settings)
        for confidence in (0.9, 0.5):
            plan = self.plan.model_copy(update={"confidence": confidence})
            evidence = committee._evidence(SKEPTIC, self.market, plan, self.account, self.sim)
            proposal_breach = any(c["breached"] for c in evidence["limit_checks"][:2])
            self.assertEqual(proposal_breach, not evidence["role_check"]["passed"])

    async def test_the_system_prompt_sent_for_the_skeptic_carries_the_new_mandate(self) -> None:
        sent = {}

        async def post(url, headers=None, json=None):
            sent[json["messages"][0]["content"].split(" on an autonomous")[0]] = json["messages"][0]["content"]
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content":
                '{"verdict":"support","confidence":0.8,"summary":"ok","evidence":["a","b"]}'}}]}
            return response

        committee = ReasoningCommittee(self.settings)
        with patch("agents.committee.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value.post = AsyncMock(side_effect=post)
            await committee.deliberate(self.market, self.plan, self.account, self.sim)
        prompt = sent["You are the Adversarial Skeptic"]
        self.assertIn(SKEPTIC.mandate, prompt)
        self.assertNotIn(OLD_MANDATE, prompt)
        # the generic wording that already told every role to support clean proposals is still there
        self.assertIn("Support when the role check passes", prompt)

    async def test_model_calls_use_the_configured_timeout_not_a_hardcoded_one(self) -> None:
        self.assertEqual(Settings(_env_file=None).reasoning_timeout_seconds, 240)   # three queued ~50 s runs fit
        committee = ReasoningCommittee(Settings(_env_file=None, reasoning_base_url="http://ollama:11434/v1", reasoning_model="qwen2.5:3b",
                                                reasoning_allow_unauthenticated=True, reasoning_timeout_seconds=77))
        with patch("agents.committee.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value.post = AsyncMock(side_effect=OSError("down"))
            await committee.deliberate(self.market, self.plan, self.account, self.sim)
        self.assertTrue(client_type.called)
        self.assertEqual({call.kwargs["timeout"] for call in client_type.call_args_list}, {77})


if __name__ == "__main__":
    unittest.main()
