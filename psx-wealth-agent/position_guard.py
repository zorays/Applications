"""Private, read-only exit and profit-taking reviews for owned PSX shares.

Public research artifacts never reveal ownership. Portfolio mappings come only
from PORTFOLIO_HOLDINGS_JSON (GitHub Actions secret); no private file is created.
No automatic orders; technical verdicts are always manual-review candidates.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from agent import normalize, rsi_wilder, supertrend
from alerts import deliver

PAKISTAN = ZoneInfo("Asia/Karachi")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
STYLES = {"tactical", "long_term"}
ALERT_LEVELS = {"EXIT_REVIEW", "REDUCE_REVIEW", "TAKE_PROFIT_REVIEW"}


def parse_portfolios(raw: str) -> dict[str, dict[str, dict]]:
    """Validate secret JSON; never include it in errors/logs.

    Format: {"ZORAYS":{"SAZEW":{"style":"tactical","stop":1400}},
             "IMAN":{"SLM":{"style":"long_term"}}}
    """
    if not raw or len(raw) > 16384:
        raise ValueError("Private portfolio configuration is empty or oversized")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("Invalid portfolio secret JSON") from None
    if not isinstance(data, dict) or not data or len(data) > 5:
        raise ValueError("Portfolio secret must contain 1–5 named accounts")
    result = {}
    for owner, positions in data.items():
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", owner):
            raise ValueError("Invalid private portfolio account label")
        if not isinstance(positions, dict) or len(positions) > 100:
            raise ValueError("Portfolio positions must be a symbol-keyed object")
        result[owner] = {}
        for symbol, settings in positions.items():
            if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
                raise ValueError("Invalid symbol in private portfolio")
            if not isinstance(settings, dict):
                raise ValueError("Each position must provide a settings object")
            if set(settings) - {"style", "stop", "take_profit"}:
                raise ValueError("Unsupported private position field")
            style = settings.get("style", "long_term")
            if style not in STYLES:
                raise ValueError("Position style must be tactical or long_term")
            validated = {"style": style}
            for key in ("stop", "take_profit"):
                if key in settings:
                    v = settings[key]
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not (0 < v < 10_000_000):
                        raise ValueError("Position price level must be finite and positive")
                    validated[key] = float(v)
            if "stop" in validated and "take_profit" in validated and validated["stop"] >= validated["take_profit"]:
                raise ValueError("Stop must be below profit-taking level")
            result[owner][symbol] = validated
    return result


def technical_exit_radar(raw: pd.DataFrame) -> dict:
    """Price-derived evidence; uses previous 20-session low, excluding today."""
    df = normalize(raw)
    close, low = df["close"], df["low"]
    price = float(close.iloc[-1])
    prior20_low = float(low.iloc[-21:-1].min())
    ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
    ema21 = close.ewm(span=21, adjust=False, min_periods=21).mean()
    sma20 = close.rolling(20).mean()
    rsi = float(rsi_wilder(close).iloc[-1])
    green, _ = supertrend(df)
    prev_green, _ = supertrend(df.iloc[:-1].reset_index(drop=True))
    vol_prior = df["volume"].iloc[-21:-1].mean()
    vol_ratio = float(df["volume"].iloc[-1] / vol_prior) if vol_prior > 0 else 0.0
    return {
        "date": df["date"].iloc[-1].date().isoformat(),
        "close": round(price, 2),
        "rsi14": round(rsi, 2),
        "ema_bearish": bool(ema10.iloc[-1] < ema21.iloc[-1]),
        "ema_downcross_today": bool(ema10.iloc[-2] >= ema21.iloc[-2] and ema10.iloc[-1] < ema21.iloc[-1]),
        "supertrend_red": not green,
        "supertrend_flipped_red": bool(prev_green and not green),
        "below_sma20": bool(price < float(sma20.iloc[-1])),
        "prior20_low": round(prior20_low, 2),
        "fresh_20d_break": bool(price < prior20_low and float(close.iloc[-2]) >= prior20_low),
        "volume_ratio": round(vol_ratio, 2),
        "last_bar_age": None,
    }


def evaluate_position(radar: dict, settings: dict, today: str, max_bar_age_days: int = 0) -> dict:
    """Make advisory choices without using holding quantities or entry cost."""
    from datetime import date
    try:
        age = (date.fromisoformat(today) - date.fromisoformat(radar["date"])).days
    except (ValueError, KeyError):
        age = 999
    if age < 0 or age > max_bar_age_days:
        return {"verdict": "NO_FRESH_DATA", "reasons": ["No fresh validated trading session"],
                "notify": False}
    price = float(radar["close"])
    style = settings.get("style", "long_term")
    risk = bool(radar["ema_bearish"] and radar["supertrend_red"] and radar["below_sma20"])
    reasons = []
    if "stop" in settings and price <= settings["stop"]:
        reasons.append("Owner-defined stop level breached at closing price")
        verdict = "EXIT_REVIEW"
    elif style == "tactical" and risk and (
            radar["fresh_20d_break"] and radar["rsi14"] < 45
            or radar["ema_downcross_today"]
            or radar["supertrend_flipped_red"] and radar["rsi14"] < 45):
        reasons.append("New price-structure breakdown and bearish trend confirmation")
        verdict = "EXIT_REVIEW"
    elif radar["fresh_20d_break"] and risk and (
            radar["rsi14"] < 40 or radar["volume_ratio"] >= 1.2):
        reasons.append("New 20-session support break during bearish trend")
        verdict = "REDUCE_REVIEW"
    elif (radar["supertrend_flipped_red"] and radar["ema_bearish"] and
          radar["rsi14"] < 40):
        reasons.append("Supertrend freshly turned negative while momentum is weak")
        verdict = "REDUCE_REVIEW"
    elif "take_profit" in settings and price >= settings["take_profit"]:
        reasons.append("Owner-defined profit-taking level reached")
        verdict = "TAKE_PROFIT_REVIEW"
    elif risk:
        reasons.append("Bearish indicators persist without a new exit trigger")
        verdict = "WATCH_RISK"
    else:
        reasons.append("No new price-only sell/trim trigger")
        verdict = "HOLD_REVIEW"
    return {"verdict": verdict, "reasons": reasons, "notify": verdict in ALERT_LEVELS}


def assess_portfolios(report: dict, secret: str, data_dir: Path, today: str) -> tuple[list[dict], int]:
    profiles = parse_portfolios(secret)
    allowed = {item["symbol"] for item in report.get("results", [])}
    by_symbol = {}
    outcomes = []
    for owner, holdings in profiles.items():
        for symbol, setting in holdings.items():
            if symbol not in allowed:
                outcomes.append({"owner": owner, "symbol": symbol, "verdict": "NO_FRESH_DATA",
                                 "notify": False, "reasons": ["Symbol not evaluated in public report"]})
                continue
            if symbol not in by_symbol:
                try:
                    by_symbol[symbol] = technical_exit_radar(pd.read_csv(data_dir / (symbol + ".csv")))
                except (OSError, ValueError, KeyError, TypeError):
                    by_symbol[symbol] = None
            radar = by_symbol[symbol]
            if radar is None:
                outcome = {"verdict": "NO_FRESH_DATA", "notify": False,
                           "reasons": ["Invalid or incomplete OHLCV candles"]}
            else:
                outcome = evaluate_position(radar, setting, today)
            outcomes.append({"owner": owner, "symbol": symbol, **outcome,
                             "price": None if radar is None else radar["close"],
                             "style": setting["style"],
                             "signal_date": None if radar is None else radar["date"]})
    return outcomes, len(profiles)


def private_email(outcomes: list[dict], today: str, incomplete: int) -> str:
    """Email body only. Never write or print ownership-specific text in public logs."""
    lines = [
        f"PSX OWNED POSITIONS — PRIVATE EXIT REVIEW ({today})",
        "",
        "Technical signals for HUMAN REVIEW, NOT automatic sell instructions.",
        "Signals do not incorporate latest audited financials, intrinsic value,",
        "corporate events, Shariah changes, bid/ask execution or liquidity gaps.",
        "",
    ]
    for row in outcomes:
        if not row["notify"]:
            continue
        lines += [f"{row['owner']} / {row['symbol']} / {row['verdict']}",
                  f"Last confirmed close: PKR {row['price']}; data session: {row['signal_date']}",
                  "Cause: " + "; ".join(row["reasons"]), ""]
    lines += [
        f"Other covered holdings or pending/no-data positions: {incomplete}",
        "Review the full portfolio and avoid reacting to one-day volatility.",
        "A triggered stop does not guarantee an executable sale price.",
        "No orders were sent. Ownership is stored only in GitHub Actions secrets.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="reports/verdict.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    raw_secret = os.getenv("PORTFOLIO_HOLDINGS_JSON", "")
    if not raw_secret:
        print("Private owned-stock exit radar disabled: no PORTFOLIO_HOLDINGS_JSON secret.")
        return 0
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        today = datetime.now(PAKISTAN).date().isoformat()
        outcomes, accounts = assess_portfolios(report, raw_secret, Path(args.data_dir), today)
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("Portfolio sell-side configuration or OHLCV report invalid") from None
    flagged = [x for x in outcomes if x["notify"]]
    # Explicitly do NOT print holding symbols, portfolio identities, costs or levels.
    print(f"Private sell radar checked {len(outcomes)} holdings across {accounts} accounts; "
          f"{len(flagged)} need exit/reduce/profit-taking review. "
          f"{sum(x['verdict'] == 'NO_FRESH_DATA' for x in outcomes)} lack fresh confirmed data.")
    if not flagged:
        print("No fresh position-level sell review event; no private email.")
        return 0
    if not args.send:
        print("Dry run: private alert candidates identified; no email sent.")
        return 0
    try:
        # Reuse authenticated Gmail sender; subject intentionally omits holdings.
        deliver(
            private_email(outcomes, today, len(outcomes) - len(flagged)),
            ["OWNED POSITION EXIT REVIEW"]
        )
    except Exception:
        raise SystemExit("Private sell-side email delivery failed") from None
    print("Private exit-review email delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
