"""PSX daily-chart research engine. Not an order generator or investment advice."""
from __future__ import annotations

import argparse
import json
import math
import os
from io import StringIO
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DEFAULT_SYMBOLS = [
    "SAZEW", "LUCK", "SYS", "OGDC", "FFC", "MEBL", "ENGROH",
    "MARI", "MLCF", "FCCL", "DGKC", "ECOP", "SPEL",
]
MIN_BARS = 80


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate and standardize the library's daily OHLCV frame."""
    if raw is None or raw.empty:
        raise ValueError("empty OHLCV response")
    frame = raw.copy()
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError("missing columns: " + ", ".join(sorted(missing)))
    if "is_anomaly" in frame:
        frame = frame.loc[~frame["is_anomaly"].fillna(False).astype(bool)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(None)
    for col in required[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=required).sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.loc[
        (frame["open"] > 0) & (frame["close"] > 0)
        & (frame["low"] > 0) & (frame["high"] >= frame["low"])
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & (frame["volume"] >= 0)
    ].reset_index(drop=True)
    if len(frame) < MIN_BARS:
        raise ValueError(f"insufficient validated bars: {len(frame)}; need {MIN_BARS}")
    return frame


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.diff()
    gains = change.clip(lower=0)
    losses = (-change).clip(lower=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    output = 100 - 100 / (1 + rs)
    output = output.mask((avg_loss == 0) & (avg_gain > 0), 100)
    output = output.mask((avg_loss == 0) & (avg_gain == 0), 50)
    return output


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Return trend direction and Wilder-style ATR, using daily bars."""
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    mid = (high + low) / 2
    upper, lower = mid + multiplier * atr, mid - multiplier * atr
    fupper = upper.to_numpy().copy()
    flower = lower.to_numpy().copy()
    bullish = [False] * len(df)
    first = period - 1
    bullish[first] = bool(close.iloc[first] >= mid.iloc[first])
    for i in range(first + 1, len(df)):
        if upper.iloc[i] >= fupper[i - 1] and close.iloc[i - 1] <= fupper[i - 1]:
            fupper[i] = fupper[i - 1]
        if lower.iloc[i] <= flower[i - 1] and close.iloc[i - 1] >= flower[i - 1]:
            flower[i] = flower[i - 1]
        if bullish[i - 1]:
            bullish[i] = not (close.iloc[i] < flower[i])
        else:
            bullish[i] = bool(close.iloc[i] > fupper[i])
    return bullish[-1], float(atr.iloc[-1])


def round_or_none(value, ndigits=2):
    try:
        v = float(value)
        return round(v, ndigits) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def analyze(symbol: str, raw: pd.DataFrame, today: date | None = None) -> dict:
    df = normalize(raw)
    today = today or date.today()
    close = df["close"]
    sma20 = close.rolling(20).mean()
    ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
    ema21 = close.ewm(span=21, adjust=False, min_periods=21).mean()
    rsi14 = rsi_wilder(close)
    st_up, atr10 = supertrend(df)
    last_date = df["date"].iloc[-1].date()
    days_old = (today - last_date).days
    recent_vol = df["volume"].iloc[-21:-1].mean()
    volume_ratio = float(df["volume"].iloc[-1] / recent_vol) if recent_vol > 0 else None
    median_turnover = float((close * df["volume"]).iloc[-20:].median())
    price = float(close.iloc[-1])
    deviation = 100 * (price / float(sma20.iloc[-1]) - 1)
    rsi_value = float(rsi14.iloc[-1])

    # Technical component: exactly five observations, 3 points each.
    conditions = {
        "close_above_sma20": price > float(sma20.iloc[-1]),
        "ema10_above_ema21": float(ema10.iloc[-1]) > float(ema21.iloc[-1]),
        "supertrend_green": bool(st_up),
        "rsi_above_50": rsi_value >= 50,
        "volume_above_prior_20d_mean": volume_ratio is not None and volume_ratio >= 1.0,
    }
    trend_score = 3 * sum(conditions.values())
    if deviation > 12 and rsi_value >= 75:
        setup = "D_OVERHEATED"
    elif deviation <= -5 and not (st_up and conditions["ema10_above_ema21"]):
        setup = "C_DISCOUNT_STILL_FALLING"
    elif st_up and conditions["ema10_above_ema21"] and conditions["close_above_sma20"]:
        setup = "B_TREND_RECOVERING"
    else:
        setup = "WATCH"

    support20 = float(df["low"].iloc[-20:].min())
    resistance60 = float(df["high"].iloc[-60:].max())
    risk = price - support20
    reward = resistance60 - price
    rr = reward / risk if risk > 0 and reward > 0 else None
    flags = []
    if days_old < 0 or days_old > 7:
        flags.append(f"stale_or_future_data_{days_old}_calendar_days")
    if median_turnover < 3_000_000:
        flags.append("thin_turnover_proxy")
    if volume_ratio is None:
        flags.append("volume_comparison_unavailable")
    if rr is None or rr < 2:
        flags.append("illustrative_reward_risk_below_2_or_unavailable")

    return {
        "symbol": symbol,
        "last_bar": last_date.isoformat(),
        "close_pkr": round_or_none(price),
        "sma20": round_or_none(sma20.iloc[-1]),
        "ema10": round_or_none(ema10.iloc[-1]),
        "ema21": round_or_none(ema21.iloc[-1]),
        "rsi14": round_or_none(rsi_value),
        "atr10": round_or_none(atr10),
        "sma20_deviation_pct": round_or_none(deviation),
        "supertrend_green": bool(st_up),
        "volume_ratio_to_prior_20d": round_or_none(volume_ratio),
        "median_daily_turnover_pkr_20d": round_or_none(median_turnover, 0),
        "technical_component_out_of_15": trend_score,
        "setup": setup,
        "illustrative_support_20d": round_or_none(support20),
        "illustrative_resistance_60d": round_or_none(resistance60),
        "illustrative_reward_risk": round_or_none(rr),
        "indicators": conditions,
        "warnings": flags,
        "fundamental_score": None,
        "valuation_score": None,
        "shariah_verified": False,
        "liquidity_spread_verified": False,
        "portfolio_exposure_verified": False,
        "full_verdict_score": None,
        "buy_approved": False,
        "decision": "RESEARCH_ONLY_NO_BUY_APPROVAL",
        "data_source": "configured OHLCV feed (see run settings)",
    }


def render_report(items: list[dict], errors: dict[str, str], timestamp: str) -> str:
    out = [
        "# PSX Wealth Creation Agent — Daily Research",
        "",
        f"Generated: {timestamp} (Pakistan time)",
        "",
        "**No automated trades or buy approvals.** Technical results are not the full 100-point verdict.",
        "Fundamental, valuation, Shariah, spread and portfolio risk gates require separate verification.",
        "",
        "| Symbol | Close (PKR) | RSI14 | EMA10 > EMA21 | Supertrend | Technical /15 | Setup |",
        "|---|---:|---:|:---:|:---:|---:|---|",
    ]
    for item in items:
        up = "Yes" if item["indicators"]["ema10_above_ema21"] else "No"
        st = "Green" if item["supertrend_green"] else "Red"
        out.append(
            f'| {item["symbol"]} | {item["close_pkr"]} | {item["rsi14"]} | '
            f'{up} | {st} | {item["technical_component_out_of_15"]} | {item["setup"]} |'
        )
    out += ["", "## Data or risk warnings", ""]
    for item in items:
        if item["warnings"]:
            out.append(f'- **{item["symbol"]}:** {", ".join(item["warnings"])}')
    for symbol, reason in errors.items():
        out.append(f"- **{symbol}: DATA UNAVAILABLE** — {reason}")
    if not errors and not any(i["warnings"] for i in items):
        out.append("No technical-data quality warnings.")
    out += [
        "",
        "A possible support/resistance reward/risk is illustrative only, not a validated stop or target.",
        "All indicators derive from historical daily prices and may reverse. Data may lag.",
        "Refer to individual company PSX financial disclosures before acting.",
        "",
    ]
    return "\n".join(out)


def load_prices(symbol: str, source: str, data_dir: str) -> pd.DataFrame:
    """Only use configured/approved market-data inputs.

    provider expects a CSV response with date,open,high,low,close,volume.
    Authentication remains in GitHub Actions secrets, never in the report.
    """
    if source == "csv":
        path = Path(data_dir) / (symbol + ".csv")
        if not path.is_file():
            raise FileNotFoundError("Local input missing: " + str(path))
        return pd.read_csv(path)
    if source == "provider":
        import requests
        template = os.getenv("PSX_OHLCV_URL_TEMPLATE", "")
        if not template or "{symbol}" not in template:
            raise ValueError("Authorized CSV provider URL template missing")
        url = template.replace("{symbol}", symbol)
        if not url.startswith("https://"):
            raise ValueError("Provider URL must use HTTPS")
        headers = {}
        token = os.getenv("PSX_OHLCV_TOKEN", "")
        if token:
            headers["Authorization"] = "Bearer " + token
        response = requests.get(url, headers=headers, timeout=25)
        response.raise_for_status()
        if len(response.content) > 5_000_000:
            raise ValueError("Provider CSV response exceeds 5 MB")
        return pd.read_csv(StringIO(response.text))
    if source == "psx":
        # Explicit opt-in; can fail due to PSX access restrictions.
        import psxdata
        return psxdata.stocks(
            symbol, start=(date.today() - timedelta(days=500)).isoformat(),
            end=date.today().isoformat()
        )
    raise ValueError("Unsupported data source")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--output", default="reports")
    parser.add_argument("--source", choices=["auto", "csv", "provider", "psx"], default="auto")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--symbols-from-data-dir", action="store_true")
    parser.add_argument("--watchlist", default=None)
    args = parser.parse_args()
    if args.watchlist and not args.symbols_from_data_dir:
        from psx_dps_feed import read_watchlist
        try:
            args.symbols = read_watchlist(args.watchlist)
        except ValueError as exc:
            parser.error(str(exc))
    if args.symbols_from_data_dir:
        data_path = Path(args.data_dir)
        detected = sorted(path.stem for path in data_path.glob("*.csv")
                          if path.stem.isalnum() and path.stem == path.stem.upper())
        if not detected:
            parser.error("No validated OHLCV CSV symbol files available")
        args.symbols = detected
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Karachi"))
    source = args.source
    if source == "auto":
        source = "provider" if os.getenv("PSX_OHLCV_URL_TEMPLATE") else (
            "csv" if Path(args.data_dir).is_dir() else "unconfigured"
        )
    items: list[dict] = []
    errors: dict[str, str] = {}
    if source == "csv":
        error_path = Path(args.data_dir) / "fetch_errors.json"
        if error_path.is_file():
            try:
                missing = json.loads(error_path.read_text(encoding="utf-8"))
                if isinstance(missing, dict):
                    errors.update({str(key): "Feed unavailable: " + str(value)[:60]
                                   for key, value in missing.items()})
            except (ValueError, OSError):
                errors["FEED"] = "Unreadable feed coverage report"
    for symbol in dict.fromkeys(s.upper().strip() for s in args.symbols):
        try:
            if source == "unconfigured":
                raise ValueError("No authorized feed configured; supply provider secret or --source csv")
            raw = load_prices(symbol, source=source, data_dir=args.data_dir)
            items.append(analyze(symbol, raw, today=now.date()))
        except Exception as exc:
            # Never include provider URLs or authentication values in public CI logs.
            msg = str(exc) if source == "csv" or source == "unconfigured" else type(exc).__name__
            errors[symbol] = f"{type(exc).__name__}: {msg[:120]}"
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_pakistan": now.isoformat(),
        "results": items,
        "errors": errors,
        "trading_automation_enabled": False,
        "all_full_verdict_scores_pending": True,
    }
    (output / "verdict.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = render_report(items, errors, now.isoformat(timespec="minutes"))
    (output / "verdict.md").write_text(report, encoding="utf-8")
    print(report)
    # A missing ticker cannot generate a signal; healthy tickers can still
    # be analyzed and reported. Fail only when no validated price data exists.
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())
