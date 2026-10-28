"""Offline gate tests; no Google Drive tokens, market API, or broker access."""
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import nml_eclear_ingest as module


def rows(n=3):
    dates = pd.bdate_range("2026-10-20", periods=n)
    return pd.DataFrame({
        "date": [x.strftime("%Y-%m-%d") for x in dates],
        "open": [120.0] * n,
        "high": [123.0] * n,
        "low": [119.0] * n,
        "close": [121.0] * n,
        "volume": [879284] * n,
    })


class BrokerNMLIngestTests(unittest.TestCase):
    def test_structurally_valid_short_sample(self):
        cleaned = module.validate_frame(rows(), require_snapshot=False)
        self.assertEqual(len(cleaned), 3)

    def test_impossible_candlestick_rejected(self):
        example = rows()
        example.loc[0, "high"] = 115
        with self.assertRaisesRegex(ValueError, "impossible OHLCV"):
            module.validate_frame(example, require_snapshot=False)

    def test_weekend_candlestick_rejected(self):
        example = rows()
        example.loc[1, "date"] = "2026-10-24"
        example = example.sort_values("date")
        with self.assertRaisesRegex(ValueError, "weekend"):
            module.validate_frame(example, require_snapshot=False)

    def test_duplicate_date_rejected(self):
        example = rows()
        example.loc[1, "date"] = example.loc[0, "date"]
        with self.assertRaisesRegex(ValueError, "unique"):
            module.validate_frame(example, require_snapshot=False)

    def test_unverified_change_to_private_csv_is_blocked(self):
        fake = io.BytesIO(rows().to_csv(index=False).encode()).getvalue()
        entries = {
            "Chart References": {"name":"Chart References", "id":"chart",
                                 "mimeType":"application/vnd.google-apps.folder"},
            module.FOLDERS[-1]: {"name":module.FOLDERS[-1], "id":"nml",
                                 "mimeType":"application/vnd.google-apps.folder"},
            module.FILENAME: {"name":module.FILENAME, "id":"csv",
                              "mimeType":"text/csv", "size":str(len(fake))},
        }
        with patch.object(module, "list_drive_files", side_effect=lambda s,p: list(entries.values())), \
             patch.object(module, "download_csv", return_value=fake), TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                module.import_from_drive(None, "root", Path(tmp))
            self.assertFalse((Path(tmp) / "NML.csv").exists())

    def test_snapshot_requires_full_93_sessions(self):
        with self.assertRaisesRegex(ValueError, "93-session snapshot"):
            module.validate_frame(rows(), require_snapshot=True)


if __name__ == "__main__":
    unittest.main()
