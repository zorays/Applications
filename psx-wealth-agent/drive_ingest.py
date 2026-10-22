"""Read approved market-only OHLCV CSV exports from a private Google Drive folder.

Requires a Google service account granted read access to the specific inbox folder.
Does not use EClear credentials, trade endpoints or undocumented broker interfaces.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
from pathlib import Path

import pandas as pd

from agent import DEFAULT_SYMBOLS, normalize
from psx_dps_feed import read_watchlist

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FILES_API = "https://www.googleapis.com/drive/v3/files"
MAX_CSV_BYTES = 5_000_000
MAX_LIST_PAGES = 10


def choose_files(files: list[dict], allowed: set[str]) -> dict[str, dict]:
    """Select latest raw CSV per exact watchlist ticker; reject other file types."""
    selected = {}
    for item in files:
        name = item.get("name", "")
        if not re.fullmatch(r"[A-Z0-9]{2,12}\.csv", name):
            continue
        symbol = name[:-4]
        if symbol not in allowed:
            continue
        if item.get("mimeType") not in (
            "text/csv", "application/vnd.ms-excel", "application/octet-stream"
        ):
            continue
        if symbol not in selected or item.get("modifiedTime", "") > selected[symbol].get("modifiedTime", ""):
            selected[symbol] = item
    return selected


def list_drive_files(session, folder_id: str) -> list[dict]:
    """Read metadata only, limit pagination to avoid runaway scans."""
    items: list[dict] = []
    token = None
    for _ in range(MAX_LIST_PAGES):
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "pageSize": "1000",
        }
        if token:
            params["pageToken"] = token
        response = session.get(FILES_API, params=params, timeout=25)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("files", []))
        token = payload.get("nextPageToken")
        if not token:
            break
    if token:
        raise RuntimeError("Drive folder scan exceeded pagination limit")
    return items


def download_csv(session, item: dict) -> bytes:
    if int(item.get("size") or 0) > MAX_CSV_BYTES:
        raise ValueError("CSV file exceeds approved size limit")
    file_id = item["id"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{5,256}", file_id):
        raise ValueError("Invalid Drive file identifier")
    response = session.get(f"{FILES_API}/{file_id}", params={"alt": "media"}, timeout=35)
    response.raise_for_status()
    if len(response.content) > MAX_CSV_BYTES:
        raise ValueError("CSV downloaded exceeds size limit")
    return response.content


def validate_csv(content: bytes):
    try:
        frame = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    except (UnicodeError, pd.errors.ParserError) as exc:
        raise ValueError("Data is not UTF-8 CSV") from exc
    # Reject accidental account reports or formats missing price history columns.
    normalize(frame)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--watchlist", default=None)
    args = parser.parse_args()
    try:
        allowed = set(read_watchlist(args.watchlist) if args.watchlist else args.symbols)
    except ValueError as exc:
        parser.error(str(exc))
    folder = os.getenv("GDRIVE_FOLDER_ID", "").strip()
    credential_json = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON", "")
    if not folder or not credential_json:
        raise SystemExit("Google Drive API not configured (missing GitHub secrets)")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{5,256}", folder):
        raise SystemExit("Malformed Drive folder identifier")

    # Import lazily so offline unit tests do not require Google auth.
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    try:
        info = json.loads(credential_json)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[DRIVE_SCOPE]
        )
        session = AuthorizedSession(credentials)
        remote = list_drive_files(session, folder)
        matches = choose_files(remote, allowed)
        if not matches:
            raise ValueError("No named market OHLCV CSV files found in inbox")
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        saved = []
        for symbol in sorted(matches):
            try:
                content = download_csv(session, matches[symbol])
                validate_csv(content)
                path = output / (symbol + ".csv")
                path.write_bytes(content)
                saved.append(symbol)
            except Exception as exc:
                # No credentials, file IDs or bearer URLs in CI logs.
                print(f"{symbol}: skipped invalid CSV ({type(exc).__name__})")
        if not saved:
            raise ValueError("No valid market OHLCV data available")
        print("Market-only exports imported: " + ", ".join(saved))
        return 0
    except Exception as exc:
        # Avoid accidentally printing service credential values/API URLs.
        raise SystemExit("Drive import failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    raise SystemExit(main())
