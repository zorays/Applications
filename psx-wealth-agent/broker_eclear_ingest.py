"""Read owner-supplied EClear OHLCV CSV into PSX research, never trade.

Distinct from Ollama vision ingestion: exact source and SHA-256 provenance are
required. The file is manually transcribed and explicitly PROVISIONAL.
No EClear logins or broker order APIs are accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from agent import MIN_BARS, normalize
from drive_ingest import download_csv, list_drive_files
from psx_dps_feed import read_watchlist

DATA_SUFFIX = ".eclear.csv"
AUDIT_SUFFIX = ".eclear.audit.json"
SOURCE = "owner_uploaded_eclear_ohlc_history_screenshots"
VERIFICATION = "owner_trusted_eclear_manually_transcribed_provisional"
MAX_CSV = 5_000_000
MAX_AUDIT = 100_000


def verify_pair(symbol: str, csv_data: bytes, audit_data: bytes,
                today: date | None = None) -> pd.DataFrame:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", symbol):
        raise ValueError("Invalid symbol")
    if len(csv_data) > MAX_CSV or len(audit_data) > MAX_AUDIT:
        raise ValueError("Oversized EClear evidence")
    meta = json.loads(audit_data)
    if not isinstance(meta, dict) or meta.get("symbol") != symbol:
        raise ValueError("Audit ticker mismatch")
    if meta.get("source") != SOURCE:
        raise ValueError("Unrecognized broker provenance")
    if (meta.get("research_only") is not True or
        meta.get("trading_authority") is not False or
        meta.get("full_251_sessions_complete") is not False):
        raise ValueError("This importer requires clearly provisional research-only evidence")
    if meta.get("csv_sha256") != hashlib.sha256(csv_data).hexdigest():
        raise ValueError("EClear CSV checksum mismatch")
    frame = pd.read_csv(io.BytesIO(csv_data), dtype={"date": "string"})
    required = ["date", "open", "high", "low", "close", "volume"]
    if list(frame.columns) != required or len(frame) < MIN_BARS or len(frame) > 251:
        raise ValueError("Expected at least 80 and at most 251 EClear OHLCV rows")
    if int(meta.get("row_count", -1)) != len(frame):
        raise ValueError("Audit row count mismatch")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate EClear session date")
    if (frame["date"].dt.weekday >= 5).any():
        raise ValueError("Weekend EClear session")
    for col in required[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Zero/negative EClear price")
    if (frame["volume"] <= 0).any() or not (frame["volume"] == frame["volume"].astype("int64")).all():
        raise ValueError("Invalid EClear volume")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Impossible EClear high")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Impossible EClear low")
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame["date"].iloc[0].date().isoformat() != meta.get("first_session"):
        raise ValueError("Audit first date mismatch")
    if frame["date"].iloc[-1].date().isoformat() != meta.get("last_session"):
        raise ValueError("Audit last date mismatch")
    today = today or date.today()
    age = (today - frame["date"].iloc[-1].date()).days
    if age < 0 or age > int(meta.get("latest_session_max_age_days", 7)):
        raise ValueError("EClear CSV is stale or future-dated")
    if (frame["date"].diff().dt.days.dropna() > 5).any():
        raise ValueError("Unexplained large calendar gap")
    # Even a 5-day gap may hide trading sessions; require explicit audit warning.
    if not isinstance(meta.get("gaps_review_required"), list):
        raise ValueError("Missing data-gap audit")
    valid = normalize(frame)
    if len(valid) != len(frame):
        raise ValueError("EClear normalization rejected rows")
    return valid


def overlay_one(data_dir: Path, symbol: str, csv_data: bytes, audit_data: bytes,
                today: date | None = None) -> dict:
    incoming = verify_pair(symbol, csv_data, audit_data, today=today)
    dest = data_dir / (symbol + ".csv")
    if dest.is_file():
        trusted = normalize(pd.read_csv(dest))
        overlap = incoming.merge(trusted, on="date", suffixes=("_eclear", "_other"))
        for row in overlap.itertuples(index=False):
            for field in ("open", "high", "low", "close", "volume"):
                tol = 1 if field == "volume" else 0.06
                if abs(float(getattr(row, field + "_eclear")) -
                       float(getattr(row, field + "_other"))) > tol:
                    raise ValueError("Broker CSV conflicts with existing PSX history")
        added = incoming.loc[~incoming["date"].isin(trusted["date"])]
        merged = pd.concat([trusted, added], ignore_index=True)
        merged = normalize(merged)
    else:
        added = incoming
        merged = incoming
    data_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(merged.to_csv(index=False), encoding="utf-8")
    state_path = data_dir / "ocr_sources.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    state[symbol] = {
        "source": SOURCE,
        "verification": VERIFICATION,
        "ocr_dates": incoming["date"].dt.strftime("%Y-%m-%d").tolist(),
        "research_only": True,
        "manually_transcribed": True,
        "known_source_gaps": json.loads(audit_data)["gaps_review_required"],
        "buy_approved": False,
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    error_file = data_dir / "fetch_errors.json"
    if error_file.exists():
        try:
            prior = json.loads(error_file.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                prior.pop(symbol, None)
                error_file.write_text(json.dumps(prior, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    result = {
        "symbol": symbol,
        "source": SOURCE,
        "verification": VERIFICATION,
        "validated_rows": len(incoming),
        "research_rows": len(merged),
        "new_sessions": len(added),
        "research_only": True,
        "buy_approved": False,
        "latest_close": float(merged["close"].iloc[-1]),
    }
    (data_dir / "eclear_import_summary.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    return result


def find_pairs(items: list[dict], symbols: set[str]) -> dict[str, dict]:
    paired: dict[str, dict] = {}
    for item in items:
        name = item.get("name", "")
        if not isinstance(name, str):
            continue
        suffix = DATA_SUFFIX if name.endswith(DATA_SUFFIX) else (
            AUDIT_SUFFIX if name.endswith(AUDIT_SUFFIX) else None)
        if suffix is None:
            continue
        symbol = name[:-len(suffix)]
        if symbol not in symbols:
            continue
        if item.get("mimeType") not in (
            "text/csv", "application/json", "application/octet-stream",
            "application/vnd.ms-excel"):
            continue
        p = paired.setdefault(symbol, {})
        old = p.get(suffix)
        if old is None or item.get("modifiedTime", "") > old.get("modifiedTime", ""):
            p[suffix] = item
    return paired


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--watchlist", default="watchlist.txt")
    parser.add_argument("--output", default="data")
    args = parser.parse_args()
    allowed = set(read_watchlist(args.watchlist))
    names = set(s.upper().strip() for s in args.symbols) if args.symbols else allowed
    if not names or not names.issubset(allowed):
        parser.error("Only watched stocks may be imported")
    folder = os.getenv("GDRIVE_FOLDER_ID", "").strip()
    secret = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON", "")
    if not folder or not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", folder) or not secret:
        raise SystemExit("Read-only Google Drive service account not configured")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    try:
        credentials = Credentials.from_service_account_info(
            json.loads(secret), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        session = AuthorizedSession(credentials)
        pairs = find_pairs(list_drive_files(session, folder), names)
        complete = {s: files for s, files in pairs.items()
                    if DATA_SUFFIX in files and AUDIT_SUFFIX in files}
        if not complete:
            raise ValueError("No matching EClear CSV/audit evidence in inbox")
        results = []
        for symbol in sorted(complete):
            try:
                entry = complete[symbol]
                result = overlay_one(
                    Path(args.output), symbol,
                    download_csv(session, entry[DATA_SUFFIX]),
                    download_csv(session, entry[AUDIT_SUFFIX]))
                results.append(result)
                print("EClear research: " + json.dumps(result, separators=(",", ":")))
            except Exception as exc:
                # Never include broker credentials or Google API responses in logs.
                print(symbol + ": EClear research skipped (" + type(exc).__name__ + ")")
        return 0 if results else 1
    except Exception as exc:
        raise SystemExit("EClear research import unavailable: " + type(exc).__name__) from None


if __name__ == "__main__":
    raise SystemExit(main())
