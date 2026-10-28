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
from trade_policy import PHI, evaluate_trade

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
    sma10 = close.rolling(10).mean()
    sma20 = close.rolling(20).mean()
    sma21 = close.rolling(21).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
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
    prior_rsi = float(rsi14.iloc[-2])
    deviation50 = 100 * (price / float(sma50.iloc[-1]) - 1)
    # Mean-reversion is independently observed rather than added to the
    # momentum score: buying an oversold downtrend is not automatically safe.
    if deviation50 <= -20 and rsi_value < 30:
        mean_reversion_state = "EXTREME_OVERSOLD_CAUTION"
    elif (-20 <= deviation50 <= -5 and 30 <= rsi_value <= 55
          and rsi_value > prior_rsi and price > float(close.iloc[-2])):
        mean_reversion_state = "POTENTIAL_RECOVERY_RESEARCH_ONLY"
    elif deviation50 <= -10:
        mean_reversion_state = "DEEP_DISCOUNT_NO_REVERSAL"
    else:
        mean_reversion_state = "NO_CONFIRMED_REVERSAL"

    # Separate, preliminary mean-reversion evidence out of 20; NOT a
    # completed investment verdict and NOT independent of price momentum.
    reversion_checks = {
        "sma50_discount_10_to_20_pct": -20 <= deviation50 <= -10,
        "rsi_recovery_from_oversold": (
            prior_rsi < 35 and 30 <= rsi_value <= 55 and rsi_value > prior_rsi
        ),
        "up_close_vs_prior_session": price > float(close.iloc[-2]),
        "volume_at_least_prior_20d_mean": volume_ratio is not None and volume_ratio >= 1.0,
        "close_above_sma10": price > float(sma10.iloc[-1]),
    }
    reversion_weights = {
        "sma50_discount_10_to_20_pct": 5,
        "rsi_recovery_from_oversold": 6,
        "up_close_vs_prior_session": 3,
        "volume_at_least_prior_20d_mean": 3,
        "close_above_sma10": 3,
    }
    reversion_score = sum(reversion_weights[k] for k, yes in reversion_checks.items() if yes)

    # Technical momentum component: exactly five observations, 3 points each.
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
    # A hypothetical 7%-gross-target trade uses the actual 20-session low
    # as a candidate stop. Its 25k/3% sizing and 15%-CGT assumptions are
    # explicitly NOT verified broker charges or personal position settings.
    hypothetical_trade = (evaluate_trade(price, support20)
                          if support20 > 0 and support20 < price else None)
    flags = []
    if days_old < 0 or days_old > 7:
        flags.append(f"stale_or_future_data_{days_old}_calendar_days")
    if median_turnover < 3_000_000:
        flags.append("thin_turnover_proxy")
    if volume_ratio is None:
        flags.append("volume_comparison_unavailable")
    if (hypothetical_trade is None or
        hypothetical_trade["status"] != "RESEARCH_ONLY_POLICY_PASSED"):
        flags.append("hypothetical_after_cost_golden_ratio_not_met")

    return {
        "symbol": symbol,
        "last_bar": last_date.isoformat(),
        "close_pkr": round_or_none(price),
        "sma10": round_or_none(sma10.iloc[-1]),
        "sma20": round_or_none(sma20.iloc[-1]),
        "sma21": round_or_none(sma21.iloc[-1]),
        "sma50": round_or_none(sma50.iloc[-1]),
        "sma200": round_or_none(sma200.iloc[-1]),
        "sma50_deviation_pct": round_or_none(deviation50),
        "prior_rsi14": round_or_none(prior_rsi),
        "mean_reversion_state": mean_reversion_state,
        "mean_reversion_research_component_out_of_20": reversion_score,
        "mean_reversion_checks": reversion_checks,
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
        "hypothetical_7pct_trade": hypothetical_trade,
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


def render_report(items: list[dict], errors: dict[str, str], timestamp: str, coverage: dict | None = None) -> str:
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
    if coverage:
        out += ["", f"**Data coverage:** {coverage.get('completed', '?')}/{coverage.get('requested', '?')} symbols have at least {MIN_BARS} valid historical candles.",
                f"**Source updates:** One whole-market daily price request; up to {coverage.get('historical_bootstrap_attempts', '?')} historical backfills this run.",
                "**Uninitialized symbols:** Awaiting gradual backfill; NOT a bearish trading signal.", ""]
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
        label = ("AWAITING INITIAL HISTORY" if
                 ("bootstrap pending" in reason.lower()
                  or "bootstrap deferred" in reason.lower())
                 else "DATA UNAVAILABLE")
        out.append(f"- **{symbol}: {label}** — {reason}")
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
    ocr_provenance = {}
    if source == "csv":
        provenance_path = Path(args.data_dir) / "ocr_sources.json"
        if provenance_path.is_file():
            try:
                possible = json.loads(provenance_path.read_text(encoding="utf-8"))
                if isinstance(possible, dict):
                    ocr_provenance = possible
            except (ValueError, OSError):
                errors["PROVENANCE"] = "OCR evidence file unreadable; no price-based alert approval"
    collection_summary = None
    if source == "csv":
        summary_path = Path(args.data_dir) / "collection_summary.json"
        if summary_path.is_file():
            try:
                parsed = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    collection_summary = parsed
            except (ValueError, OSError):
                pass
    for symbol in dict.fromkeys(s.upper().strip() for s in args.symbols):
        try:
            if source == "unconfigured":
                raise ValueError("No authorized feed configured; supply provider secret or --source csv")
            raw = load_prices(symbol, source=source, data_dir=args.data_dir)
            result = analyze(symbol, raw, today=now.date())
            provenance = ocr_provenance.get(symbol, {})
            if isinstance(provenance, dict) and provenance.get("ocr_dates"):
                mode = provenance.get("verification")
                result["ocr_supplemented"] = True
                result["ocr_verification"] = mode or "manually_reviewed"
                if mode == "auto_verified_independent_psx":
                    result["data_source"] = "OCR independently cross-checked against PSX historical OHLCV"
                    result["independently_matched_rows"] = provenance.get("independently_matched_rows", 0)
                elif mode == "owner_trusted_eclear_manually_transcribed_provisional":
                    result["warnings"].append("owner_trusted_eclear_transcription_provisional_not_complete_251_sessions")
                    if provenance.get("known_source_gaps"):
                        result["warnings"].append("eclear_history_missing_sessions_require_review")
                    result["data_source"] = "Owner EClear TradeCast OHLCV screenshot transcription (research-only)"
                else:
                    result["warnings"].append("screenshot_derived_candles_need_independent_verification")
                    result["data_source"] = "PSX history supplemented with human-reviewed screenshot OCR"
                # Technical research can be shown but is never a 100-point buy verdict.
                result["buy_approved"] = False
            items.append(result)
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
        "collection_summary": collection_summary,
    }
    (output / "verdict.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = render_report(items, errors, now.isoformat(timespec="minutes"), coverage=collection_summary)
    (output / "verdict.md").write_text(report, encoding="utf-8")
    print(report)
    # A missing ticker cannot generate a signal; healthy tickers can still
    # be analyzed and reported. Fail only when no validated price data exists.
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())
