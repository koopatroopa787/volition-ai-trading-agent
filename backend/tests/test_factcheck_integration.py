"""The fact-checker wired into ReasoningCommittee.deliberate, with the model mocked."""
from __future__ import annotations

import json as jsonlib  # the fake endpoint takes a keyword named json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from agents.committee import ROLES, ReasoningCommittee
from audit import AuditTrail
from config import Settings
from fixtures import demo_account, demo_markets
from strategy import StrategyRouter

FALSE_CLAIM = "The probability of near-maximum loss is 41%, which exceeds the 60% threshold."
CLEAN = "The proposal fits its role check."


def reply(by_role: dict[str, tuple[str, str]]):
    """A fake chat-completions endpoint: (verdict, summary) per committee role."""
    async def post(url, headers=None, json=None):
        role = next(r.name for r in ROLES if r.name in json["messages"][0]["content"])
        verdict, summary = by_role[role]
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": jsonlib.dumps(
            {"verdict": verdict, "confidence": 0.8, "summary": summary, "evidence": ["x"]})}}]}
        return response
    return post


class FactcheckWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.market = demo_markets()[0]
        self.account = demo_account()
        self.plan = StrategyRouter().route(self.market, self.account, 1.25)

    async def run_committee(self, mode: str, model=None, by_role=None):
        settings = Settings(_env_file=None, reasoning_base_url="http://ollama:11434/v1", reasoning_model="qwen2.5:3b",
                            reasoning_allow_unauthenticated=True, factcheck_mode=mode)
        by_role = by_role or {"Regime Sentinel": ("support", CLEAN), "Volatility Architect": ("support", CLEAN),
                              "Adversarial Skeptic": ("oppose", FALSE_CLAIM)}
        with tempfile.TemporaryDirectory(prefix="volition-factcheck-") as directory:
            audit = AuditTrail(Path(directory) / "audit.sqlite3")
            committee = ReasoningCommittee(settings, audit)
            with patch("agents.committee.httpx.AsyncClient") as client_type:
                client_type.return_value.__aenter__.return_value.post = model or AsyncMock(side_effect=reply(by_role))
                opinions = await committee.deliberate(self.market, self.plan, self.account, cycle_id="c1")
            events = [row for row in audit.events(cycle_id="c1") if row["kind"] == "opinion_factcheck"]
        return opinions, events

    def test_default_is_off(self) -> None:
        self.assertEqual(Settings(_env_file=None).factcheck_mode, "off")

    async def test_off_changes_nothing_and_writes_nothing(self) -> None:
        opinions, events = await self.run_committee("off")
        self.assertEqual([o.verdict for o in opinions], ["support", "support", "oppose"])
        self.assertEqual(events, [])

    async def test_shadow_logs_the_false_claim_and_changes_no_verdict(self) -> None:
        opinions, events = await self.run_committee("shadow")
        self.assertEqual([o.verdict for o in opinions], ["support", "support", "oppose"])
        self.assertEqual(len(events), 1)
        graded = {o["agent"]: o["grade"] for o in events[0]["payload"]["opinions"]}
        self.assertEqual(graded["Adversarial Skeptic"], "false")
        self.assertEqual(graded["Regime Sentinel"], "unchecked")

    async def test_enforce_downgrades_only_the_opinion_with_a_hard_false_claim(self) -> None:
        opinions, events = await self.run_committee("enforce")
        self.assertEqual([o.verdict for o in opinions], ["support", "support", "abstain"])
        self.assertIn("fact-check", opinions[2].evidence[-1])
        self.assertTrue(ReasoningCommittee.supports(self.plan, opinions))
        self.assertEqual(len(events), 1)

    async def test_enforce_never_touches_the_deterministic_fallback(self) -> None:
        failing = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        opinions, _ = await self.run_committee("enforce", model=failing)
        self.assertTrue(all(o.model == "deterministic-fallback" for o in opinions))
        expected = [ReasoningCommittee(Settings(_env_file=None))._fallback(r, self.market, self.plan).verdict for r in ROLES]
        self.assertEqual([o.verdict for o in opinions], expected)


if __name__ == "__main__":
    unittest.main()
