# PSX Wealth Agent: contrarian + momentum + private position risk policy

**Status October 23, 2026:** Implemented technical calculations, a hypothetical trade policy calculator, and conditional 7%-gross tactical take-profit reviews. Full 100-point investability grades are **NOT implemented**; financial, valuation, execution, and dated Shariah data are not yet verified. No automatic trades or buy approvals.

## Evidence-weighted 100-point rubric (DESIGN, NOT A LIVE FULL SCORE)

| Domain | Maximum points | Status / information required |
| --- | ---: | --- |
| Financial resilience: cash flow, leverage, margins, earnings consistency | 25 | PENDING audited issuer financials and sector normalization |
| Valuation: trailing/forward P/E, sector median, sustainable earnings, P/B and intrinsic-value sensitivity | 20 | PENDING sourced EPS and valuation context; negative earnings => P/E N/M |
| Mean reversion: 10–20% discount to SMA50, RSI recovery, price/volume reversal | 20 | **Research component calculated** out of 20, not an entry approval |
| Trend confirmation: EMA10/21, Supertrend, SMA20, RSI, volume | 15 | **Research component calculated** out of 15 |
| Execution: structural stop, after-tax/fee R/R, concentration and liquidity/spread | 10 | OFFLINE SCENARIO calculator only; real broker fees and lot data PENDING |
| Verifiable business catalyst with cash-flow transmission | 5 | PENDING independently dated evidence |
| Governance: insider filings, unusual movement warnings, litigation and disclosure quality | 5 | PENDING disclosures and independently dated review |
| **Maximum** | **100** | **Only 35 preliminary technical points can currently be shown** |

**Hard gates, not extra points:** current dated Shariah eligibility for any Shariah-only account, no severe issuer/PSX regulatory warning, no unreliable or stale OHLCV, sufficient liquidity, no disallowed capital concentration, known fees, and a defensible chart stop. Do not add an uninformed 65 points or score a prospective trade 100/100. Market-price indicators overlap; the two research subscores are NOT statistically independent evidence.

## Separate opportunity states

**Mean reversion:** A stock can trade 10–20% below a 50-session SMA and still be collapsing. Discount alone is not a buy. Review RSI(14) rising out of oversold territory (illustratively from below 35 into 30–55), price uptick, volume support, and an actual stop. The agent now outputs SMA10/20/21/50/200, the SMA50 discount, RSI change, a mean-reversion state and preliminary score out of 20. A 200-day average is null until 200 actual sessions exist; the 251-session target is historical data, not fabricated bars.

**Momentum:** EMA10 > EMA21, Supertrend green, price above SMA20, RSI in a sustainable range, and volume confirm a *different* entry style. The existing 15-point component remains. Do not force a mean-reversion discount and a full momentum recovery to occur simultaneously: classify each setup separately and wait when evidence disagrees.

**Business conviction:** Write a short falsifiable thesis, e.g. a dated launch milestone, order intake, revenue contribution, input costs, capital expenditure, operating cash, dilution and debt servicing. A seasonal kite-paper thesis or EV assembly headline is a hypothesis, not measured earnings. For P/E, divide the current share price by trailing 12-month diluted EPS from the issuer's relevant statements; for loss-making businesses conventional P/E is not meaningful. Compare with the same sector and normalize one-off and cyclical earnings.

## Preferred private tactical risk and take-profit economics

- Capital illustration: Rs 25,000. Maximum planned loss on a single new idea: **3% = Rs 750** *including assumed entry/exit trading friction*. Aggregate stop-risk across concurrent positions also matters; one triggered stop can slip in fast markets.
- Minimum **NET** planned profit/loss ratio: **golden ratio phi = (1 + sqrt(5))/2 ≈ 1.618**, not 1.8. This is a preference to evaluate and backtest, not a market law. A 2:1 NET ratio is superior on payoff asymmetry, and only a 2R net winner offsets two 1R losers. A 1.618R net win does not.
- Preferred tactical take-profit *price move*: **+7% gross** (stop/limit or partial-taking after review). This is **not** a guaranteed 7% after tax and costs.
- Risk is the *possible loss* to a defensible stop, not 3% of the shares' price, nor permission to spend only 3% of account value.
- Illustrative, NOT verified broker/account terms: 0.15% commission each side, 0% slippage, 15% capital-gains tax on gross positive price change. Actual NCCPL regime and any other taxes, minimum brokerage, levies and custody charges must be verified for the relevant investor, date and securities.
- Example: entry Rs100, **structural stop Rs97**, target Rs107. Estimated net target gain/share Rs5.6395; net loss/share Rs3.2955. Net R/R ≈1.711. Maximum **227 whole shares** under Rs25,000 capital and Rs750 total risk; entry cash including assumed fee ≈Rs22,734.05. Planned full-stop loss ≈Rs748.08; estimated full-target after-cost profit ≈Rs1,280.17.
- If the defensible stop is **Rs96** instead, net R/R falls to about **1.313**. Under the *preferred* golden-ratio rule, **decline the trade**, rather than manipulating a stop to justify entering. Repositioning the lot size caps rupee loss but does not improve R/R.
- At entry Rs100 and target Rs107 with these assumed costs, the lowest stop price that could pass phi is about **Rs96.81**. This boundary is a mathematical constraint, NOT a suggested technical stop.
- Gap-through-stop, partial fills, dividends, split adjustment, short-term volatility and taxes can invalidate the illustration.

