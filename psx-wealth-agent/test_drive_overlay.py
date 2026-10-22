"""Offline tests: screenshot OCR supplement cannot trump exchange candles."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from agent import analyze
from drive_overlay import import_one, merge_trusted_and_ocr, validate_reviewed_pair, best_matching_files
from local_vision_ingest import csv_bytes
from position_guard import assess_portfolios


def screenshot_rows(count=90):
    days = pd.date_range("2026-03-02", periods=count, freq="B")
    result = []
    for i, d in enumerate(days):
        c = round(100 + 0.2 * i, 2)
        result.append({"date":d.strftime("%Y-%m-%d"),
                       "open":c, "high":round(c + 2, 2),
                       "low":round(c - 2, 2), "close":round(c + 1, 2),
                       "volume": 500_000 + i})
    return result


def pair(rows, reviewed=True):
    content = csv_bytes(rows)
    proof = {
        "symbol":"SAZEW", "source":"ollama_vision_from_broker_screenshots",
        "csv_sha256":hashlib.sha256(content).hexdigest(),
        "reviewed":reviewed, "reviewed_at":"2026-10-22T18:00:00+05:00" if reviewed else None,
        "image_hashes":["a" * 64], "row_count":len(rows)
    }
    return content, json.dumps(proof).encode()


class OcrCloudSafetyTests(unittest.TestCase):
    def test_unreviewed_rejected(self):
        content, proof = pair(screenshot_rows(85), reviewed=False)
        with self.assertRaisesRegex(ValueError, "not manually approved"):
            validate_reviewed_pair(content, proof, "SAZEW")

    def test_changed_csv_fails_checksum(self):
        content, proof = pair(screenshot_rows(85))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            validate_reviewed_pair(content + b"\n", proof, "SAZEW")

    def test_reviewed_partial_data_not_analyzed(self):
        with TemporaryDirectory() as d:
            content, proof = pair(screenshot_rows(20))
            result = import_one(Path(d), "SAZEW", content, proof)
            self.assertFalse(result["ready_for_research"])
            self.assertFalse((Path(d) / "SAZEW.csv").exists())
            self.assertTrue((Path(d) / "ocr_partial" / "SAZEW.csv").exists())

    def test_approved_90_sessions_become_valid_research_csv(self):
        with TemporaryDirectory() as d:
            content, proof = pair(screenshot_rows(90))
            result = import_one(Path(d), "SAZEW", content, proof)
            self.assertTrue(result["ready_for_research"])
            df = pd.read_csv(Path(d) / "SAZEW.csv")
            self.assertEqual(len(df), 90)
            item = analyze("SAZEW", df)
            self.assertEqual(item["full_verdict_score"], None)
            self.assertFalse(item["buy_approved"])

    def test_trusted_exchange_price_not_overridden(self):
        screenshot = screenshot_rows(90)
        trusted = pd.DataFrame(screenshot)
        trusted.loc[20, "close"] = trusted.loc[20, "close"] + 1
        supplemental = pd.DataFrame(screenshot)
        df, added, conflicts = merge_trusted_and_ocr(trusted, supplemental)
        self.assertEqual(len(df), 90)
        self.assertEqual(conflicts, 1)
        self.assertEqual(added, [])
        self.assertEqual(df.loc[20, "close"], trusted.loc[20, "close"])

    def test_invalid_name_or_wrong_ticker_excluded(self):
        files = [
            {"name":"SAZEW.csv", "mimeType":"text/csv", "modifiedTime":"2026-10-22T12:00Z"},
            {"name":"SAZEW.review.json", "mimeType":"application/json", "modifiedTime":"2026-10-22T12:00Z"},
            {"name":"portfolio.csv", "mimeType":"text/csv", "modifiedTime":"2026-10-22T12:00Z"},
        ]
        chosen = best_matching_files(files, {"SAZEW"})
        self.assertEqual(set(chosen), {"SAZEW"})
        self.assertEqual(len(chosen["SAZEW"]), 2)

    def test_private_sell_radar_blocks_ocr_research(self):
        with TemporaryDirectory() as d:
            rows = screenshot_rows(90)
            pd.DataFrame(rows).to_csv(Path(d) / "SAZEW.csv", index=False)
            report = {"results": [{
                "symbol":"SAZEW",
                "warnings":["screenshot_derived_candles_need_independent_verification"]
            }]}
            secret = json.dumps({"AccountA":{"SAZEW":{"style":"tactical","stop":1}}})
            result, count = assess_portfolios(report, secret, Path(d), "2026-10-22")
            self.assertEqual(count, 1)
            self.assertEqual(result[0]["verdict"], "OCR_RESEARCH_ONLY")
            self.assertFalse(result[0]["notify"])


if __name__ == "__main__":
    unittest.main()
