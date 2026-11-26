import unittest

import numpy as np

from tools.history_backtest import _bs, greeks, quote


class HistoryBacktestTests(unittest.TestCase):
    def test_implied_volatility_round_trips_and_greeks_are_sane(self):
        strike = np.array([90.0, 100.0, 110.0, 100.0])
        call = np.array([True, True, True, False])
        price, _, _ = _bs(100.0, strike, 30 / 365, np.full(4, 0.35), call)
        vol, delta, gamma, theta, vega = greeks(100.0, strike, 30 / 365, price, call)
        np.testing.assert_allclose(vol, 0.35, atol=1e-4)
        self.assertTrue(delta[0] > delta[1] > delta[2] > 0 > delta[3] > -1)
        self.assertAlmostEqual(delta[1] - delta[3], 1.0, places=4)
        self.assertTrue((gamma > 0).all() and (vega > 0).all() and (theta < 0).all())

    def test_close_below_intrinsic_has_no_volatility(self):
        vol, *_ = greeks(100.0, np.array([80.0]), 30 / 365, np.array([15.0]), np.array([True]))
        self.assertTrue(np.isnan(vol[0]))

    def test_synthetic_quotes_straddle_the_close(self):
        bid, ask = quote("SPY", 3.00, 1.0)
        self.assertLess(bid, 3.00)
        self.assertGreater(ask, 3.00)
        self.assertEqual(quote("SPY", 3.00, 0.0), (3.00, 3.00))


if __name__ == "__main__":
    unittest.main()
