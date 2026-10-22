"""Free PSX daily OHLCV from documented psx-dps monthly history.

Uses the public PSX historical month view through an unofficial, read-only
library. Never attempts broker access, anti-bot evasion or order placement.

Why monthly: psx_dps.eod() does NOT expose high/low, whereas
history_by_month() includes complete high/low. Supertrend requires those.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from agent import DEFAULT_SYMBOLS, MIN_BARS, normalize

PKT = ZoneInfo("Asia/Karachi")
NAME = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
MAX_WATCHLIST = 36


def read_watchlist(path: str, max_symbols: int = MAX_WATCHLIST) -> list[str]:
    """A simple GitHub-editable watchlist; no portfolio information."""
    source = Path(path)
    if not source.is_file():
        raise ValueError("Watchlist file not found")
    if source.stat().st_size > 8192:
        raise ValueError("Watchlist file exceeds 8 KiB")
    symbols: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
        name = raw.split("#", 1)[0].strip().upper()
        if not name:
            continue
        if not NAME.fullmatch(name):
            raise ValueError(f"Invalid PSX symbol at watchlist line {line_number}")
        if name in seen:
            continue
        seen.add(name)
        symbols.append(name)
        if len(symbols) > max_symbols:
            raise ValueError(
                f"Watchlist has more than {max_symbols} unique tickers; "
                "reduce the list or arrange another permitted data source"
            )
    if not symbols:
        raise ValueError("Watchlist contains no valid ticker symbols")
    return symbols


def month_back(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 - offset
    return absolute // 12, absolute % 12 + 1


def to_bar(row: dict):
    fields = {str(k).strip().lower(): v for k, v in row.items()}
    raw_date = fields.get("time", fields.get("date"))
    timestamp = pd.to_datetime(raw_date, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError("Unparseable date in monthly PSX market table")
    values = {}
    for column in ("open", "high", "low", "close", "volume"):
        text = str(fields.get(column, "")).replace(",", "").strip()
        values[column] = float(text)
    return {"date": timestamp.strftime("%Y-%m-%d"), **values}


def get_bars(client, symbol: str, now: datetime, months: int = 6):
    if not NAME.fullmatch(symbol):
        raise ValueError("Invalid ticker")
    if months < 5 or months > 12:
        raise ValueError("Request 5–12 recent calendar months")
    raw_rows = []
    for offset in reversed(range(months)):
        year, month = month_back(now.year, now.month, offset)
        # Closed months may not exist for newer IPOs; keep partial months.
        try:
            month_rows = client.history_by_month(symbol, month, year)
        except Exception as exc:
            # Never silently ignore technical transport failures.
            if type(exc).__name__ == "NoData":
                continue
            raise
        raw_rows.extend(to_bar(row) for row in month_rows)
    if len(raw_rows) < MIN_BARS:
        raise ValueError("Insufficient monthly candles before cleaning")
    if len(raw_rows) != len(set(r["date"] for r in raw_rows)):
        raise ValueError("Duplicate sessions across historical months")
    clean = normalize(pd.DataFrame(raw_rows))
    if len(clean) < max(MIN_BARS, int(0.98 * len(raw_rows))):
        raise ValueError("Excessive malformed/invalid OHLCV candles")
    if (now.date() - clean["date"].iloc[-1].date()).days > 7:
        raise ValueError("Stale most recent monthly OHLCV bar")
    return clean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--watchlist", default=None, help="GitHub-editable ticker list, one per line")
    parser.add_argument("--months", type=int, default=5)
    args = parser.parse_args()
    if args.months < 5 or args.months > 12:
        parser.error("--months must be between 5 and 12")
    try:
        symbols = (read_watchlist(args.watchlist) if args.watchlist
                   else list(dict.fromkeys(s.upper().strip() for s in args.symbols)))
    except ValueError as exc:
        parser.error(str(exc))
    if len(symbols) > MAX_WATCHLIST:
        parser.error(f"Maximum {MAX_WATCHLIST} symbols per run")
    if len(symbols) * args.months > 190:
        parser.error("Watchlist exceeds safe request budget for selected history window")
    from psx_dps import Client

    now = datetime.now(PKT)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    errors = {}
    succeeded = 0
    print(f"Research watchlist: {len(symbols)} PSX tickers, "
          f"up to {len(symbols) * args.months} historical month requests")
    with Client(min_interval=2.0, daily_budget=200, timeout=20.0, retries=1) as client:
        for symbol in symbols:
            try:
                bars = get_bars(client, symbol, now, months=args.months)
                # No private holdings; temporary market-only data.
                (out / f"{symbol}.csv").write_text(
                    bars.to_csv(index=False), encoding="utf-8")
                succeeded += 1
                print(f"{symbol}: validated {len(bars)} daily OHLCV bars, "
                      f"latest {bars['date'].iloc[-1].date()}")
            except Exception as exc:
                # No proxy, bypass or aggressive retry on transport errors.
                print(f"{symbol}: unavailable ({type(exc).__name__})")
                errors[symbol] = type(exc).__name__
    # Keep the complete watchlist's missing coverage visible in the report.
    (out / "fetch_errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    if errors:
        print("Unavailable symbols (not analyzed): " + ", ".join(errors))
    print(f"Coverage: {succeeded}/{len(symbols)} symbols")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
