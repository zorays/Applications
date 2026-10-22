"""Incremental PSX EOD collector with one whole-market daily request.

Bootstraps a *small* number of historical tickers per run, then maintains
previously validated full OHLCV series using one PSX history_by_date() request.
Market-only CSV history is persisted via GitHub Actions cache. No account
information, broker session, bypass, or automated trade execution is involved.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from agent import MIN_BARS, normalize
from psx_dps_feed import get_bars, read_watchlist, to_bar

PKT = ZoneInfo("Asia/Karachi")
MAX_ROWS = 300
PRIORITY = ["SAZEW", "LUCK", "SYS", "OGDC", "FFC", "MEBL", "ENGROH"]


def daily_rows_to_bars(rows: list[dict], day: date, symbols: set[str]) -> dict[str, dict]:
    """Check the whole-market trading-session table before trusting its candles."""
    if not isinstance(rows, list) or len(rows) < 30:
        raise ValueError("Bulk PSX daily history is unavailable or suspiciously incomplete")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        if symbol not in symbols:
            continue
        try:
            bar = to_bar({**row, "date": day.isoformat()})
            # Inadequate high/low or no trade means no trustworthy daily OHLCV.
            if (bar["open"] <= 0 or bar["high"] <= 0 or bar["low"] <= 0
                or bar["close"] <= 0 or bar["volume"] <= 0
                or bar["high"] < max(bar["open"], bar["close"])
                or bar["low"] > min(bar["open"], bar["close"])
                or bar["low"] > bar["high"]):
                continue
            result[symbol] = bar
        except (TypeError, ValueError):
            continue
    return result


def read_existing(directory: Path, symbol: str, today: date):
    path = directory / (symbol + ".csv")
    if not path.exists():
        return None
    try:
        bars = normalize(pd.read_csv(path))
        if bars["date"].iloc[-1].date() > today:
            return None
        return bars
    except (OSError, ValueError, KeyError):
        return None


def append_session(existing: pd.DataFrame, bar: dict) -> pd.DataFrame:
    combined = pd.concat((existing, pd.DataFrame([bar])), ignore_index=True)
    # normalize converts mixed CSV string / pandas Timestamp dates before sorting.
    return normalize(combined).tail(MAX_ROWS).reset_index(drop=True)


def load_state(directory: Path):
    path = directory / "collection_state.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_text(path: Path, text: str):
    draft = path.with_suffix(path.suffix + ".tmp")
    draft.write_text(text, encoding="utf-8")
    draft.replace(path)


def collect(client, directory: Path, watchlist: list[str], now: datetime,
            bootstrap_limit: int = 4, months: int = 5):
    """Collect modestly; obey upstream cooldown without repeated failed calls.

    Returns a summary. A lack of history never turns into a fabricated signal.
    """
    directory.mkdir(parents=True, exist_ok=True)
    today = now.date()
    cached = {symbol: read_existing(directory, symbol, today) for symbol in watchlist}
    state = load_state(directory)
    retry_after = state.get("retry_after") or {}
    errors: dict[str, str] = {}
    data_warnings: dict[str, str] = {}
    bulk = {}
    cooling = False
    last_bulk_success = False

    try:
        response = client.history_by_date(today.isoformat())
        bulk = daily_rows_to_bars(response, today, set(watchlist))
        last_bulk_success = True
        print(f"PSX bulk daily: {len(response)} listed rows, "
              f"{len(bulk)} valid watched OHLCV candles in ONE request")
    except Exception as exc:
        # Do not retry a source that has pushed back.
        print(f"PSX bulk update not available: {type(exc).__name__}")
        cooling = type(exc).__name__ == "CoolingDown"
        status = getattr(getattr(client, "transport", None), "breaker", None)
        if status is not None:
            try:
                cooling = cooling or bool(status.status().get("cooling_down"))
            except Exception:
                pass

    # Apply whole-market daily data to all already-initialized securities.
    for symbol, bars in cached.items():
        if bars is None or len(bars) < MIN_BARS:
            continue
        if symbol in bulk:
            try:
                bars = append_session(bars, bulk[symbol])
            except (ValueError, TypeError):
                data_warnings[symbol] = "Daily candle failed validation; retaining prior history"
        cached[symbol] = bars
        atomic_text(directory / (symbol + ".csv"), bars.to_csv(index=False))

    # Historical backfills are *only* for symbols without an initial window.
    initial = [s for s in watchlist if cached[s] is None or len(cached[s]) < MIN_BARS]
    queue = [s for s in PRIORITY if s in initial] + [s for s in initial if s not in PRIORITY]
    tried = 0
    bootstrapped = []
    deferred = []

    for symbol in queue:
        next_day = retry_after.get(symbol)
        if next_day and next_day > today.isoformat():
            errors[symbol] = f"Bootstrap deferred until {next_day}; insufficient valid history"
            deferred.append(symbol)
            continue
        if cooling or tried >= bootstrap_limit:
            errors[symbol] = "Bootstrap pending; source cooldown or bounded daily request budget"
            deferred.append(symbol)
            continue
        tried += 1
        try:
            frame = get_bars(client, symbol, now, months=months)
            # Merge validated bulk day if it is newer than historical-month data.
            if symbol in bulk:
                frame = append_session(frame, bulk[symbol])
            frame = frame.tail(MAX_ROWS).reset_index(drop=True)
            atomic_text(directory / (symbol + ".csv"), frame.to_csv(index=False))
            cached[symbol] = frame
            bootstrapped.append(symbol)
            retry_after.pop(symbol, None)
        except Exception as exc:
            kind = type(exc).__name__
            errors[symbol] = f"Historical bootstrap unavailable: {kind}"
            # Back off for one week for bad/empty history; source transport
            # errors are treated as *stop* signals for this run.
            if kind in ("ValueError", "NoData", "UnknownSymbol"):
                retry_after[symbol] = (today + timedelta(days=7)).isoformat()
            if kind in ("CoolingDown", "TransportError", "NoHealthyNode",
                        "RateLimited", "UpstreamError"):
                cooling = True
                print(f"Stopped historical backfill after {kind}; "
                      "no additional PSX history requests this run")

    complete = {}
    for symbol in watchlist:
        bars = cached[symbol]
        if bars is not None and len(bars) >= MIN_BARS:
            complete[symbol] = bars
        else:
            errors.setdefault(symbol, "Historical OHLCV bootstrap pending")

    summary = {
        "generated_at_pakistan": now.isoformat(),
        "requested": len(watchlist),
        "completed": len(complete),
        "missing": len(watchlist) - len(complete),
        "bulk_request_succeeded": last_bulk_success,
        "bulk_watchlist_candles": len(bulk),
        "historical_bootstrap_attempts": tried,
        "historical_bootstrapped": bootstrapped,
        "deferred": deferred,
        "data_warnings": data_warnings,
        "cooldown_respected": cooling,
        "latest_dates": {s: f["date"].iloc[-1].date().isoformat()
                         for s, f in complete.items()},
    }
    atomic_text(directory / "fetch_errors.json", json.dumps(errors, indent=2))
    atomic_text(directory / "collection_state.json", json.dumps(
        {"retry_after": retry_after}, indent=2
    ))
    atomic_text(directory / "collection_summary.json", json.dumps(summary, indent=2))
    print(f"Validated OHLCV coverage {len(complete)}/{len(watchlist)}; "
          f"{tried} historical backfills attempted; cooldown={cooling}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", required=True)
    parser.add_argument("--output", default="data")
    parser.add_argument("--months", type=int, default=5)
    parser.add_argument("--bootstrap-limit", type=int, default=4)
    args = parser.parse_args()
    if not (1 <= args.bootstrap_limit <= 4) or not (5 <= args.months <= 6):
        parser.error("Bootstrap limit must be 1–4 and months must be 5–6")
    watchlist = read_watchlist(args.watchlist)
    if len(watchlist) * args.months > 216:
        parser.error("Request ceiling would exceed configured watchlist limit")
    from psx_dps import Client
    now = datetime.now(PKT)
    # Up to 4*5 + 1 requests per run, plus light source node probes.
    with Client(min_interval=3.0, daily_budget=50, timeout=20, retries=1) as client:
        summary = collect(
            client, Path(args.output), watchlist, now,
            bootstrap_limit=args.bootstrap_limit, months=args.months
        )
    # Never fail the job just because new symbols are awaiting backfill.
    # Fail if no symbol has sufficient complete OHLCV at all.
    return 0 if summary["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
