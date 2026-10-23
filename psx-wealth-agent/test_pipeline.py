"""Offline tests for Drive CSV handoff and conservative email gates."""
import unittest
from datetime import date

from alerts import candidate, make_message
from drive_ingest import choose_files, validate_csv
from test_agent import synthetic_prices


class IngestionTests(unittest.TestCase):
    def test_latest_exact_named_csv_only(self):
        files = [
            {"name": "SAZEW.csv", "id": "old_id", "mimeType": "text/csv", "modifiedTime": "2026-10-01T00:00:00Z"},
            {"name": "SAZEW.csv", "id": "new_id", "mimeType": "text/csv", "modifiedTime": "2026-10-21T00:00:00Z"},
            {"name": "portfolio.csv", "id": "private", "mimeType": "text/csv", "modifiedTime": "2026-10-21T00:00:00Z"},
            {"name": "LUCK.xlsx", "id": "sheet", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "modifiedTime": "2026-10-21T00:00:00Z"},
            {"name": "SYS.csv", "id": "third", "mimeType": "text/csv", "modifiedTime": "2026-10-20T00:00:00Z"},
        ]
        chosen = choose_files(files, {"SAZEW", "SYS", "LUCK"})
        self.assertEqual(chosen["SAZEW"]["id"], "new_id")
        self.assertIn("SYS", chosen)
        self.assertNotIn("LUCK", chosen)
        self.assertNotIn("portfolio", chosen)

    def test_historical_ohlcv_csv_validates(self):
        csv = synthetic_prices().to_csv(index=False).encode("utf-8")
        validate_csv(csv)

    def test_portfolio_statement_rejected(self):
        with self.assertRaises(ValueError):
            validate_csv(b"account,balance\nXYZ,19000\n")


class AlertTests(unittest.TestCase):
    def fixture(self):
        return {
            "symbol": "TEST",
            "last_bar": "2026-10-22",
            "setup": "B_TREND_RECOVERING",
            "technical_component_out_of_15": 15,
            "warnings": [],
            "buy_approved": False,
            "rsi14": 60,
            "volume_ratio_to_prior_20d": 1.5,
            "sma20_deviation_pct": 3,
            "median_daily_turnover_pkr_20d": 5000000,
            "illustrative_reward_risk": 2.8,
            "hypothetical_7pct_trade": {
                "status": "RESEARCH_ONLY_POLICY_PASSED",
                "net_reward_risk": 1.9,
            },
            "close_pkr": 100,
            "ema10": 101,
            "ema21": 99,
            "supertrend_green": True,
        }

    def test_technical_candidate_is_not_buy_approval(self):
        a = self.fixture()
        self.assertTrue(candidate(a, "2026-10-22"))
        self.assertIn("NOT a buy instruction", make_message([a], "2026-10-22"))

    def test_old_data_never_triggers(self):
        self.assertFalse(candidate(self.fixture(), "2026-10-23"))

    def test_warning_blocks_alert(self):
        a = self.fixture()
        a["warnings"] = ["thin_turnover_proxy"]
        self.assertFalse(candidate(a, "2026-10-22"))

    def test_overheated_blocks_alert(self):
        a = self.fixture()
        a["rsi14"] = 84
        self.assertFalse(candidate(a, "2026-10-22"))

    def test_unapproved_trade_never_auto_executes(self):
        a = self.fixture()
        a["buy_approved"] = True
        self.assertFalse(candidate(a, "2026-10-22"))


if __name__ == "__main__":
    unittest.main()
