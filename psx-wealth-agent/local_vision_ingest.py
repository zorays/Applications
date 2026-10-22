"""Local Windows: Ollama vision screenshots -> cautious OHLCV CSV -> Google Drive.

An OCR CSV is *always unreviewed* after new or changed screenshots.
Use the explicit 'approve' command only after comparing EVERY extracted candle
against the broker's OHLC History table. Github will import reviewed CSVs as
supplementary research only, and will not issue buy/sell email signals based
on screenshot-derived candles.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

PKT = ZoneInfo("Asia/Karachi")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
CSV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
MAX_IMAGE_BYTES = 12_000_000
DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]
DEFAULT_FOLDER = "1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_atomic(path: Path, contents: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(contents)
    temp.replace(path)


def csv_bytes(rows: list[dict]) -> bytes:
    import io
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for item in rows:
        writer.writerow(item)
    return buf.getvalue().encode("utf-8")


def parse_price(value, integer=False):
    if isinstance(value, bool):
        raise ValueError("Boolean in numeric candle")
    s = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        raise ValueError("Ambiguous OHLCV number")
    number = float(s)
    if number < 0 or (integer and not number.is_integer()):
        raise ValueError("Invalid OHLCV magnitude")
    return int(number) if integer else number


def validate_image_rows(records, today=None) -> list[dict]:
    """Allow partial screenshot snippets; NEVER pad missing candles."""
    if not isinstance(records, list) or not (1 <= len(records) <= 100):
        raise ValueError("No visible candlestick rows or implausibly many rows")
    today = today or datetime.now(PKT).date()
    clean = []
    seen = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid candle record")
        raw_date = str(record.get("date", "")).strip()
        try:
            parsed = pd.to_datetime(raw_date, errors="raise", dayfirst=False).date()
        except (ValueError, TypeError):
            raise ValueError("Unreadable date in screenshot") from None
        if parsed > today or parsed.weekday() >= 5:
            raise ValueError("Future or weekend candle from OCR")
        row = {"date": parsed.isoformat()}
        for col in ("open", "high", "low", "close"):
            row[col] = parse_price(record.get(col, ""))
        row["volume"] = parse_price(record.get("volume", ""), integer=True)
        if (min(row["open"], row["high"], row["low"], row["close"]) <= 0 or
            row["high"] < max(row["open"], row["close"]) or
            row["low"] > min(row["open"], row["close"]) or
            row["high"] < row["low"] or row["volume"] < 0):
            raise ValueError("Impossible OHLCV candle")
        prior = seen.get(row["date"])
        if prior is not None and prior != row:
            raise ValueError("Conflicting duplicated session within screenshot")
        seen[row["date"]] = row
    clean = sorted(seen.values(), key=lambda r: r["date"])
    return clean


def vision_schema():
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "symbol": text,
            "visible_rows": {"type": "array", "maxItems": 100, "items": {
                "type": "object",
                "properties": {k: text for k in CSV_COLUMNS},
                "required": CSV_COLUMNS,
                "additionalProperties": False}},
            "notes": text,
        },
        "required": ["symbol", "visible_rows", "notes"],
        "additionalProperties": False,
    }


def ask_ollama(image_path: Path, symbol: str, model: str, url: str):
    """Use ONLY the locally running Ollama service; never send broker screenshots online."""
    if url not in ("http://127.0.0.1:11434/api/chat", "http://localhost:11434/api/chat"):
        raise ValueError("Ollama endpoint must be local loopback only")
    image = image_path.read_bytes()
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot exceeds 12 MB")
    payload = {
        "model": model, "stream": False, "format": vision_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "user",
                      "content": (
                          f"Transcribe ONLY visible rows in the OHLC History grid for PSX symbol {symbol}. "
                          "Read the Date, Open, High, Low, Close, Volume columns EXACTLY as printed. "
                          "EXCLUDE the Change column, market watch prices, totals, extrema, header statistics, "
                          "minimum/maximum summaries, other symbols, and rows hidden offscreen. "
                          "Do not infer, extrapolate, approximate, or invent any date/price. "
                          "Return each numeric value as a string, including the printed decimals; "
                          "if any row is illegible OMIT it entirely. State the ticker detected. "
                          "An empty visible_rows array is acceptable if the grid is unreadable."
                      ),
                      "images": [base64.b64encode(image).decode("ascii")]}],
    }
    try:
        result = requests.post(url, json=payload, timeout=240)
        result.raise_for_status()
        response = json.loads(result.json()["message"]["content"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise RuntimeError("Local Ollama vision request failed or returned invalid JSON") from None
    if str(response.get("symbol", "")).strip().upper() != symbol:
        raise ValueError("OCR ticker does not match screenshot filename")
    return validate_image_rows(response.get("visible_rows")), str(response.get("notes", ""))[:200]


def filename_symbol(path: Path):
    symbol = path.name.split("__", 1)[0].upper()
    if not SYMBOL_RE.fullmatch(symbol) or "__" not in path.name:
        raise ValueError("Screenshot must be named SYMBOL__description.png")
    return symbol


def read_approved_symbols(watchlist: Path) -> set[str]:
    from psx_dps_feed import read_watchlist
    return set(read_watchlist(str(watchlist)))


def merge_extracts(extractions: list[dict]) -> list[dict]:
    combined = {}
    for evidence in extractions:
        for row in evidence["rows"]:
            old = combined.get(row["date"])
            if old is not None and old != row:
                raise ValueError("Conflicting candles across screenshots; resolve before uploading")
            combined[row["date"]] = row
    return [combined[day] for day in sorted(combined)]


def process_images(images_dir: Path, out_dir: Path, watchlist: Path,
                   model: str, url: str):
    allowed = read_approved_symbols(watchlist)
    evidence_dir = out_dir / "extractions"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    updated = set()
    failures = []
    images = sorted(p for p in images_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    for image in images:
        try:
            symbol = filename_symbol(image)
            if symbol not in allowed:
                raise ValueError("Ticker not found in GitHub watchlist")
            sha = digest(image.read_bytes())
            evidence_path = evidence_dir / (symbol + "__" + sha + ".json")
            if not evidence_path.exists():
                rows, notes = ask_ollama(image, symbol, model, url)
                evidence = {"symbol": symbol, "sha256_image": sha,
                            "image_name": image.name, "rows": rows, "notes": notes}
                save_atomic(evidence_path, json.dumps(evidence, indent=2).encode())
                print(f"{image.name}: extracted {len(rows)} *visible* candles")
            updated.add(symbol)
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append(f"{image.name}: {type(exc).__name__} — {exc}")
    built = []
    for symbol in sorted(updated):
        try:
            items = [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted(evidence_dir.glob(symbol + "__*.json"))]
            merged = merge_extracts(items)
            data = csv_bytes(merged)
            path = out_dir / (symbol + ".csv")
            old_quality = out_dir / (symbol + ".review.json")
            current = {}
            if old_quality.is_file():
                try:
                    current = json.loads(old_quality.read_text(encoding="utf-8"))
                except ValueError:
                    pass
            # Approval survives only for precisely the same CSV byte content.
            approved = (current.get("reviewed") is True and
                        current.get("csv_sha256") == digest(data))
            quality = {
                "symbol": symbol, "source": "ollama_vision_from_broker_screenshots",
                "csv_sha256": digest(data), "row_count": len(merged),
                "reviewed": approved,
                "reviewed_at": current.get("reviewed_at") if approved else None,
                "image_hashes": sorted(x["sha256_image"] for x in items),
            }
            save_atomic(path, data)
            save_atomic(old_quality, json.dumps(quality, indent=2).encode())
            built.append(symbol)
            print(f"{symbol}: consolidated {len(merged)} sessions; "
                  f"human_verified={approved}")
        except (ValueError, OSError) as exc:
            failures.append(f"{symbol}: {type(exc).__name__} — {exc}")
    return built, failures


def approve(out_dir: Path, symbol: str, watchlist: Path):
    if symbol not in read_approved_symbols(watchlist):
        raise ValueError("Ticker not in watchlist")
    path = out_dir / (symbol + ".csv")
    metadata_path = out_dir / (symbol + ".review.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["csv_sha256"] != digest(path.read_bytes()):
        raise ValueError("CSV was modified; rerun extraction and inspect before approval")
    # Check again for invalid rows; manual confirmation is still required.
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    validate_image_rows(rows)
    metadata["reviewed"] = True
    metadata["reviewed_at"] = datetime.now(PKT).isoformat()
    save_atomic(metadata_path, json.dumps(metadata, indent=2).encode())
    return metadata


def folder_upload_service(credentials_file: Path, token_file: Path):
    """Desktop Google OAuth: owner creates files under their own My Drive quota."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), DRIVE_SCOPE)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not credentials_file.exists():
                raise ValueError("Google Desktop OAuth client JSON missing")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), DRIVE_SCOPE)
            credentials = flow.run_local_server(port=0, open_browser=True)
        save_atomic(token_file, credentials.to_json().encode("utf-8"))
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def upload_google(files: list[Path], folder_id: str, credentials: Path, token_file: Path):
    """Replace own named files (rather than creating daily duplicates) with Drive API."""
    from googleapiclient.http import MediaFileUpload
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", folder_id):
        raise ValueError("Invalid Drive folder ID")
    api = folder_upload_service(credentials, token_file)
    for path in files:
        query = ("name = '" + path.name + "' and '" + folder_id +
                 "' in parents and trashed = false")
        existing = api.files().list(q=query, fields="files(id,name)", page_size=10).execute()
        mime = "text/csv" if path.suffix.lower() == ".csv" else "application/json"
        media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
        found = existing.get("files", [])
        if found:
            api.files().update(fileId=found[0]["id"], media_body=media,
                               fields="id").execute()
        else:
            api.files().create(body={"name": path.name, "parents": [folder_id],
                                     "mimeType": mime}, media_body=media,
                               fields="id").execute()
        print(f"Google Drive: uploaded {path.name}")


