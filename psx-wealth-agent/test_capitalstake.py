"""Validate official Capital Stake API normalization offline."""
import unittest
from datetime import date, timedelta
from unittest.mock import Mock

from capitalstake_feed import fetch_bars, parse_bars


def response(count=130):
    begin = date(2026, 1, 5)
    # Timestamps are 12:00 UTC for easy PKT session mapping.
    from datetime import datetime, timezone
    rows = []
    for i in range(count):
        dt = begin + timedelta(days=i)
        unix = int(datetime(dt.year, dt.month, dt.day, 12, tzinfo=timezone.utc).timestamp())
        c = 100 + i / 5
        rows.append({"time": unix, "open": c, "high": c + 1,
                     "low": c - 1, "close": c, "volume": 100_000 + 100 * i})
    return {"status": "ok", "data": rows}


class CapitalStakeTests(unittest.TestCase):
    def test_json_to_valid_csv(self):
        bars = parse_bars(response())
        self.assertEqual(len(bars), 130)
        self.assertEqual(list(bars.columns), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(bars.iloc[0]["date"].strftime("%Y-%m-%d"), "2026-01-05")

    def test_provider_error_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-success"):
            parse_bars({"status": "error", "message": "unauthorized_request"})

    def test_duplicate_session_rejected(self):
        p = response()
        p["data"].append(dict(p["data"][-1]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_bars(p)

    def test_bad_body_rejected(self):
        with self.assertRaises(ValueError):
            parse_bars({"status": "ok", "data": [{"symbol": "LUCK"}]})

    def test_authorized_header_and_known_endpoint(self):
        sess = Mock()
        http = Mock()
        http.content = b"{}"
        http.json.return_value = response()
        sess.get.return_value = http
        bars = fetch_bars(sess, "dummy-test-only", "LUCK",
                          "2026-01-05", "2026-06-10")
        self.assertEqual(len(bars), 130)
        args, kwargs = sess.get.call_args
        self.assertEqual(args[0], "https://csapis.com/3.0/market/eod")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer dummy-test-only")
        self.assertEqual(kwargs["params"]["symbol"], "LUCK")
        self.assertEqual(kwargs["timeout"], 35)

    def test_symbol_injection_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid stock symbol"):
            fetch_bars(Mock(), "not-a-token", "../LUCK", "2026-01-01", "2026-06-01")


if __name__ == "__main__":
    unittest.main()
