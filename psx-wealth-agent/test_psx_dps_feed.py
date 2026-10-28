"""Offline test of free monthly OHLCV series adapter."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from zoneinfo import ZoneInfo

from psx_dps_feed import get_bars, month_back, to_bar, read_watchlist


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


    def test_watchlist_comments_duplicates_case(self):
        with TemporaryDirectory() as root:
            p = Path(root) / "watchlist.txt"
            p.write_text("# Our candidates\nSAZEW\n luck # cement\nLUCK\n\nSYS\n")
            self.assertEqual(read_watchlist(str(p)), ["SAZEW", "LUCK", "SYS"])

    def test_watchlist_invalid_symbol_rejected(self):
        with TemporaryDirectory() as root:
            p = Path(root) / "watchlist.txt"
            p.write_text("SAZEW\n../broker_credentials\n")
            with self.assertRaisesRegex(ValueError, "Invalid PSX symbol"):
                read_watchlist(str(p))

    def test_watchlist_maximum_enforced(self):
        with TemporaryDirectory() as root:
            p = Path(root) / "watchlist.txt"
            p.write_text("\n".join(f"X{n:02d}" for n in range(38)))
            with self.assertRaisesRegex(ValueError, "more than 37"):
                read_watchlist(str(p))

    def test_current_git_watchlist_includes_both_eclear_profiles(self):
        symbols = read_watchlist(str(Path(__file__).with_name("watchlist.txt")))
        expected = {"NML", "SAZEW", "LOADS", "OBOY", "CEPB", "ENGROH", "FFC", "LUCK",
                    "MEBL", "NETSOL", "OGDC", "SIEM", "SLM", "SRR", "SYS",
                    "TREET", "TRG", "TPLT", "DSIL", "PSO", "PPL", "MLCF",
                    "DGKC", "WASL", "WAVES", "WAVESAPP", "THCCL", "ANL",
                    "LOTCHEM", "PRL", "TPL"}
        self.assertTrue(expected.issubset(set(symbols)))
        self.assertEqual(len(symbols), 37)
        self.assertLessEqual(len(symbols), 37)
        self.assertLessEqual(len(symbols) * 5, 190)

    def test_watchlist_empty_rejected(self):
        with TemporaryDirectory() as root:
            p = Path(root) / "watchlist.txt"
            p.write_text("# Comments only\n")
            with self.assertRaisesRegex(ValueError, "no valid ticker"):
                read_watchlist(str(p))

    def test_invalid_symbol_fails(self):
        now = datetime(2026, 10, 22, 18, tzinfo=ZoneInfo("Asia/Karachi"))
        with self.assertRaisesRegex(ValueError, "Invalid ticker"):
            get_bars(ClientFake(), "../SAZEW", now)


if __name__ == "__main__":
    unittest.main()
