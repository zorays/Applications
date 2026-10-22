# Windows Ollama Vision → Google Drive → PSX GitHub Research

This optional bridge is for **screenshots of the OHLC HISTORY TABLE**, not price-watchlist screens, account balances, broker order forms or private information. It never connects to your broker login or executes a trade. All images stay on your Windows PC and are sent only to the **local** Ollama model at `127.0.0.1:11434`. It uploads **CSV and review metadata only**, not screenshots.

**Current status:** GitHub code and mocked offline tests are implemented. The user's Windows Ollama model and Google Desktop OAuth have **not been remotely installed or authenticated**. You must complete the setup below.

## 1. Prepare Windows once

Install [Python 3.11+](https://www.python.org/downloads/windows/) and [Ollama for Windows](https://ollama.com/download/windows). On the Windows PC, download or `git pull` the public repository and open PowerShell in `Applications\psx-wealth-agent`.

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-setup.ps1
ollama pull llama3.2-vision
```

The setup creates `screenshots\`, `vision_output\`, `.private\`, and a virtual Python environment. Check `ollama list` to ensure the downloaded **vision-capable** model is available. You may select a different local vision model with `--model` using the Python entry point.

## 2. Collect actual OHLC-history screenshots

In EClear Trade Cast open `OHLC History`, select a ticker (for example SAZEW), and display the columns **Date / Open / High / Low / Close / Volume**.

Crop screenshots to the **historical-price grid** and preserve clear headers/rows. Do not photograph the full brokerage desktop with account details. As you scroll, save several overlapping screenshots:

```text
screenshots\SAZEW__page01.png
screenshots\SAZEW__page02.png
screenshots\SAZEW__page03.png
screenshots\LUCK__page01.png
```

The text *before two underscores* MUST be an exact symbol in `watchlist.txt`. Screenshot overlap is fine when identical dates/values match. **A screenshot containing 10–15 visible rows does not provide 250 days of price history**; collect enough history and check the resulting row count. Rows not visible in screenshots are never fabricated.

## 3. Extract locally and inspect the CSV

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-run-vision.ps1 -UploadMode none
```

This calls the local Ollama API with a structured JSON schema and temperature zero. Each OCR result is checked for ticker mismatch, invalid date, malformed prices, duplicate/conflicting dates, impossible high/low/open/close values, invalid volumes and future sessions. Conflicting screenshots **fail closed** instead of silently replacing prices.

The local result is `vision_output\SAZEW.csv`, containing exactly:

```csv
date,open,high,low,close,volume
2026-10-21,1475.0,1533.0,1475.0,1496.46,892752
```

The line above is an example grounded in a previously supplied EClear OHLC window, **not a new live quotation**. The metadata file `vision_output\SAZEW.review.json` starts with `reviewed: false`.

**Compare every extracted row manually against the OMS historical grid.** Local vision OCR can confuse `0/O`, `1/7`, decimal points, columns and years despite structural checks. A plausible-looking candle is not necessarily correct. If OCR is wrong, crop/re-capture the screen and remove the obsolete `vision_output\extractions\SAZEW__*.json` evidence files before rescanning that ticker.

## 4. Choose how Python uploads to Google Drive

### Option A — Google Desktop OAuth (direct API upload)

Use a **Desktop OAuth Client ID**, *not* the service-account key already shared with the Drive folder. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials), select the same project with the Google Drive API enabled, configure its OAuth consent screen (add your email as a test user if applicable), and create an **OAuth client ID → Desktop app**. Download its JSON to:

`psx-wealth-agent\.private\google_desktop_client.json`

Then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-run-vision.ps1 -UploadMode oauth
```

Your browser opens for Google's OAuth confirmation on the first run. The local token will be saved under `.private\google_drive_token.json` (ignored by Git). The uploader requests **Google Drive scope**, which is broad because it must access an already-existing personal My Drive folder. The code restricts its operations to exact `SYMBOL.vision.csv` and `SYMBOL.vision.review.json` filenames in your configured folder. Review Google's permission screen before authorizing.

### Option B — Google Drive for Desktop sync (no Google API credentials)

Install [Drive for Desktop](https://www.google.com/drive/download/) and make the existing **PSX Wealth Agent - OHLCV Inbox** folder available locally. Point to its real Windows folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-run-vision.ps1 -UploadMode sync -SyncFolder "G:\My Drive\PSX Wealth Agent - OHLCV Inbox"
```

