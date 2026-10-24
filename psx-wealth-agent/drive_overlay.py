"""Supplement PSX cache with manually reviewed or INDEPENDENTLY VERIFIED OCR CSV.

The screenshot-derived series is never privileged over trusted free PSX candles,
never overwrites conflicting exchange candles, and creates provenance warnings
to suppress actionable BUY/SELL alerts on affected technical lookbacks.

Only reads the Google Drive folder as a service account (Viewer).
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
from psx_dps_feed import read_watchlist

MAX_ROWS = 1000
MAX_META_BYTES = 100_000
ALLOWED_SOURCES = {
    "ollama_vision_from_broker_screenshots",
    "gemini_cloud_vision_from_broker_screenshots",
    "ollama_cloud_vision_from_broker_screenshots",
}


def validate_reviewed_pair(csv_data: bytes, json_data: bytes, symbol: str,
                           require_review: bool = True) -> pd.DataFrame:
    if len(json_data) > MAX_META_BYTES:
        raise ValueError("Provenance metadata is unexpectedly large")
    meta = json.loads(json_data)
    if (not isinstance(meta, dict) or
        meta.get("symbol") != symbol or
        meta.get("source") not in ALLOWED_SOURCES or
        type(meta.get("reviewed")) is not bool or
        (require_review and (meta.get("reviewed") is not True or
                             not meta.get("reviewed_at"))) or
        (meta.get("reviewed") is True and not meta.get("reviewed_at")) or
        not isinstance(meta.get("image_hashes"), list) or
        not meta["image_hashes"]):
        raise ValueError("Screenshot CSV is not manually approved")
    if hashlib.sha256(csv_data).hexdigest() != meta.get("csv_sha256"):
        raise ValueError("Screenshot CSV SHA256 differs from approved metadata")
    raw = pd.read_csv(io.BytesIO(csv_data), encoding="utf-8-sig")
    mandatory = {"date", "open", "high", "low", "close", "volume"}
    if set(raw.columns) != mandatory or not (1 <= len(raw) <= MAX_ROWS):
        raise ValueError("Screenshot CSV must contain only OHLCV and 1–1000 candles")
    parsed = raw.copy()
    parsed["date"] = pd.to_datetime(parsed["date"], errors="raise")
    if parsed["date"].dt.tz is not None or parsed["date"].duplicated().any():
        raise ValueError("Invalid or duplicated screenshot session dates")
    if parsed["date"].dt.date.max() > date.today():
        raise ValueError("Future session in approved screenshot")
    if (parsed["date"].dt.weekday >= 5).any():
        raise ValueError("Weekend trading session in approved screenshot")
    for col in ("open", "high", "low", "close", "volume"):
        parsed[col] = pd.to_numeric(parsed[col], errors="raise")
    if parsed[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("Missing OCR candle values")
    if ((parsed["volume"] < 0).any()
        or (parsed["volume"] != parsed["volume"].astype("int64")).any()
        or (parsed[["open", "high", "low", "close"]] <= 0).any().any()
        or (parsed["high"] < parsed[["open", "close"]].max(axis=1)).any()
        or (parsed["low"] > parsed[["open", "close"]].min(axis=1)).any()):
        raise ValueError("Impossible screenshot OHLCV values")
    if int(meta.get("row_count", -1)) != len(parsed):
        raise ValueError("CSV row count changed after manual approval")
    return parsed.sort_values("date").reset_index(drop=True)


def verify_against_independent_psx(supplemental: pd.DataFrame, reference_path: Path) -> int:
    """Zero human clicks when EVERY OCR candle matches a separate PSX source.

    This directory is created exclusively by incremental_collector.get_bars()
    and direct bulk market updates, NEVER from a screenshot-backed main CSV.
    Structural self-consistency or a second Ollama pass is not enough.
    """
    if not reference_path.is_file():
        raise ValueError("No independent PSX historical reference for automatic approval")
    reference_raw = pd.read_csv(reference_path)
    reference = normalize(reference_raw)
    if len(reference) != len(reference_raw) or reference["date"].duplicated().any():
        raise ValueError("Independent reference contains invalid or duplicate sessions")
    left = supplemental.copy()
    left["date"] = pd.to_datetime(left["date"], errors="raise")
    right = reference.set_index("date")
    if not left["date"].isin(reference["date"]).all():
        raise ValueError("Independent PSX reference does not cover every OCR date")
    if left["date"].duplicated().any():
        raise ValueError("OCR dates duplicated")
    for row in left.itertuples(index=False):
        expected = right.loc[row.date]
        # An exact volume check and one paisa price tolerance account only
        # for decimal conversion, not approximate visual similarity.
        if any(abs(float(getattr(row, k)) - float(expected[k])) > 0.011
               for k in ("open", "high", "low", "close")):
            raise ValueError("OCR price differs from independently retrieved PSX history")
        if abs(int(row.volume) - int(expected["volume"])) > 0:
            raise ValueError("OCR volume differs from independently retrieved PSX history")
    return len(left)


def merge_trusted_and_ocr(existing: pd.DataFrame | None, supplemental: pd.DataFrame):
    """Trusted PSX series wins by date; do not invent missing candles."""
    supplemental = supplemental.copy()
    supplemental["date"] = pd.to_datetime(supplemental["date"], errors="raise")
    if existing is not None and not existing.empty:
        trusted = existing.copy()
        trusted["date"] = pd.to_datetime(trusted["date"])
        trusted_dates = set(trusted["date"].dt.date)
        overlap = supplemental[supplemental["date"].dt.date.isin(trusted_dates)]
        conflicts = 0
        for _, row in overlap.iterrows():
            source = trusted.loc[trusted["date"] == row["date"]].iloc[-1]
            if any(abs(float(source[k]) - float(row[k])) > (1 if k == "volume" else 0.06)
                   for k in ("open", "high", "low", "close", "volume")):
                conflicts += 1
        new_rows = supplemental[~supplemental["date"].dt.date.isin(trusted_dates)].copy()
        merged = pd.concat([trusted, new_rows], ignore_index=True)
    else:
        conflicts = 0
        new_rows = supplemental
        merged = new_rows.copy()
    merged = merged.sort_values("date").reset_index(drop=True)
    # When fewer than 80 bars exist, retain the partial CSV but do not pass
    # it to the stock verdict analyzer.
    if len(merged) >= 80:
        valid = normalize(merged)
        if len(valid) != len(merged):
            raise ValueError("Overlay has invalid OHLCV bars")
        merged = valid
    return merged, new_rows["date"].dt.strftime("%Y-%m-%d").tolist(), conflicts


def import_one(data_dir: Path, symbol: str, csv_data: bytes, review_json: bytes,
               trust_broker_screenshots: bool = False) -> dict:
    meta = json.loads(review_json)
    manual_reviewed = meta.get("reviewed") is True
    supplemental = validate_reviewed_pair(
        csv_data, review_json, symbol, require_review=manual_reviewed)
    verification = "manually_reviewed"
    independent_count = 0
    if not manual_reviewed:
        if trust_broker_screenshots and meta.get("source") == "ollama_cloud_vision_from_broker_screenshots":
            if len(supplemental) < 80:
                raise ValueError("Need at least 80 broker OCR sessions for unattended research")
            gaps = supplemental["date"].diff().dt.days.dropna()
            if (gaps > 10).any():
                raise ValueError("Missing screenshot history: large calendar-date gap")
            if (date.today() - supplemental["date"].max().date()).days > 10:
                raise ValueError("Latest screenshot OCR price is stale")
            verification = "owner_trusted_broker_ocr_research_only"
        else:
            independent_count = verify_against_independent_psx(
                supplemental, data_dir / "independent_psx" / (symbol + ".csv"))
            verification = "auto_verified_independent_psx"
    existing_path = data_dir / (symbol + ".csv")
    existing = None
    if existing_path.exists():
        try:
            existing = pd.read_csv(existing_path)
            # An existing incomplete CSV can come only from the prior approved OCR process.
            if set(existing.columns) != set(("date", "open", "high", "low", "close", "volume")):
                raise ValueError("Unsupported cached history columns")
        except (ValueError, OSError):
            raise ValueError("Existing cached series is invalid")
    merged, new_dates, conflicts = merge_trusted_and_ocr(existing, supplemental)
    if verification == "owner_trusted_broker_ocr_research_only" and conflicts:
        raise ValueError("Broker OCR differs from existing market history")
    # Never let OCR-only partial data masquerade as complete PSX history.
    out = existing_path if len(merged) >= 80 else data_dir / "ocr_partial" / (symbol + ".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(merged.to_csv(index=False), encoding="utf-8")
    return {"symbol": symbol, "merged_rows": len(merged), "ocr_new_dates": new_dates,
            "conflicts_with_existing": conflicts, "ready_for_research": len(merged) >= 80,
            "verification": verification, "independently_matched_rows": independent_count}


def best_matching_files(items: list[dict], watch: set[str]) -> dict[str, dict[str, dict]]:
    selected = {}
    for item in items:
        name = item.get("name", "")
        if not isinstance(name, str):
            continue
        suffix = None
        if name.endswith(".vision.review.json"):
            suffix = ".vision.review.json"
        elif name.endswith(".vision.csv"):
            suffix = ".vision.csv"
        if suffix is None:
            continue
        symbol = name[:-len(suffix)]
        if symbol not in watch or item.get("mimeType") not in (
            "text/csv", "application/json", "application/octet-stream",
            "application/vnd.ms-excel"):
            continue
        group = selected.setdefault(symbol, {})
        current = group.get(suffix)
        if not current or current.get("modifiedTime", "") < item.get("modifiedTime", ""):
            group[suffix] = item
    return selected


def import_all(session, folder_id: str, data_dir: Path, watch: set[str],
               trust_broker_screenshots: bool = False) -> dict:
    found = best_matching_files(list_drive_files(session, folder_id), watch)
    state_path = data_dir / "ocr_sources.json"
    try:
        states = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(states, dict):
            states = {}
    except (OSError, ValueError):
        states = {}
    results, skipped = [], {}
    for symbol in sorted(found):
        entry = found[symbol]
        if ".vision.csv" not in entry or ".vision.review.json" not in entry:
            skipped[symbol] = "Needs both CSV and review metadata"
            continue
        try:
            csv_data = download_csv(session, entry[".vision.csv"])
            meta_data = download_csv(session, entry[".vision.review.json"])
            result = import_one(data_dir, symbol, csv_data, meta_data,
                                trust_broker_screenshots=trust_broker_screenshots)
            prev = states.get(symbol, {})
            prev_dates = set(prev.get("ocr_dates", [])) if isinstance(prev, dict) else set()
            prev_dates.update(result["ocr_new_dates"])
            states[symbol] = {
                "ocr_dates": sorted(prev_dates),
                "conflicts": result["conflicts_with_existing"],
                "source": ("owner_trusted_broker_ocr" if result["verification"] ==
                           "owner_trusted_broker_ocr_research_only" else
                           "verified_vision_screenshot_ocr"),
                "reviewed": result["verification"] == "manually_reviewed",
                "verification": result["verification"],
                "independently_matched_rows": result["independently_matched_rows"],
            }
            results.append(result)
            print(f"{symbol}: Drive supplement {result['merged_rows']} history rows, "
                  f"{len(result['ocr_new_dates'])} new, "
                  f"{result['conflicts_with_existing']} conflicting existing sessions, "
                  f"verification={result['verification']}")
        except Exception as exc:
            skipped[symbol] = type(exc).__name__
            print(f"{symbol}: image-based supplement SKIPPED ({type(exc).__name__})")
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(states, indent=2), encoding="utf-8")
    # Clear "bootstrap pending" only when a complete validated CSV exists.
    error_file = data_dir / "fetch_errors.json"
    if error_file.exists():
        errors = json.loads(error_file.read_text(encoding="utf-8"))
        for r in results:
            if r["ready_for_research"]:
                errors.pop(r["symbol"], None)
        error_file.write_text(json.dumps(errors, indent=2), encoding="utf-8")
    summary = {"imported": results, "skipped": skipped}
    (data_dir / "ocr_import_summary.json").write_text(json.dumps(summary, indent=2),
                                                     encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="watchlist.txt")
    parser.add_argument("--output", default="data")
    args = parser.parse_args()
    folder = os.environ.get("GDRIVE_FOLDER_ID", "")
    json_secret = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "")
    if not json_secret or not folder:
        raise SystemExit("Google Drive service-account reader not configured")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    try:
        credentials = Credentials.from_service_account_info(
            json.loads(json_secret),
            scopes=["https://www.googleapis.com/auth/drive.readonly"])
        reader = AuthorizedSession(credentials)
        summary = import_all(reader, folder, Path(args.output),
                             set(read_watchlist(args.watchlist)),
                             trust_broker_screenshots=os.environ.get(
                                 'TRUST_BROKER_SCREENSHOT_OCR', '').lower() == 'true')
        print(f"Drive OCR supplement complete: {len(summary['imported'])} symbol(s) updated, "
              f"{len(summary['skipped'])} rejected or pending approval")
    except Exception as exc:
        raise SystemExit("Read-only Drive supplement failed: " + type(exc).__name__) from None
    # Failed/absent Drive OCR should never corrupt already downloaded public candles.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