def upload_prepared(out_dir: Path, symbols: list[str], mode: str, args):
    if mode == "none" or not symbols:
        return
    files = []
    for symbol in symbols:
        for suffix in (".csv", ".review.json"):
            path = out_dir / (symbol + suffix)
            if not path.exists():
                raise ValueError("Prepared CSV/provenance metadata missing")
            files.append(path)
    if mode == "sync":
        sync = Path(args.sync_folder)
        if not sync.is_dir():
            raise ValueError("Drive for Desktop sync folder not found")
        for path in files:
            dest = sync / path.name
            save_atomic(dest, path.read_bytes())
            print(f"Drive for Desktop: staged {dest.name}")
    elif mode == "oauth":
        upload_google(files, args.drive_folder_id, Path(args.oauth_client),
                      Path(args.oauth_token))
    else:
        raise ValueError("Unsupported uploader mode")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "approve"])
    parser.add_argument("--symbol", help="Only for approve, e.g. SAZEW")
    parser.add_argument("--images", default="screenshots")
    parser.add_argument("--out", default="vision_output")
    parser.add_argument("--watchlist", default="watchlist.txt")
    parser.add_argument("--model", default="llama3.2-vision")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/chat")
    parser.add_argument("--upload", choices=["none", "oauth", "sync"], default="none")
    parser.add_argument("--sync-folder", default="")
    parser.add_argument("--drive-folder-id", default=DEFAULT_FOLDER)
    parser.add_argument("--oauth-client", default=".private/google_desktop_client.json")
    parser.add_argument("--oauth-token", default=".private/google_drive_token.json")
    args = parser.parse_args()
    out = Path(args.out)
    if args.command == "scan":
        images = Path(args.images)
        if not images.is_dir():
            parser.error("screenshots folder does not exist")
        symbols, errors = process_images(images, out, Path(args.watchlist),
                                         args.model, args.ollama_url)
        for msg in errors:
            print("REVIEW ERROR:", msg, file=sys.stderr)
        try:
            upload_prepared(out, symbols, args.upload, args)
        except Exception as exc:
            print("Upload failed: " + type(exc).__name__, file=sys.stderr)
            return 1
        return 1 if errors else 0
    symbol = (args.symbol or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        parser.error("--symbol required (valid PSX ticker)")
    try:
        review = approve(out, symbol, Path(args.watchlist))
        upload_prepared(out, [symbol], args.upload, args)
        print(f"{symbol}: marked reviewed; {review['row_count']} screenshot sessions")
        return 0
    except Exception as exc:
        print("Review/upload failed: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
