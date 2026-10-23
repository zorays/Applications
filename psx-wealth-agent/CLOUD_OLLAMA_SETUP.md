# PSX EClear screenshots → Ollama Cloud → Google Drive → GitHub

**Pipeline status (October 23, 2026):** GitHub cloud OCR script and mocked tests are implemented. The Screenshots Google Drive folder is configured and **18 DSIL price-history screenshot files (DSIL__page01.png through DSIL__page18.png) have been uploaded and verified in Drive**. **The hosted Ollama API key and Google Apps Script permissions/trigger are not yet configured.** No key is stored in GitHub. Previously uploaded captures can be processed without recapturing them, but further automatically capturing the Windows EClear screen requires a separate desktop agent.

## Your actual flow

1. **Capture EClear OHLC History**: EClear Trade Cast is a logged-in Windows desktop application. Neither Ollama's cloud service nor Google Apps Script can independently click/scroll it or retrieve screenshots while your Windows computer is off. A small Windows screenshot-capture automation running on your machine, or a broker-approved data export/API, is still necessary to eliminate the manual screenshots. Do not share broker login credentials.
2. The capture step uploads **only the cropped Date/Open/High/Low/Close/Volume history table** with filenames `SAZEW__page01.png`, `SAZEW__page02.png` etc. to the [Screenshots Drive folder](https://drive.google.com/drive/folders/1Swy8I2Q709D_vtXv29S8ooL6HAuO97Op). A screenshot of 13 visible rows contributes up to 13 rows—not an invented 250.
3. A Google Apps Script checks the Drive folder **every 15 minutes** and calls **Ollama's hosted vision API** using your private `OLLAMA_API_KEY`. It records only *visible* rows, merges overlap, flags inconsistent OCR, and saves unapproved `SYMBOL.vision.csv` and matching `SYMBOL.vision.review.json` in the parent Drive inbox.
4. After human verification of the extracted rows, run `approveSymbol('SAZEW')` using the Apps Script editor. Only then may GitHub import it.
5. GitHub's existing `PSX Wealth Research` action combines manually reviewed OCR with free PSX history but **does not replace inconsistent exchange observations**. As a safety guard, screenshot-derived histories remain research-only for buy/sell email signals until independently verified by market source.

## One-time Google Apps Script setup

- Open [Google Apps Script](https://script.google.com/home/projects/create) using the Google account that owns the Drive inbox.
- Copy the entire source from [cloud_ollama_drive.gs](https://github.com/zorays/Applications/blob/master/psx-wealth-agent/cloud_ollama_drive.gs) to `Code.gs`. Save.
- Open **Project Settings → Script Properties → Add script property**.
  - `OLLAMA_API_KEY` = your private [Ollama API key](https://ollama.com/settings/keys).
  - `OLLAMA_CLOUD_MODEL` = an **image-capable cloud model listed on your Ollama account**, for example `gemma4:cloud` (a vision-capable hosted model listed in Ollama's model library). Model availability can change; verify the exact ID via Ollama's model library before choosing.
- In the function dropdown, choose **testVisionValidation**, click Run. This test uses mock OHLC data and does not call Ollama.
- Choose **setupVisionCloud**, click Run, and approve Google's Drive management and external-request permissions. This creates a 15-minute timed trigger that executes **scanVisionInbox**.
- **Already present: 18 DSIL files** in Screenshots. Run **scanVisionInbox** manually once and inspect **Executions**. The script should create `DSIL.vision.csv` and `DSIL.vision.review.json` in the parent [OHLCV inbox](https://drive.google.com/drive/folders/1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek).
- The first export is **reviewed=false**. Open its CSV and check every date, price and volume manually against EClear. To mark it reviewed, add a wrapper like `function approveDSIL(){ approveSymbol('DSIL'); }`, save, select `approveDSIL` and run only after examining the entire CSV. It revokes approval automatically when input screenshots change.

## GitHub integration

Set repository Actions secrets (never commit credentials):
- `GDRIVE_FOLDER_ID`: `1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek`
- `GDRIVE_SERVICE_ACCOUNT_JSON`: complete private service account key JSON for the account shared on the parent Drive folder. Viewer access suffices for GitHub importing.
- Separate SMTP secrets for any email alerts.

Run [PSX Wealth Research](https://github.com/zorays/Applications/actions/workflows/psx-wealth-agent.yml) manually to confirm the `Overlay manually verified Google Drive OHLCV screenshots` step. Check `verdict.md` artifact; OCR dates will be marked research-only.

## Important constraints

**Cloud vision is not cloud screen automation.** To automatically visit the EClear Trade Cast OHLC window, change ticker, scroll 250 rows and capture screenshots, you still need a desktop agent running on Windows (with an authorized, read-only EClear session). Screen capture automation is not yet implemented. The current solution automatically processes and uploads **screenshots arriving in Google Drive**, not screenshots never captured. Avoid harvesting private account screens.

**Cloud OCR is not fully autonomous trading data approval.** A structurally valid but misread number can distort indicators. Human review or corroboration against PSX prices must precede buy/sell alerts.

**Cost and permissions:** Ollama Cloud inference may be metered; Apps Script Drive/UrlFetch quotas also apply. Do not use unbounded triggers or retry loops. Cloud API endpoint: `https://ollama.com/api/chat`, authenticated with a bearer token; images are sent as base64 in the chat message. Ollama Cloud may not support the local API's structured output format, so the script validates returned JSON independently.

**Safety/security:** remove brokerage account numbers, order activity and personally identifying information from screenshots before sharing with any cloud vision provider. Do not expose API keys in screenshots, GitHub code or chat.

## Screenshot-specific extraction warning

The DSIL screenshots also include occasional **all-zero nontrading date placeholders**. The cloud script now drops only rows where every price and volume field is zero, rather than treating them as historical OHLCV candles; all other implausible values still fail validation. Overlapping pages and dates are deduplicated. The visible images do **not** prove that every one of the broker's reported 251 history records is captured, so check that dates and row counts meet the strategy's required lookback before evaluating technical signals.
