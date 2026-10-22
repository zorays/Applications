"""Offline safety tests for Windows local Ollama + Drive screenshot pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock

import pandas as pd

from local_vision_ingest import (
    approve, ask_ollama, csv_bytes, filename_symbol, merge_extracts,
    parse_price, process_images, validate_image_rows, vision_schema
)


def row(day="2026-10-21", close="1496.46", opening="1475.00", high="1533.00",
        low="1475.00", volume="892,752"):
    return {"date": day, "open": opening, "high": high, "low": low,
            "close": close, "volume": volume}


class VisionIngestTests(unittest.TestCase):
    def test_numeric_ohlcv_and_date_parsing(self):
        result = validate_image_rows([row(day="Oct 21, 2026")], today=date(2026, 10, 22))
        self.assertEqual(result[0]["date"], "2026-10-21")
        self.assertEqual(result[0]["volume"], 892752)
        self.assertEqual(result[0]["close"], 1496.46)

    def test_refuse_fabricated_zero_candles_or_invalid_high(self):
        with self.assertRaisesRegex(ValueError, "Impossible"):
            validate_image_rows([row(high="1,400")], today=date(2026, 10, 22))
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            validate_image_rows([row(close="1,50O.00")], today=date(2026, 10, 22))
        with self.assertRaisesRegex(ValueError, "Future or weekend"):
            validate_image_rows([row(day="2026-10-24")], today=date(2026, 10, 24))

    def test_duplicate_sessions_must_agree(self):
        one = validate_image_rows([row(), row()], today=date(2026, 10, 22))
        self.assertEqual(len(one), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            merge_extracts([{"rows":[{"date":"2026-10-21", "close":1496}]},
                            {"rows":[{"date":"2026-10-21", "close":1497}]}])

    def test_image_filename_requires_explicit_ticker(self):
        self.assertEqual(filename_symbol(Path("SAZEW__page_01.png")), "SAZEW")
        with self.assertRaisesRegex(ValueError, "SYMBOL"):
            filename_symbol(Path("private-screen.png"))

    def test_local_vision_only_and_ticker_mismatch_rejected(self):
        self.assertIn("visible_rows", vision_schema()["properties"])
        with TemporaryDirectory() as path:
            img = Path(path) / "SAZEW__1.png"
            img.write_bytes(b"fake image bytes")
            bad = "https://example.com/api/chat"
            with self.assertRaisesRegex(ValueError, "loopback"):
                ask_ollama(img, "SAZEW", "test-vision", bad)
            response = Mock()
            response.json.return_value = {
                "message": {"content": json.dumps({
                    "symbol": "LUCK", "visible_rows": [row()], "notes": ""})}}
            with patch("local_vision_ingest.requests.post", return_value=response):
                with self.assertRaisesRegex(ValueError, "ticker"):
                    ask_ollama(img, "SAZEW", "test-vision", "http://127.0.0.1:11434/api/chat")

    def test_merge_130_sessions_requires_manual_approval(self):
        with TemporaryDirectory() as path:
            base = Path(path)
            pictures = base / "pictures"
            out = base / "out"
            pictures.mkdir()
            (base / "watchlist.txt").write_text("SAZEW\n", encoding="utf-8")
            days = pd.date_range("2026-01-05", periods=130, freq="B").strftime("%Y-%m-%d").tolist()
            batch = []
            for i in range(2):
                image = pictures / f"SAZEW__{i}.png"
                image.write_bytes(("fake screenshot " + str(i)).encode("utf-8"))
                batch.append([row(day=d) for d in days[i*65:(i+1)*65]])
            with patch("local_vision_ingest.ask_ollama",
                       side_effect=[(batch[0], ""), (batch[1], "")]):
                symbols, problems = process_images(pictures, out,
                    base / "watchlist.txt", "fake-model", "http://127.0.0.1:11434/api/chat")
            self.assertEqual(problems, [])
            self.assertEqual(symbols, ["SAZEW"])
            rows = list(csv.DictReader((out / "SAZEW.csv").open(encoding="utf-8")))
            self.assertEqual(len(rows), 130)
            meta = json.loads((out / "SAZEW.review.json").read_text())
            self.assertFalse(meta["reviewed"])
            approved = approve(out, "SAZEW", base / "watchlist.txt")
            self.assertTrue(approved["reviewed"])
            self.assertEqual(approved["row_count"], 130)
            self.assertEqual(approved["csv_sha256"],
                             hashlib.sha256((out / "SAZEW.csv").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