Command line, from the repository's psx-wealth-agent folder:

    python trade_policy.py --entry 100 --stop 97 --capital 25000 --risk-pct 3 --target-pct 7 --commission-pct 0.15 --cgt-pct 15

Add --already-at-risk-pkr 500 to see how existing open risk reduces maximum new shares. Use **your actual broker charges and tax situation**, not the example defaults.

## Private sell-side handling

The public `watchlist.txt` means **research interest**, not ownership or Shariah clearance. Separately the private `PORTFOLIO_HOLDINGS_JSON` GitHub Actions secret identifies accounts with position styles and optional stops.

Example structure (FAKE PRICES AND TICKERS, NOT USER HOLDINGS):

    {
      "AccountA": {
        "EXAMPLE": {"style": "tactical", "entry": 100, "stop": 97}
      },
      "AccountB": {
        "SAMPLE": {"style": "long_term", "stop": 80, "take_profit": 130}
      }
    }

When `entry` is present for a **tactical** position, `position_guard.py` now requests TAKE_PROFIT_REVIEW once the latest confirmed closing quote is at least 7% above entry. Stop breaches and strong confirmed breakdowns can require more urgent EXIT_REVIEW or REDUCE_REVIEW. For long-term holdings, the arbitrary 7% sale rule is OFF unless a `take_profit` is explicitly provided. To calculate true holding-specific net gain and partial-sale quantities, a private filled lot ledger with acquisition dates, quantities, brokerage charges, tax status and average costs is still needed. Do NOT commit it to a public repository or attach it to public market reports.

Private alert checks require fresh validated exchange candles. Approved screenshot-derived bars may contribute to research but are excluded from automatic sell alerts until independently corroborated. A review email does not send an order.

## Historical data and the Ollama Cloud screenshot path

- Use the **free public PSX data collector** as the primary source while authorized; it updates all monitored shares and incrementally bootstraps historical data.
- Upload clearly cropped symbol-specific screenshots to the already-created [Drive Screenshots folder](https://drive.google.com/drive/folders/1Swy8I2Q709D_vtXv29S8ooL6HAuO97Op), naming each SYMBOL__unique_page_or_date.png. Chat-attached screenshots must first be explicitly placed there via an available connected file-upload action; **Apps Script cannot see ChatGPT attachments on its own**.
- In Google [Apps Script](https://script.google.com/home/projects/create), paste current [cloud_ollama_drive.gs](cloud_ollama_drive.gs) and store `OLLAMA_API_KEY` and `OLLAMA_CLOUD_MODEL` under **Project Settings → Script Properties**. Run `testVisionValidation`, then `setupVisionCloud` once, then manually `scanVisionInbox`. Apps Script creates a time trigger to check Drive every 15 minutes, up to three new images per scan.
- CSV and provenance are written to the private [OHLCV inbox](https://drive.google.com/drive/folders/1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek) as `SYMBOL.vision.csv` and `SYMBOL.vision.review.json`. Later screenshots append missing dated rows via image-hash evidence and deduplication. Conflicting rows reject a consolidation rather than changing a credible existing PSX candle.
- Review **every new OCR row** and run `approveSymbol('SYMBOL')`. A changed screenshot changes its provenance hash and revokes approval. Re-approve only after checking. There must be **250+ real OHLCV sessions** when that depth is requested; the system never fabricates older rows.
- GitHub Actions reads the same Drive inbox **read-only** using `GDRIVE_FOLDER_ID` and `GDRIVE_SERVICE_ACCOUNT_JSON` secrets. `drive_overlay.py` ignores unreviewed pairs and preserves exchange values in conflicts. Do not share the Ollama key in chat or GitHub source.
- A normal GitHub Actions cache is **not a permanent research database**. Google Drive preserves approved screenshot files, but broader canonical market-price archives require a separately authorized durable storage/versioning policy (e.g. private history files or vetted licensed snapshots); public-repo redistribution may violate data licenses.
- For further research, prioritize issuer annual/interim statements, PSX notices, dated Islamic screening, dividend/corporate action history, debt maturity and cash flows, insider transactions, liquidity and relative-sector valuation. Treat rumor-led hypotheses as evidence requests, not scoring points.

**No live Ollama Cloud extraction or Drive-to-GitHub authorized import has been verified during this update.** The repo contains implementation and offline tests; you still must authorize Apps Script and configure GitHub secrets to activate live movement.
