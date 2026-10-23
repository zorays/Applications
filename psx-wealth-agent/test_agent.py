"""Offline unit tests: synthetic data, no PSX network calls."""
import unittest
from datetime import date, timedelta

import pandas as pd

from agent import analyze, normalize, render_report, supertrend


def synthetic_prices(count=130, direction=1):
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(count)]
    # Increasing or decreasing close, with OHLC constraints and finite volume.
    close = [150 + direction * (i * 0.35) for i in range(count)]
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": [c + 1.0 for c in close],
        "low": [c - 1.0 for c in close],
        "close": close,
        "volume": [200_000 + i * 100 for i in range(count)],
    })


class AgentTests(unittest.TestCase):
    def test_uptrend(self):
        data = synthetic_prices()
        result = analyze("TEST", data, today=date(2026, 5, 10))
        self.assertTrue(result["indicators"]["ema10_above_ema21"])
        self.assertTrue(result["supertrend_green"])
        self.assertGreater(result["rsi14"], 50)
        self.assertEqual(result["full_verdict_score"], None)
        self.assertFalse(result["buy_approved"])
        self.assertEqual(result["technical_component_out_of_15"], 15)

    def test_sma_10_21_and_mean_reversion_are_research_only(self):
        result = analyze("TEST", synthetic_prices(), today=date(2026, 5, 10))
        self.assertIsNotNone(result["sma10"])
        self.assertIsNotNone(result["sma21"])
        self.assertIsNotNone(result["sma50"])
        self.assertIsNone(result["sma200"])  # only 130 real bars available
        self.assertIn("mean_reversion_state", result)
        self.assertTrue(0 <= result["mean_reversion_research_component_out_of_20"] <= 20)
        self.assertIsNone(result["full_verdict_score"])
        self.assertFalse(result["buy_approved"])

    def test_downtrend(self):
        data = synthetic_prices(direction=-1)
        result = analyze("TEST", data, today=date(2026, 5, 10))
        self.assertFalse(result["indicators"]["ema10_above_ema21"])
        self.assertFalse(result["supertrend_green"])
        self.assertLess(result["rsi14"], 50)

    def test_insufficient_history_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "insufficient"):
            normalize(synthetic_prices(60))

    def test_bad_candle_is_rejected(self):
        data = synthetic_prices(130)
        data.loc[0, "low"] = -3
        result = normalize(data)
        self.assertEqual(len(result), 129)

    def test_report_never_approves(self):
        item = analyze("TEST", synthetic_prices(), today=date(2026, 5, 10))
        result = render_report([item], {}, "2026-05-10T12:00+05:00")
        self.assertIn("No automated trades or buy approvals", result)
        self.assertIn("TEST", result)


if __name__ == "__main__":
    unittest.main()
