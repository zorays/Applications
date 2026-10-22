"""Authorized Capital Stake historical PSX OHLCV to local CSV.

Requires a dedicated Capital Stake developer API Bearer token. Never use the
Capital Analytics browser session token or broker credentials.
Docs: https://www.capitalstake.com/docs/rest/api/stocks/market-quotes-and-trading/charts-ohlc/
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from agent import DEFAULT_SYMBOLS, normalize
from psx_dps_feed import read_watchlist

API_ROOT = "https://csapis.com/3.0/market"
PKT = ZoneInfo("Asia/Karachi")
VALID_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
MAX_BYTES = 5_000_000
MIN_BARS = 80


def parse_bars(payload: dict) -> pd.DataFrame:
    """Parse documented {status:ok,data:[{time,open,high,low,close,volume}]}."""
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("Provider returned non-success status")
    bars = payload.get("data")
    if not isinstance(bars, list):
        raise ValueError("Provider response does not contain OHLCV list")
    frame = pd.DataFrame(bars)
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError("Provider response missing required candle fields")
    times = pd.to_datetime(
        pd.to_numeric(frame["time"], errors="coerce"), unit="s",
        errors="coerce", utc=True,
    )
    if times.isna().any():
        raise ValueError("Invalid Unix timestamps in historical OHLCV")
    # Provider describes its Unix seconds as UTC; project market dates into PKT.
    frame["date"] = times.dt.tz_convert(PKT).dt.strftime("%Y-%m-%d")
    # If a vendor ever supplies multiple intraday points per calendar date,
    # do not invent a single EOD candle by dropping duplicates.
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate market sessions in supposed EOD response")
    output = frame.loc[:, ["date", "open", "high", "low", "close", "volume"]]
    return normalize(output)


def fetch_bars(session: requests.Session, token: str, symbol: str,
               date_from: str, date_to: str, adjusted: bool = False) -> pd.DataFrame:
    if not VALID_SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid stock symbol")
    path = "eod-adj" if adjusted else "eod"
    response = session.get(
        f"{API_ROOT}/{path}",
        headers={"Authorization": "Bearer " + token},
        params={"symbol": symbol, "date_from": date_from, "date_to": date_to},
        timeout=35,
    )
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError("Provider payload too large")
    return parse_bars(response.json())


def main() -> int:
    parser = argparse.ArgumentParser(description="Import licensed Capital Stake PSX candles")
    parser.add_argument("--output", default="data")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--watchlist", default=None)
    parser.add_argument("--lookback-days", type=int, default=550)
    parser.add_argument("--adjusted", action="store_true",
                        help="Dividend/split adjusted candles for backtests, not executable stops")
    args = parser.parse_args()
    try:
        symbols = read_watchlist(args.watchlist) if args.watchlist else args.symbols
    except ValueError as exc:
        parser.error(str(exc))
    if not 180 <= args.lookback_days <= 2500:
        parser.error("lookback-days must be 180–2500")
    token = os.getenv("CAPITALSTAKE_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("No dedicated licensed Capital Stake API token configured")
    now = datetime.now(PKT).date()
    start = (now - timedelta(days=args.lookback_days)).isoformat()
    end = now.isoformat()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    saved = []
    errors = []
    with requests.Session() as session:
        for symbol in dict.fromkeys(s.upper().strip() for s in symbols):
            try:
                bars = fetch_bars(session, token, symbol, start, end, adjusted=args.adjusted)
                (output / (symbol + ".csv")).write_text(
                    bars.to_csv(index=False), encoding="utf-8"
                )
                saved.append((symbol, len(bars)))
            except Exception as exc:
                # Never show HTTP response bodies, request headers or bearer tokens.
                errors.append((symbol, type(exc).__name__))
    print("Imported EOD OHLCV: " + ", ".join(f"{s}({n})" for s, n in saved))
    for symbol, error in errors:
        print(f"{symbol}: unavailable ({error})")
    if not saved:
        raise SystemExit("No valid EOD bars returned; verify API entitlement")
    # Mark incomplete symbols as a failed scheduled run. Never silently treat
    # partial watchlist data as complete.
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
