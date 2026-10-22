# PSX Wealth Creation Agent — research prototype

A **research-only** daily OHLCV monitor for Pakistan Stock Exchange tickers. It implements the technical part of the October 2026 **Contrarian–Momentum Framework**; it deliberately does **not** calculate or display an invented full 100-point rating or approve purchases.

## Data source

Supports **licensed Capital Stake REST API history directly**, private Google Drive CSV uploads, manually provided CSV data, an alternate licensed HTTPS CSV provider, or an **explicit opt-in** [psxdata](https://github.com/psxdata/psxdata) mode (an unofficial, alpha-stage Python client that retrieves PSX public-page history). PSX does not provide this application an officially supported production API. Respect PSX access/redistribution terms, request limits and licensing requirements. An official licensed feed is preferable before production or commercial deployment. The collector may fail due to remote-site changes or GitHub-hosted runner restrictions.

## Initial watchlist

SAZEW, LUCK, SYS, OGDC, FFC, MEBL, ENGROH, MARI, MLCF, FCCL, DGKC, ECOP, SPEL.

No account information, brokerage login, personal holdings, cost bases or family portfolio information are stored here. This is a **public repository**; do not add any of those details.

## Run

Requires Python 3.11+.

```bash
cd psx-wealth-agent
python -m pip install -r requirements.txt
python -m unittest discover -p 'test_*.py' -v
# Put authorized CSV files such as SAZEW.csv, LUCK.csv and SYS.csv in a local data folder.
python agent.py --source csv --data-dir data --symbols SAZEW LUCK SYS --output reports
```

Outputs:

- `reports/verdict.json`: machine-readable indicators, data-quality flags, incomplete-gate indicators.
- `reports/verdict.md`: human-readable technical snapshot.

Data fetch or validation failure produces an explicit error and a nonzero process exit rather than inventing values.

## Scheduled workflow

The GitHub workflow `.github/workflows/psx-wealth-agent.yml` runs weekdays around **17:45 Pakistan time (12:45 UTC)**, on relevant code pushes, and on manual `workflow_dispatch`. GitHub schedules may be delayed or skipped. It runs offline tests first. **Live data retrieval is skipped until a permitted provider is configured**, following a confirmed HTTP 403 when the initial cloud runner tried to access official PSX historical pages. Once configured it uploads reports retained for 14 days. It does **not** push fetched prices or execute broker orders.

### Authorized provider configuration

For an **alternate** licensed CSV provider, in **Settings → Secrets and variables → Actions**, create a repository secret named `PSX_OHLCV_URL_TEMPLATE`, containing an authorized HTTPS CSV endpoint template, e.g. `https://your-licensed-provider.example/ohlcv/{symbol}.csv`. Optionally create `PSX_OHLCV_TOKEN` for bearer authentication. The endpoint must return CSV columns `date,open,high,low,close,volume` with at least 80 validated daily bars. Replace the example URL with an actual provider you have permission to use; the project **does not include** a provider subscription or access key. Do not put secrets in source control.

Until the secret exists, successful CI verifies **only code/tests**, not live PSX prices. Live reports become available after the authorized endpoint is configured and a run succeeds.

Navigate to **Actions → PSX Wealth Research → Run workflow** to test manually. Once a provider is configured, download the `psx-wealth-report` artifact from a successful run to read verdicts. Without a feed, the workflow simply reports a configuration warning.

## Preferred: Capital Stake documented PSX API → GitHub

The Capital Analytics website is operated by the Capital Stake group, which publishes its **separately authenticated developer API** documentation:

- [Capital Stake REST introduction](https://www.capitalstake.com/docs/rest/intro)
- [EOD unadjusted OHLCV](https://www.capitalstake.com/docs/rest/api/stocks/market-quotes-and-trading/charts-ohlc/end-of-day-stocks-unadjusted)
- [EOD adjusted OHLCV](https://www.capitalstake.com/docs/rest/api/stocks/market-quotes-and-trading/charts-ohlc/end-of-day-stocks-adjusted)
- [Daily technicals](https://www.capitalstake.com/docs/rest/api/stocks/performance-and-analytics/technical-indicators/technicals-by-symbol)

**A browser access link or logged-in Capital Analytics portal token is not a developer API entitlement.** Request a separate developer Bearer token and confirm permitted access, historical depth, rates, fees and scheduled research usage with `data.solutions@capitalstake.com`. This repository neither extracts nor reuses an EClear/browser login token.

**Activation:** In GitHub `zorays/Applications → Settings → Secrets and variables → Actions`, add a secret called `CAPITALSTAKE_API_TOKEN` with a dedicated authorized Capital Stake API token. Do not commit it or send it in chat.

The GitHub workflow then prioritizes `capitalstake_feed.py`, which calls only official documented HTTPS endpoint `https://csapis.com/3.0/market/eod` with the Bearer token in the **Authorization header**. It requests historical `time,open,high,low,close,volume` data for the public research watchlist, converts Unix timestamps to Pakistan session dates and writes temporary OHLCV CSV files for the existing indicator engine. The default is **unadjusted** history appropriate for real-price technical entry zones; an optional `--adjusted` flag is available for return/backtest contexts, but adjusted values are not executable order prices. It performs no trading.

No Google Drive folder, desktop OMS, local Windows process or scheduled report export is necessary when this API access is supplied. Drive remains a fallback if the API subscription is unavailable. GitHub runs weekdays at about 17:45 PKT, and the only emailed outputs are tentative **technical review candidates** once the existing SMTP secrets have also been set.

Run the adapter locally once authenticated:

```bash
cd psx-wealth-agent
# Set CAPITALSTAKE_API_TOKEN in local environment, do not paste in code.
python capitalstake_feed.py --symbols SAZEW LUCK SYS --output data
python agent.py --source csv --symbols-from-data-dir --data-dir data --output reports
```

The current public repo is designed for **market-only** tickers. Do not upload private household position information. API imports are not considered proven until an authenticated GitHub Action has fetched and validated enough real candles.

## EClear OMS → Google Drive → GitHub → email integration

This integration is **implemented but not activated**. The desktop EClear OMS app must provide an **authorized historical OHLCV export**. Do not screen-scrape or capture hidden login services without EClear/StockHub permission. No EClear login/password is used.

### Private Google Drive inbox

A dedicated Google Drive folder named **PSX Wealth Agent - OHLCV Inbox** has been created in the user's connected Drive.

- Upload **raw CSV**, not an account statement, a PDF, an Excel spreadsheet, or screenshots.
- Filename must be **exactly** `SAZEW.csv`, `LUCK.csv`, `SYS.csv`, etc., matching a ticker in `DEFAULT_SYMBOLS` in `agent.py`. One CSV for each stock being monitored.
- Every file needs the headers `date,open,high,low,close,volume`, with ISO dates (e.g. `2026-10-22`) and preferably at least 250 trading sessions (minimum 80 validated candles). Numerical prices should be **adjusted consistently for corporate actions** when supplied by the authorized source. Date rows must be sorted by the analyzer; do not insert summaries or monetary values as text.
- Uploading updated versions of the same named CSV file is fine; importer chooses the newest item by modified time.
- Google Drive for Desktop can **sync** an export directory after the OMS has created the CSV. Drive does **not** itself trigger the desktop OMS to generate reports. Configure that only through documented OMS functionality or manual export.
- Never put account statements, broker credentials, trade records, portfolio balances, or personal files into this inbox.

### Enable read-only Google Drive API access

1. In Google Cloud, create or select a project, enable the **Google Drive API**, and create a service account with minimal privileges. Obtain its service-account email and JSON credentials through Google's authorized setup.
2. In Google Drive, share **only the OHLCV inbox folder** with that service-account email as **Viewer**. A linked ChatGPT Drive connection does **not** automatically authenticate GitHub Actions.
3. In GitHub `zorays/Applications → Settings → Secrets and variables → Actions`, add repository secrets:
   - `GDRIVE_FOLDER_ID`: Drive folder ID of the market-only inbox.
   - `GDRIVE_SERVICE_ACCOUNT_JSON`: **entire** service-account JSON, stored exclusively as a secret.
4. Keep these secrets out of commits, issue comments, chat messages, screen captures and CSV files. Rotate/delete keys if exposed.

The workflow scans at most 10 Drive API pages, reads only `{SYMBOL}.csv` matching its configured watchlist, verifies minimum OHLCV history, does not modify Drive files, and stores downloads only in the temporary GitHub runner. It will not read Google Sheets or PDFs.

### Enable email alerts

Use a dedicated SMTP-enabled sender account; Gmail may require two-factor authentication and an **app password**, not the normal account password. In GitHub repository Actions secrets, add:

- `ALERT_EMAIL_TO`: desired destination email address.
- `ALERT_SMTP_USER`: SMTP sender's email address.
- `ALERT_SMTP_APP_PASSWORD`: SMTP app-specific password or permitted SMTP credential.

The default SMTP server is `smtp.gmail.com` port 465. For a different provider add `ALERT_SMTP_HOST` to the workflow environment only after confirming its secure SMTP host and compatible authentication.

**Alert criteria are deliberately strict.** Same-day fresh daily bar; Supertrend green; EMA10 > EMA21; close above SMA20; RSI14 50–70; volume at least 1.2× prior 20-day average; SMA20 deviation 0–8%; 20-day turnover proxy at least Rs3m; illustrative reward/risk at least 2; no technical-data warnings; technical component 15/15. These criteria generate a **TECHNICAL REVIEW**, not a trading recommendation.

A technical signal cannot satisfy the remaining 85 points of the published 100-point system on its own. No financial, valuation, portfolio, current Shariah eligibility or execution-spread gates are yet approved. The agent never places an order.

The workflow runs once after the market on each weekday (normally 17:45 PKT) and can be started manually under **Actions → PSX Wealth Research → Run workflow**. GitHub scheduling may be delayed, and repeated manual runs with the same day's qualifying data can produce duplicate alert emails until persistent deduplication is implemented. The daily research report is uploaded as an Actions artifact, **not** as a publicly committed file.

If any required secrets are absent, the workflow runs **tests only** and emits a configuration notice; that success does **not** mean market retrieval and email delivery are active.

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

1. Obtain authorized Capital Stake REST API entitlement; verify live data access and fidelity of daily bars from GitHub-hosted runners.
2. If API subscription is not available, verify Capital Analytics web UI allows permitted scheduled per-symbol CSV exports into the Drive inbox.
3. Ingest fundamental disclosures and dated Shariah screening with evidence links.
4. Add adjusted corporate-action returns, true bid–ask and sector concentration.
5. Backtest with survivorship bias controls, trading costs, realistic fill assumptions and walk-forward testing.
6. Integrate private household-level portfolio exposure **only in protected infrastructure**.
