from __future__ import annotations

import unittest
from datetime import date

from config import Settings
from models import AccountSnapshot, StrategyKind
from services.market_provider import MarketProvider


class PositionParsingTests(unittest.TestCase):
    def test_two_long_occ_legs_are_grouped_as_a_strangle(self) -> None:
        raw = [
            {
                "symbol": "NVDA260925C00225000",
                "side": "long",
                "qty": "1",
                "cost_basis": "420",
                "market_value": "455",
                "unrealized_pl": "35",
            },
            {
                "symbol": "NVDA260925P00210000",
                "side": "long",
                "qty": "1",
                "cost_basis": "390",
                "market_value": "360",
                "unrealized_pl": "-30",
            },
        ]
        positions = MarketProvider._parse_position_views(raw, 100_000.0)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "NVDA")
        self.assertEqual(positions[0].strategy, StrategyKind.LONG_STRANGLE)
        self.assertEqual(positions[0].max_loss, 810.0)
        self.assertEqual(len(positions[0].legs), 2)


class MarketPulseTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_pulse_covers_cross_asset_groups(self) -> None:
        provider = MarketProvider(Settings(_env_file=None))
        items = await provider.market_pulse()
        groups = {item.group for item in items}
        symbols = {item.symbol for item in items}

        self.assertGreaterEqual(len(items), 30)
        self.assertEqual(groups, {"Indices", "Commodities", "Macro & risk", "Sectors", "Leaders"})
        self.assertTrue({"SPY", "QQQ", "GLD", "USO", "TLT", "XLK", "NVDA"}.issubset(symbols))
        self.assertTrue(all(len(item.bars) == 30 for item in items))
        self.assertTrue(all(item.price > 0 for item in items))


class PortfolioHistoryTests(unittest.TestCase):
    @staticmethod
    def account() -> AccountSnapshot:
        return AccountSnapshot(
            account_id="paper-account",
            equity=101_000,
            last_equity=100_500,
            cash=90_000,
            options_buying_power=90_000,
            portfolio_value=101_000,
            starting_balance=100_000,
        )

    def test_aligns_broker_equity_with_spy(self) -> None:
        portfolio = {
            "timestamp": [1788220800, 1788307200],
            "equity": [100_000, 101_000],
        }
        benchmark = {
            "bars": [
                {"t": "2026-09-01T04:00:00Z", "c": 650},
                {"t": "2026-09-02T04:00:00Z", "c": 656.5},
            ]
        }

        points = MarketProvider._portfolio_points(
            portfolio,
            benchmark,
            self.account(),
            date(2026, 9, 1),
        )

        self.assertEqual([point.label for point in points], ["2026-09-01", "2026-09-02"])
        self.assertEqual(points[-1].equity, 101_000)
        self.assertEqual(points[-1].benchmark, 101_000)

    def test_omits_unsupported_comparison(self) -> None:
        points = MarketProvider._portfolio_points(
            {},
            {"bars": []},
            self.account(),
            date(2026, 9, 1),
        )
        self.assertEqual(points, [])


if __name__ == "__main__":
    unittest.main()
