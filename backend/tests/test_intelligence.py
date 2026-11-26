from __future__ import annotations

import unittest

from config import Settings
from services.intelligence import (
    IntelligenceProvider,
    classify_catalyst,
    headline_metrics,
    score_sentiment,
)


class IntelligenceScoringTests(unittest.TestCase):
    def test_sentiment_fallback_is_directional_and_bounded(self) -> None:
        self.assertGreater(score_sentiment("Earnings beat as profit growth surges"), 0)
        self.assertLess(score_sentiment("Revenue misses as lawsuit risk grows"), 0)
        self.assertLessEqual(abs(score_sentiment("surge " * 100)), 1)

    def test_deployed_model_score_is_positive_minus_negative_probability(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        import numpy as np

        import services.intelligence as intelligence

        class Session:
            def get_inputs(self):
                return [SimpleNamespace(name="input_ids")]

            def run(self, _outputs, feeds):
                assert feeds["input_ids"].shape == (1, 3)
                return [np.log(np.asarray([[0.1, 0.2, 0.7]]))]

        tokenizer = SimpleNamespace(encode=lambda _text: SimpleNamespace(ids=[5, 6, 7]))
        with mock.patch.object(intelligence, "_sentiment_model", return_value=(Session(), tokenizer)):
            self.assertAlmostEqual(intelligence.score_sentiment("Guidance raised"), 0.6, places=3)
        broken = SimpleNamespace(get_inputs=lambda: (_ for _ in ()).throw(RuntimeError("bad model")))
        with mock.patch.object(intelligence, "_sentiment_model", return_value=(broken, tokenizer)):
            self.assertEqual(intelligence.score_sentiment("Revenue misses"), intelligence.lexicon_sentiment("Revenue misses"))

    def test_catalyst_classifier_prefers_specific_evidence(self) -> None:
        self.assertEqual(classify_catalyst("Quarterly earnings guidance was raised"), "earnings")
        self.assertEqual(classify_catalyst("DOJ opens a regulatory probe"), "regulatory")

    def test_headline_metrics_parses_alpaca_shape(self) -> None:
        score, catalysts = headline_metrics(
            {"news": [{"headline": "Company beats earnings expectations", "summary": "Profit growth"}]}
        )
        self.assertGreater(score, 0)
        self.assertIn("earnings", catalysts)

    def test_multi_symbol_story_has_unique_ids_and_duplicate_rows_are_removed(self) -> None:
        story = {
            "id": "a41f763e-508d-43c5-9d30-398be1fae4e4",
            "headline": "One story affects two companies",
            "symbols": ["AAPL", "MSFT"],
            "url": "https://example.com/story",
            "created_at": "2026-08-31T12:00:00Z",
        }
        insights = IntelligenceProvider._parse_alpaca(
            {"news": [story, story]},
            ["AAPL", "MSFT"],
        )

        self.assertEqual(len(insights), 2)
        self.assertEqual(len({item.id for item in insights}), 2)
        self.assertEqual({item.symbol for item in insights}, {"AAPL", "MSFT"})


class DemoIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_snapshot_is_labelled_and_keyless(self) -> None:
        provider = IntelligenceProvider(Settings(_env_file=None, volition_mode="demo"))
        snapshot = await provider.snapshot(["SPY", "QQQ", "NVDA"])
        self.assertEqual(len(snapshot.news), 3)
        self.assertTrue(all(item.provider == "demo-simulated" for item in snapshot.news))
        self.assertTrue(snapshot.sources[0].available)


if __name__ == "__main__":
    unittest.main()
