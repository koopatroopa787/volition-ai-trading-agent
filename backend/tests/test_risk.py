from __future__ import annotations

import unittest

from config import Settings
from fixtures import demo_account, demo_markets
from risk import RiskConstitution
from strategy import StrategyRouter


class RiskConstitutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(_env_file=None)
        self.risk = RiskConstitution(self.settings)
        self.account = demo_account()
        self.market = demo_markets()[0]
        self.plan = StrategyRouter().route(self.market, self.account, self.settings.max_risk_per_trade_pct)

    def test_valid_defined_risk_spread_passes(self) -> None:
        gates = self.risk.evaluate(self.plan, self.account, self.market, False)
        self.assertTrue(self.risk.approved(gates), [gate for gate in gates if not gate.passed])

    def test_kill_switch_is_a_final_veto(self) -> None:
        gates = self.risk.evaluate(self.plan, self.account, self.market, True)
        gate = next(item for item in gates if item.name == "kill switch")
        self.assertFalse(gate.passed)
        self.assertFalse(self.risk.approved(gates))

    def test_daily_loss_circuit_breaker_blocks_new_risk(self) -> None:
        account = self.account.model_copy(update={"equity": 96_500.0, "last_equity": 100_000.0})
        gates = self.risk.evaluate(self.plan, account, self.market, False)
        gate = next(item for item in gates if item.name == "daily loss circuit breaker")
        self.assertFalse(gate.passed)

    def test_options_permission_is_enforced(self) -> None:
        account = self.account.model_copy(update={"options_trading_level": 2})
        gates = self.risk.evaluate(self.plan, account, self.market, False)
        gate = next(item for item in gates if item.name == "options permission")
        self.assertFalse(gate.passed)

    def test_a_short_leg_without_matching_geometry_is_rejected(self) -> None:
        broken = self.plan.model_copy(update={"legs": self.plan.legs[1:]})
        gates = self.risk.evaluate(broken, self.account, self.market, False)
        gate = next(item for item in gates if item.name == "defined risk")
        self.assertFalse(gate.passed)

    def test_stale_market_snapshot_is_rejected(self) -> None:
        from datetime import timedelta

        stale = self.market.model_copy(update={"captured_at": self.market.captured_at - timedelta(hours=1)})
        gates = self.risk.evaluate(self.plan, self.account, stale, False)
        gate = next(item for item in gates if item.name == "quote freshness")
        self.assertFalse(gate.passed)


if __name__ == "__main__":
    unittest.main()
