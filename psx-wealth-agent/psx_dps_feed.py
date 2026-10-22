"""Free PSX daily OHLCV from documented psx-dps monthly history.

Uses the public PSX historical month view through an unofficial, read-only
library. Never attempts broker access, anti-bot evasion or order placement.

Why monthly: psx_dps.eod() does NOT expose high/low, whereas
history_by_month() includes complete high/low. Supertrend requires those.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from agent import DEFAULT_SYMBOLS, MIN_BARS, normalize

PKT = ZoneInfo("Asia/Karachi")
NAME = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


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
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()
    from psx_dps import Client

    now = datetime.now(PKT)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    with Client(min_interval=2.0, daily_budget=200, timeout=20.0, retries=1) as client:
        for symbol in dict.fromkeys(s.upper().strip() for s in args.symbols):
            try:
                bars = get_bars(client, symbol, now, months=args.months)
                # No private holdings; temporary market-only data.
                (out / f"{symbol}.csv").write_text(
                    bars.to_csv(index=False), encoding="utf-8")
                print(f"{symbol}: validated {len(bars)} daily OHLCV bars, "
                      f"latest {bars['date'].iloc[-1].date()}")
            except Exception as exc:
                # No proxy, bypass or aggressive retry on transport errors.
                print(f"{symbol}: unavailable ({type(exc).__name__})")
                errors.append(symbol)
    if errors:
        print("Data incomplete: " + ", ".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
