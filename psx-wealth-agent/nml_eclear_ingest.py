"""Read owner-supplied NML EClear OHLCV from private Google Drive.

Source: EClear TradeCast OHLC History table screenshots.
The approved partial transcription is pinned to an exact SHA-256 and 93 sessions.
Research only; broker screenshot values are not independently exchange verified.
No broker authentication or trading operations are available.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

from agent import normalize
from drive_ingest import download_csv, list_drive_files

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
EXPECTED_SHA256 = "7f65d06b4276967584bb2ceaa1f66a6343b5b4a3733da6890cb654f9a31709d2"
EXPECTED_FIRST = "2026-06-11"
EXPECTED_LAST = "2026-10-27"
EXPECTED_COUNT = 93
FOLDERS = ("Chart References", "NML EClear OHLC History - 2026-10-28")
FILENAME = "NML_EClear_validated_93sessions_2026-10-28.csv"


def exact_child(session, parent_id: str, name: str) -> dict:
    matches = [item for item in list_drive_files(session, parent_id)
               if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError("Required NML OHLCV source absent or ambiguously duplicated")
    return matches[0]


def validate_frame(raw: pd.DataFrame, require_snapshot: bool = True) -> pd.DataFrame:
    required = ("date", "open", "high", "low", "close", "volume")
    if list(raw.columns) != list(required):
        raise ValueError("NML source must contain exact OHLCV columns")
    if raw.empty or len(raw) > 251:
        raise ValueError("NML source must contain 1–251 sessions")
    checked = raw.copy()
    checked["date"] = pd.to_datetime(checked["date"], errors="raise")
    if checked["date"].duplicated().any() or not checked["date"].is_monotonic_increasing:
        raise ValueError("NML source dates must be unique and ascending")
    if (checked["date"].dt.weekday >= 5).any():
        raise ValueError("NML screenshot contains nontrading weekend rows")
    if checked["date"].iloc[-1].date() > date.today():
        raise ValueError("NML screenshot contains a future date")
    if (checked["date"].diff().dt.days.dropna() > 7).any():
        raise ValueError("NML screenshot history contains a large calendar gap")
    for col in required[1:]:
        checked[col] = pd.to_numeric(checked[col], errors="raise")
    if ((checked[list(required[1:])].isna()).any().any() or
        (checked[["open", "high", "low", "close"]] <= 0).any().any() or
        (checked["volume"] < 0).any() or
        (checked["volume"] != checked["volume"].astype("int64")).any() or
        (checked["high"] < checked[["open", "close"]].max(axis=1)).any() or
        (checked["low"] > checked[["open", "close"]].min(axis=1)).any()):
        raise ValueError("NML source contains impossible OHLCV values")
    if require_snapshot and (
        len(checked) != EXPECTED_COUNT or
        checked["date"].iloc[0].date().isoformat() != EXPECTED_FIRST or
        checked["date"].iloc[-1].date().isoformat() != EXPECTED_LAST
    ):
        raise ValueError("NML source differs from verified 93-session snapshot")
    if len(checked) >= 80:
        normalized = normalize(checked)
        if len(normalized) != len(checked):
            raise ValueError("Price analyzer silently filtered NML sessions")
    return checked


def import_from_drive(session, root_folder: str, out: Path) -> dict:
    folder = root_folder
    for name in FOLDERS:
        item = exact_child(session, folder, name)
        if item.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError("Required NML source location is not a Drive folder")
        folder = item["id"]
    entry = exact_child(session, folder, FILENAME)
    if entry.get("mimeType") not in ("text/csv", "application/octet-stream"):
        raise ValueError("NML historical source must be a raw CSV")
    content = download_csv(session, entry)
    if hashlib.sha256(content).hexdigest() != EXPECTED_SHA256:
        raise ValueError("NML EClear CSV checksum mismatch; manual revalidation required")
    checked = validate_frame(pd.read_csv(io.BytesIO(content)))
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "NML.csv"
    checked.to_csv(destination, index=False, date_format="%Y-%m-%d")
    provenance = {
        "symbol": "NML",
        "source": "owner_supplied_eclear_tradecast_ohlc_history",
        "verification": "structurally_validated_not_independently_crosschecked",
        "historical_candles": len(checked),
        "source_checksum_sha256": EXPECTED_SHA256,
        "source_first_session": EXPECTED_FIRST,
        "source_last_session": EXPECTED_LAST,
        "source_screen_count": 4,
        "screenshots_available_in_drive": 11,
        "source_total_displayed_records": 251,
        "limitations": [
            "Partial 93-session visual transcription from 251 EClear records",
            "Two source Change-column discrepancies; Change excluded from OHLCV",
            "Only technical analysis; Shariah/valuation/portfolio gates not cleared",
        ],
        "trading_automation_enabled": False,
    }
    (out / "nml_source.json").write_text(json.dumps(provenance, indent=2))
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data")
    args = parser.parse_args()
    service_json = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    root_id = os.getenv("GDRIVE_FOLDER_ID", "").strip()
    if not service_json or not root_id:
        raise SystemExit("Missing read-only Google Drive service-account configuration")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    try:
        credentials = Credentials.from_service_account_info(
            json.loads(service_json), scopes=[DRIVE_SCOPE])
        provenance = import_from_drive(
            AuthorizedSession(credentials), root_id, Path(args.output))
        print("NML EClear fallback validated:", provenance["historical_candles"],
              "bars; last", provenance["source_last_session"],
              "source=broker screenshot; research only")
    except Exception as exc:
        # Do not leak private Drive IDs, credentials or response bodies.
        raise SystemExit("NML private EClear import failed: " +
                         type(exc).__name__) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
