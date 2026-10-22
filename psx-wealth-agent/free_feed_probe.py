"""One-off, read-only compatibility probe for no-key PSX history libraries.

No credentials, no order endpoints, no proxy rotation, no scraped content
committed to the public repository. At most one ticker is probed.
This is diagnostic only. It does not issue trading alerts.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SYMBOL = "SAZEW"
PKT = ZoneInfo("Asia/Karachi")


def test_psxdata():
    try:
        import psxdata

        # Restrict to one ticker and a reasonable historical period.
        raw = psxdata.stocks(SYMBOL, start="2026-06-01", end="2026-10-21")
        cols = [str(col).lower() for col in raw.columns]
        required = {"date", "open", "high", "low", "close", "volume"}
        if "date" not in cols and getattr(raw.index, "name", None) == "date":
            cols.append("date")
        return {
            "success": not raw.empty,
            "rows": len(raw),
            "fields": sorted(set(cols) & required),
            "complete_ohlcv": required.issubset(cols),
            "error_type": None,
        }
    except Exception as err:
        return {"success": False, "error_type": type(err).__name__, "rows": 0,
                "complete_ohlcv": False}


def test_psx_dps():
    try:
        from psx_dps import Client

        with Client(min_interval=2.0, daily_budget=40, timeout=15, retries=1) as psx:
            data = psx.eod(SYMBOL, since="2026-06-01")
            if not isinstance(data, list):
                raise ValueError("EOD response must be list")
            eod_fields = set(data[0]) if data else set()
            # EOD series doesn't contain high/low: explicitly test monthly route.
            monthly = psx.history_by_month(SYMBOL, 9, 2026)
            monthly_fields = sorted({str(k).strip().lower() for d in monthly
                                      for k in d.keys()}) if monthly else []
            return {
                "success": bool(data) and bool(monthly),
                "rows": len(data),
                "eod_fields": sorted(eod_fields),
                "monthly_rows": len(monthly),
                "monthly_fields": monthly_fields,
                "complete_ohlcv": all(
                    k in eod_fields for k in ("date", "open", "close", "volume")
                ) and {"high", "low"}.issubset(set(monthly_fields)),
                "error_type": None,
            }
    except Exception as err:
        return {"success": False, "error_type": type(err).__name__,
                "rows": 0, "complete_ohlcv": False}


def main():
    results = {
        "tested_at_pakistan": datetime.now(PKT).isoformat(),
        "ticker": SYMBOL,
        "psxdata": test_psxdata(),
        "psx_dps": test_psx_dps(),
        "note": "Unlicensed/unofficial public-price sources; no executable trade alerts.",
    }
    Path("reports").mkdir(exist_ok=True)
    Path("reports/free-feed-probe.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))
    # Missing data is a *diagnostic* result, not a workflow failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
