"""Single authorized public-history bulk response probe; prints no private data."""
from datetime import date
from psx_dps import Client

with Client(min_interval=3.0, daily_budget=10, timeout=15, retries=1) as client:
    try:
        bars = client.history_by_date("2026-10-21")
        print("ROWS:", len(bars))
        print("FIELDS:", sorted(bars[0].keys()) if bars else [])
        matching = [b for b in bars if str(b.get("symbol","")).upper() in {"SAZEW","LUCK","OGDC"}]
        print("SAMPLE:", matching[:2])
    except Exception as ex:
        print("BULK PROBE UNAVAILABLE:", type(ex).__name__)