**That path is an illustration**; substitute the actual Drive for Desktop folder path. The script writes two small files per ticker; Drive for Desktop handles uploading them. This is often the simplest option.

Both options upload automatically when run, but uploads are **not automatically approved as trusted trading signals**.

## 5. After verifying the screenshot rows, approve explicitly

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-run-vision.ps1 -ApproveSymbol SAZEW -UploadMode oauth
```

Or use the same command with `-UploadMode sync -SyncFolder "YOUR_ACTUAL_FOLDER"`.

The script requires you to type `VERIFIED` after manual comparison. It records the CSV's SHA-256 and approval timestamp and reuploads the **same** `SAZEW.vision.csv` alongside `SAZEW.vision.review.json`. Subsequent screenshot edits change the checksum and automatically revoke the old approval.

**Crucial:** this is approval of transcription accuracy, not investment approval or authorization to execute a trade.

## 6. Let GitHub read approved data automatically

You already created a dedicated Google Drive folder and shared it with your service account. For GitHub's **read-only** side, add these repository Actions secrets in [GitHub Secrets](https://github.com/zorays/Applications/settings/secrets/actions):

- `GDRIVE_FOLDER_ID`: `1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek`
- `GDRIVE_SERVICE_ACCOUNT_JSON`: the **entire private JSON key** of the service account that can read the shared folder.

Never paste these into code, issues, chat, or screenshots. **Viewer** sharing permission is sufficient for the GitHub importer; Editor access is not needed. This service-account JSON is **different from the Windows Desktop OAuth JSON** in Step 4.

The normal [PSX Wealth Research GitHub Action](https://github.com/zorays/Applications/actions/workflows/psx-wealth-agent.yml) continues fetching the free PSX whole-market daily feed. If the two Drive secrets are present, it additionally checks `SYMBOL.vision.csv` plus matching manually reviewed metadata. It **never** overwrites conflicting free/exchange prices; missing trading sessions are supplemented by reviewed rows. If fewer than 80 valid daily sessions exist in the combined series, the symbol remains pending history.

**Safety policy:** any series containing screenshot-derived sessions is explicitly labelled `screenshot_derived_candles_need_independent_verification` in `verdict.md/json`. You can review its technical figures, but **automatic buy and held-stock sell email triggers remain blocked** until prices are independently corroborated. That guard prevents an OCR mistake triggering a trading decision.

## 7. Optional weekday automatic local scan

After a successful manual OAuth/sync upload, register a local weekday Windows Task Scheduler action:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows-schedule-vision.ps1 -UploadMode oauth -At "16:45"
```

For Drive for Desktop, pass `-UploadMode sync -SyncFolder "YOUR_ACTUAL_FOLDER"`. The task reads screenshots that *you* intentionally placed in the input folder, not your desktop screen continuously. Your Windows computer, Google sync and Ollama must be running, and Windows Task Scheduler must have sufficient access to run your user session. The workflow runs weekdays only and requires a separate manual review/approval when new screenshots are processed.

GitHub normally runs after market close around **17:45 Pakistan time** (the schedule is not guaranteed to run exactly on time). You may also press **Actions → PSX Wealth Research → Run workflow** for a manual report after Drive synchronization.

## Troubleshooting

- **Ollama connection error:** start Ollama, run `ollama list`, check that a vision model is installed and listening on `http://127.0.0.1:11434`.
- **Empty/ambiguous table:** crop/enlarge the history grid. The system cannot infer hidden rows or unreadable years.
- **Conflicting session:** recheck both screenshots and delete wrong local evidence JSON; the exporter stops rather than guessing.
- **No Drive access:** confirm the correct Desktop OAuth credentials, Google Drive API enablement, folder access and user testing status. The service-account sharing dialog alone does NOT grant Python a user OAuth token.
- **No GitHub import:** check both Actions secrets and the `Overlay manually verified Google Drive OHLCV screenshots` step. Files named `SYMBOL.csv` are ignored by the screenshot overlay intentionally.
- **No emailed trade alert:** expected for screenshot-derived prices; independent data verification and the other fundamental/Shariah/risk safety gates remain necessary.

This integration is research assistance, **not automated advice, orders, or guaranteed returns**.
