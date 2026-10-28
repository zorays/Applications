"""Offline regression tests for native broker screenshot CSV admission."""
import hashlib
import io
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from broker_eclear_ingest import verify_pair, overlay_one, find_pairs, SOURCE


def series(n=92):
    end = pd.Timestamp("2026-10-27")
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": [120.0 + i * .1 for i in range(n)],
        "high": [122.0 + i * .1 for i in range(n)],
        "low": [119.0 + i * .1 for i in range(n)],
        "close": [121.0 + i * .1 for i in range(n)],
        "volume": [850000 + i * 1500 for i in range(n)],
    })


def pair(frame=None, research_only=True):
    frame = series() if frame is None else frame
    raw = frame.to_csv(index=False).encode()
    meta = {
        "symbol": "NML", "source": SOURCE, "research_only": research_only,
        "trading_authority": False, "full_251_sessions_complete": False,
        "csv_sha256": hashlib.sha256(raw).hexdigest(),
        "row_count": len(frame), "first_session": frame.iloc[0]["date"],
        "last_session": frame.iloc[-1]["date"],
        "gaps_review_required": [], "latest_session_max_age_days": 7,
    }
    return raw, json.dumps(meta).encode()


class EClearResearchTests(unittest.TestCase):
    def test_valid_92_owner_trusted_candles(self):
        raw, meta = pair()
        valid = verify_pair("NML", raw, meta, date(2026, 10, 28))
        self.assertEqual(len(valid), 92)
        with TemporaryDirectory() as temp:
            p = Path(temp)
            result = overlay_one(p, "NML", raw, meta, date(2026, 10, 28))
            self.assertEqual(result["validated_rows"], 92)
            self.assertTrue((p / "NML.csv").exists())
            self.assertFalse(result["buy_approved"])
            provenance = json.loads((p / "ocr_sources.json").read_text())
            self.assertTrue(provenance["NML"]["research_only"])

    def test_reject_checksum_mismatch(self):
        raw, meta = pair()
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_pair("NML", raw + b" ", meta, date(2026, 10, 28))

    def test_reject_weekend_and_zero_price(self):
        frame = series()
        frame.loc[0, "date"] = "2026-06-13"
        raw, meta = pair(frame)
        with self.assertRaisesRegex(ValueError, "Weekend"):
            verify_pair("NML", raw, meta, date(2026, 10, 28))
        frame = series()
        frame.loc[0, "low"] = 0
        raw, meta = pair(frame)
        with self.assertRaisesRegex(ValueError, "Zero/negative"):
            verify_pair("NML", raw, meta, date(2026, 10, 28))

    def test_reject_changing_source_and_trade_privilege(self):
        raw, meta = pair()
        data = json.loads(meta)
        data["trading_authority"] = True
        with self.assertRaisesRegex(ValueError, "research-only"):
            verify_pair("NML", raw, json.dumps(data).encode(), date(2026, 10, 28))
        data["trading_authority"] = False
        data["source"] = "ollama_cloud_vision_from_broker_screenshots"
        with self.assertRaisesRegex(ValueError, "provenance"):
            verify_pair("NML", raw, json.dumps(data).encode(), date(2026, 10, 28))

    def test_stale_feed_rejected(self):
        raw, meta = pair()
        with self.assertRaisesRegex(ValueError, "stale"):
            verify_pair("NML", raw, meta, date(2026, 11, 14))

    def test_prevent_existing_market_price_conflict(self):
        frame = series()
        raw, meta = pair(frame)
        with TemporaryDirectory() as temp:
            path = Path(temp)
            path.mkdir(exist_ok=True)
            old = frame.copy()
            old.loc[1, "close"] += 0.5
            old.to_csv(path / "NML.csv", index=False)
            with self.assertRaisesRegex(ValueError, "conflicts"):
                overlay_one(path, "NML", raw, meta, date(2026, 10, 28))

    def test_find_only_correct_files(self):
        files = [
            {"name": "NML.eclear.csv", "mimeType": "text/csv"},
            {"name": "NML.eclear.audit.json", "mimeType": "application/json"},
            {"name": "FFC.eclear.csv", "mimeType": "text/csv"}
        ]
        selected = find_pairs(files, {"NML"})
        self.assertEqual(set(selected), {"NML"})
        self.assertEqual(len(selected["NML"]), 2)


if __name__ == "__main__":
    unittest.main()
