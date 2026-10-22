"""Offline tests of persistent low-request daily PSX OHLCV collection."""
from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from incremental_collector import (
    append_session, collect, daily_rows_to_bars, read_existing
)
from test_psx_dps_feed import ClientFake as MonthlyFake

PKT = ZoneInfo("Asia/Karachi")


def bulk_row(symbol: str, close: float = 119.0) -> dict:
    return {
        "symbol": symbol, "open": "118.00", "high": "122.00",
        "low": "115.00", "close": str(close), "volume": "145,000"
    }


class FakeClient(MonthlyFake):
    def __init__(self, fail_symbol=None):
        self.fail_symbol = fail_symbol
        self.month_requests = []
        self.bulk_requests = []

    def history_by_date(self, session):
        self.bulk_requests.append(session)
        result = [bulk_row("ZZ" + str(i)) for i in range(45)]
        result.extend([bulk_row("SAZEW"), bulk_row("LUCK"),
                       bulk_row("SYS"), bulk_row("OGDC")])
        return result

    def history_by_month(self, symbol, month, year):
        self.month_requests.append((symbol, month, year))
        if symbol == self.fail_symbol:
            class TransportError(Exception):
                pass
            raise TransportError("Do not continue after server failure")
        return super().history_by_month(symbol, month, year)


class IncrementalTests(unittest.TestCase):
    def test_bulk_ohlcv_one_day(self):
        rows = [bulk_row("ZZ" + str(i)) for i in range(31)] + [bulk_row("SAZEW")]
        bars = daily_rows_to_bars(rows, date(2026, 10, 22), {"SAZEW"})
        self.assertEqual(list(bars), ["SAZEW"])
        self.assertEqual(bars["SAZEW"]["date"], "2026-10-22")
        self.assertEqual(bars["SAZEW"]["close"], 119)

    def test_bad_ohlc_daily_rejected(self):
        rows = [bulk_row("ZZ" + str(i)) for i in range(31)] + [
            {**bulk_row("SAZEW"), "high": "90.00"}
        ]
        self.assertEqual(daily_rows_to_bars(rows, date(2026, 10, 22),
                                           {"SAZEW"}), {})

    def test_small_bulk_response_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            daily_rows_to_bars([bulk_row("SAZEW")],
                               date(2026, 10, 22), {"SAZEW"})

    def test_bootstrap_is_bounded_and_persists(self):
        now = datetime(2026, 10, 22, 18, tzinfo=PKT)
        stocks = ["SAZEW", "LUCK", "SYS", "OGDC"]
        with TemporaryDirectory() as tmp:
            place = Path(tmp)
            first = FakeClient()
            summary1 = collect(first, place, stocks, now, bootstrap_limit=2)
            self.assertEqual(summary1["completed"], 2)
            self.assertEqual(summary1["historical_bootstrap_attempts"], 2)
            self.assertEqual(len(first.bulk_requests), 1)
            self.assertEqual(len(first.month_requests), 10)
            self.assertTrue((place / "fetch_errors.json").exists())
            self.assertEqual(set(json.loads((place / "fetch_errors.json").read_text())),
                             {"SYS", "OGDC"})
            self.assertIsNotNone(read_existing(place, "SAZEW", now.date()))
            second = FakeClient()
            summary2 = collect(second, place, stocks, now, bootstrap_limit=2)
            self.assertEqual(summary2["completed"], 4)
            self.assertEqual(len(second.bulk_requests), 1)
            self.assertEqual(len(second.month_requests), 10)
            self.assertEqual(json.loads((place / "fetch_errors.json").read_text()), {})
            sazew = read_existing(place, "SAZEW", now.date())
            self.assertEqual(sazew["date"].iloc[-1].date(), now.date())
            self.assertEqual((sazew["date"] == now.date()).sum(), 1)

    def test_circuit_breaker_stops_backfill(self):
        now = datetime(2026, 10, 22, 18, tzinfo=PKT)
        with TemporaryDirectory() as tmp:
            place = Path(tmp)
            client = FakeClient(fail_symbol="SAZEW")
            outcome = collect(client, place, ["SAZEW", "LUCK", "SYS"], now,
                              bootstrap_limit=3)
            self.assertTrue(outcome["cooldown_respected"])
            self.assertEqual(outcome["historical_bootstrap_attempts"], 1)
            self.assertEqual(len(client.bulk_requests), 1)
            self.assertEqual(len(client.month_requests), 1)
            self.assertEqual(outcome["completed"], 0)
            self.assertEqual(len(json.loads((place / "fetch_errors.json").read_text())), 3)

    def test_daily_updater_preserves_prior_history(self):
        now = datetime(2026, 10, 22, 18, tzinfo=PKT)
        with TemporaryDirectory() as tmp:
            place = Path(tmp)
            client = FakeClient()
            collect(client, place, ["SAZEW"], now, bootstrap_limit=1)
            before = read_existing(place, "SAZEW", now.date())
            again = FakeClient()
            collect(again, place, ["SAZEW"], now, bootstrap_limit=1)
            after = read_existing(place, "SAZEW", now.date())
            self.assertEqual(len(before), len(after))
            self.assertEqual(len(again.month_requests), 0)


if __name__ == "__main__":
    unittest.main()
