"""Offline position-exit alert tests; no real private holdings or credentials."""
import json
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from position_guard import (
    ALERT_LEVELS, assess_portfolios, evaluate_position,
    parse_portfolios, private_email, technical_exit_radar
)
from test_agent import synthetic_prices


def radar(**overrides):
    data = {
        "date": "2026-10-22", "close": 95.0, "rsi14": 35.0,
        "ema_bearish": True, "ema_downcross_today": False,
        "supertrend_red": True, "supertrend_flipped_red": False,
        "below_sma20": True, "prior20_low": 93.0,
        "fresh_20d_break": False, "volume_ratio": 1.0,
    }
    data.update(overrides)
    return data


class PositionGuardTests(unittest.TestCase):
    def test_loads_distinct_accounts_without_guessing_holdings(self):
        secret = json.dumps({
            "AccountA": {"TEST": {"style": "tactical", "stop": 90}},
            "AccountB": {"SAMPLE": {"style": "long_term"}}
        })
        results = parse_portfolios(secret)
        self.assertEqual(results["AccountA"]["TEST"]["stop"], 90.0)
        self.assertEqual(results["AccountB"]["SAMPLE"]["style"], "long_term")

    def test_rejects_json_without_positions_or_bad_stop(self):
        for text in ['{"AccountA": "ALL"}',
                     '{"AccountA":{"TEST":{"stop":-12}}}',
                     '{"AccountA":{"TEST":{"style":"high_risk"}}}',
                     '{"AccountA":{"../WRONG":{}}}',
                     '{"AccountA":{"TEST":{"cost":10}}}']:
            with self.subTest(value=text):
                with self.assertRaises(ValueError):
                    parse_portfolios(text)

    def test_single_bearish_trend_does_not_sell_long_term(self):
        result = evaluate_position(radar(), {"style": "long_term"}, "2026-10-22")
        self.assertEqual(result["verdict"], "WATCH_RISK")
        self.assertFalse(result["notify"])

    def test_breached_owner_stop_escalates_exit_review(self):
        result = evaluate_position(radar(close=89), {"style": "long_term", "stop": 90}, "2026-10-22")
        self.assertEqual(result["verdict"], "EXIT_REVIEW")
        self.assertTrue(result["notify"])

    def test_tactical_fresh_break_escalates_exit_review(self):
        result = evaluate_position(radar(close=90, fresh_20d_break=True),
                                   {"style": "tactical"}, "2026-10-22")
        self.assertEqual(result["verdict"], "EXIT_REVIEW")

    def test_longterm_fresh_break_requests_reduce_not_automatic_sell(self):
        result = evaluate_position(radar(close=90, fresh_20d_break=True),
                                   {"style": "long_term"}, "2026-10-22")
        self.assertEqual(result["verdict"], "REDUCE_REVIEW")

    def test_owner_take_profit_target(self):
        result = evaluate_position(
            radar(close=125, rsi14=55, ema_bearish=False, supertrend_red=False,
                  below_sma20=False),
            {"style": "long_term", "take_profit": 120}, "2026-10-22")
        self.assertEqual(result["verdict"], "TAKE_PROFIT_REVIEW")

    def test_seven_percent_gross_review_applies_to_tactical_lots_with_entry(self):
        config = parse_portfolios('{"AccountA":{"TEST":{"style":"tactical","entry":100}}}')
        entry = config["AccountA"]["TEST"]["entry"]
        result = evaluate_position(
            radar(close=107, rsi14=55, ema_bearish=False,
                  supertrend_red=False, below_sma20=False),
            {"style":"tactical", "entry":entry}, "2026-10-22")
        self.assertEqual(result["verdict"], "TAKE_PROFIT_REVIEW")
        self.assertTrue(result["notify"])
        self.assertIn("GROSS", result["reasons"][0])

    def test_long_term_does_not_auto_sell_at_seven_percent(self):
        result = evaluate_position(
            radar(close=107, rsi14=55, ema_bearish=False,
                  supertrend_red=False, below_sma20=False),
            {"style":"long_term", "entry":100}, "2026-10-22")
        self.assertEqual(result["verdict"], "HOLD_REVIEW")

    def test_stale_prices_can_never_trigger_exit_email(self):
        outcome = evaluate_position(radar(date="2026-10-21", close=85),
                                    {"stop": 90}, "2026-10-22")
        self.assertEqual(outcome["verdict"], "NO_FRESH_DATA")
        self.assertFalse(outcome["notify"])

    def test_analyzed_market_history_gives_only_private_owner_mappings(self):
        frame = synthetic_prices()
        with TemporaryDirectory() as directory:
            location = Path(directory)
            frame.to_csv(location / "TEST.csv", index=False)
            report = {"results": [{"symbol": "TEST"}]}
            secret = json.dumps({"AccountA": {"TEST": {"style": "long_term"},
                                               "NONE": {"style": "long_term"}}})
            outcomes, count = assess_portfolios(report, secret, location, "2026-10-22")
            self.assertEqual(count, 1)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[1]["verdict"], "NO_FRESH_DATA")

    def test_private_email_only_includes_flagged_positions(self):
        outcomes = [{"owner": "AccountA", "symbol": "TEST", "verdict": "EXIT_REVIEW",
                     "notify": True, "price": 89, "signal_date": "2026-10-22",
                     "reasons": ["Explicit stop breached"]},
                    {"owner": "AccountB", "symbol": "SAMPLE", "verdict": "HOLD_REVIEW",
                     "notify": False, "price": 105, "signal_date": "2026-10-22",
                     "reasons": ["No trigger"]}]
        body = private_email(outcomes, "2026-10-22", 1)
        self.assertIn("AccountA / TEST", body)
        self.assertNotIn("AccountB / SAMPLE", body)
        self.assertIn("No orders were sent", body)

    def test_radar_output_has_no_ownership_fields(self):
        raw = technical_exit_radar(synthetic_prices())
        self.assertIn("prior20_low", raw)
        self.assertIn("supertrend_flipped_red", raw)
        self.assertNotIn("owner", raw)
        self.assertNotIn("shares", raw)


if __name__ == "__main__":
    unittest.main()
