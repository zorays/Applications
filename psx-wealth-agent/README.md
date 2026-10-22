# PSX Wealth Creation Agent — research prototype

A **research-only** daily OHLCV monitor for Pakistan Stock Exchange tickers. It implements the technical part of the October 2026 **Contrarian–Momentum Framework**; it deliberately does **not** calculate or display an invented full 100-point rating or approve purchases.

## Data source

Uses [psxdata](https://github.com/psxdata/psxdata), an **unofficial, alpha-stage** Python client that retrieves PSX public-page history. PSX does not provide this application an officially supported production API. Respect PSX access/redistribution terms, request limits and licensing requirements. An official licensed feed is preferable before production or commercial deployment. The collector may fail due to remote-site changes or GitHub-hosted runner restrictions.

## Initial watchlist

SAZEW, LUCK, SYS, OGDC, FFC, MEBL, ENGROH, MARI, MLCF, FCCL, DGKC, ECOP, SPEL.

No account information, brokerage login, personal holdings, cost bases or family portfolio information are stored here. This is a **public repository**; do not add any of those details.

## Run

Requires Python 3.11+.

```bash
cd psx-wealth-agent
python -m pip install -r requirements.txt
python -m unittest -v test_agent
python agent.py --symbols SAZEW LUCK SYS --output reports
```

Outputs:

- `reports/verdict.json`: machine-readable indicators, data-quality flags, incomplete-gate indicators.
- `reports/verdict.md`: human-readable technical snapshot.

Data fetch or validation failure produces an explicit error and a nonzero process exit rather than inventing values.

## Scheduled workflow

The GitHub workflow `.github/workflows/psx-wealth-agent.yml` runs on weekdays around **17:45 Pakistan time (12:45 UTC)** and supports manual `workflow_dispatch`. Scheduled actions may be delayed or skipped by GitHub. It runs offline tests first, then retrieves OHLCV and uploads reports as a workflow artifact retained for 14 days. It does **not** push fetched price history back to the repository or execute broker orders.

Navigate to **Actions → PSX Wealth Research → Run workflow** to test manually. Download the `psx-wealth-report` artifact from the run to read verdicts.

## What is implemented

- Daily SMA20; EMA10/21; Wilder RSI14; Wilder-style ATR10; Supertrend(10,3).
- Volume versus prior 20 days; rolling median turnover proxy.
- 20-day swing low and 60-day high as **illustrative**, non-executable reward/risk references.
- A technical component out of 15 and provisional setup classification.
- Data staleness, anomalous bars, missing data and thin turnover flags.
- No recommendation or full score if safety and fundamental gates are not reviewed.

## What is intentionally NOT implemented

Fundamental resilience, audited debt/cash-flow analysis, intrinsic valuation discount, order-book liquidity/spreads, insider/governance screening, dated Shariah certification, portfolio correlation, and personal capital constraints must be integrated and verified independently before a full 100-point score or any buy approval. **No broker connection and no automatic trading.**

Do not treat RSI, EMA and Supertrend as independent evidence: they are correlated price-derived signals. This prototype has not been backtested or shown to be profitable.

## Next iterations

1. Verify access and fidelity of daily bars on GitHub-hosted runners.
2. Obtain permission for scheduled data access; if required, replace unofficial client with a licensed PSX feed.
3. Ingest fundamental disclosures and dated Shariah screening with evidence links.
4. Add adjusted corporate-action returns, true bid–ask and sector concentration.
5. Backtest with survivorship bias controls, trading costs, realistic fill assumptions and walk-forward testing.
6. Integrate private household-level portfolio exposure **only in protected infrastructure**.
