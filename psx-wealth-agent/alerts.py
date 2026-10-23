"""Send *technical research candidate* alerts, never automatic buy orders.

The 100-point checklist is incomplete until fundamental, financial, Shariah,
execution-liquidity and portfolio gates can be independently verified.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

PAKISTAN = ZoneInfo("Asia/Karachi")


def candidate(item: dict, today: str) -> bool:
    """A demanding technical setup; not proof of a profitable trade."""
    def value(key):
        x = item.get(key)
        return float(x) if x is not None else None

    rsi = value("rsi14")
    vol = value("volume_ratio_to_prior_20d")
    dev = value("sma20_deviation_pct")
    turnover = value("median_daily_turnover_pkr_20d")
    scenario = item.get("hypothetical_7pct_trade") or {}
    net_rr = scenario.get("net_reward_risk")
    return all([
        item.get("last_bar") == today,
        item.get("setup") == "B_TREND_RECOVERING",
        int(item.get("technical_component_out_of_15") or 0) >= 15,
        item.get("warnings") == [],
        item.get("buy_approved") is False,
        rsi is not None and 50 <= rsi <= 70,
        vol is not None and vol >= 1.2,
        dev is not None and 0 <= dev <= 8,
        turnover is not None and turnover >= 3_000_000,
        scenario.get("status") == "RESEARCH_ONLY_POLICY_PASSED",
        net_rr is not None and net_rr >= 1.61803398875,
    ])


def make_message(items: list[dict], today: str, missing: dict | None = None) -> str:
    lines = [
        f"PSX TECHNICAL WATCHLIST — {today}",
        "",
        "Technical setup qualifies for HUMAN REVIEW ONLY.",
        "This is NOT a buy instruction. The full 100-point verdict and all hard safety gates are incomplete.",
        "",
    ]
    for item in items:
        lines += [
            f"{item['symbol']} — PKR {item['close_pkr']}",
            f"RSI14 {item['rsi14']}; EMA10 {item['ema10']} > EMA21 {item['ema21']}; "
            f"Supertrend {'green' if item['supertrend_green'] else 'red'}",
            f"Volume ratio {item['volume_ratio_to_prior_20d']}; "
            f"technical component {item['technical_component_out_of_15']}/15",
            f"Illustrative gross historical reward/risk {item['illustrative_reward_risk']}",
            f"Hypothetical 7%-target post-cost R/R {(item.get('hypothetical_7pct_trade') or {}).get('net_reward_risk')} "
            "(requires actual broker charges, tax status and a defensible stop)",
            f"Daily bar: {item['last_bar']}",
            "",
        ]
    if missing:
        lines += [
            "CAUTION: Partial watchlist coverage. No technical result was obtained for:",
            ", ".join(sorted(missing)),
            "These missing symbols were excluded, not presumed safe or unsafe.",
            "",
        ]
    lines += [
        "Before entering: independently check financials, corporate disclosures,",
        "valuation, sector news, dated Shariah status, liquidity/spread, portfolio",
        "concentration and a defensible stop with realistic fees/slippage.",
        "Never use payroll funds or rely on these price-derived indicators alone.",
        "",
        "Sent by PSX Wealth Research GitHub Action. No orders have been submitted.",
    ]
    return "\n".join(lines)


def deliver(message: str, symbols: list[str]) -> None:
    user = os.getenv("ALERT_SMTP_USER", "")
    password = os.getenv("ALERT_SMTP_APP_PASSWORD", "")
    recipient = os.getenv("ALERT_EMAIL_TO", "")
    if not all([user, password, recipient]):
        raise RuntimeError("SMTP secrets not configured")
    mail = EmailMessage()
    mail["Subject"] = "PSX TECHNICAL REVIEW: " + ", ".join(symbols)
    mail["From"] = user
    mail["To"] = recipient
    mail.set_content(message)
    host = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, 465, timeout=25) as smtp:
        smtp.login(user, password)
        smtp.send_message(mail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="reports/verdict.json")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    now = datetime.now(PAKISTAN).date().isoformat()
    missing = data.get("errors") or {}
    if missing:
        print("Partial watchlist coverage: " + ", ".join(sorted(missing)))
    selected = [item for item in data.get("results", []) if candidate(item, now)]
    if not selected:
        print("No *fresh* technical review setups. No email sent.")
        return 0
    body = make_message(selected, now, missing=missing)
    print(f"Review candidates: {', '.join(item['symbol'] for item in selected)}")
    if args.send:
        try:
            deliver(body, [item["symbol"] for item in selected])
            print("Review alert email sent.")
        except Exception as exc:
            raise SystemExit("Email delivery failed: " + type(exc).__name__) from None
    else:
        print("Dry run: email not sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
