"""Offline test of free monthly OHLCV series adapter."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from psx_dps_feed import get_bars, month_back, to_bar


class ClientFake:
    def history_by_month(self, symbol, month, year):
        import calendar
        from datetime import date
        rows = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            stamp = date(year, month, day)
            if stamp.weekday() > 4 or stamp > date(2026, 10, 22):
                continue
            c = 100.0 + 0.2 * len(rows)
            rows.append({"time": stamp.strftime("%b %d, %Y"),
                         "open": f"{c:.2f}", "high": f"{c + 2:.2f}",
                         "low": f"{c - 2:.2f}", "close": f"{c + 0.1:.2f}",
                         "volume": "120,000"})
        return rows


class AdapterTests(unittest.TestCase):
    def test_month_offsets(self):
        self.assertEqual(month_back(2026, 1, 1), (2025, 12))
        self.assertEqual(month_back(2026, 10, 5), (2026, 5))

    def test_row_conversion(self):
        row = to_bar({"Time": "Oct 21, 2026", "Open": "1,453.00",
                      "High": "1,490.00", "Low": "1,447.00",
                      "Close": "1,480.00", "Volume": "200,234"})
        self.assertEqual(row["date"], "2026-10-21")
        self.assertEqual(row["close"], 1480)
        self.assertEqual(row["volume"], 200234)

    def test_six_months_complete_daily_bars(self):
        now = datetime(2026, 10, 22, 18, tzinfo=ZoneInfo("Asia/Karachi"))
        bars = get_bars(ClientFake(), "SAZEW", now)
        self.assertGreaterEqual(len(bars), 80)
        self.assertEqual(bars["date"].iloc[-1].strftime("%Y-%m-%d"), "2026-10-22")

    def test_invalid_symbol_fails(self):
        now = datetime(2026, 10, 22, 18, tzinfo=ZoneInfo("Asia/Karachi"))
        with self.assertRaisesRegex(ValueError, "Invalid ticker"):
            get_bars(ClientFake(), "../SAZEW", now)


if __name__ == "__main__":
    unittest.main()
