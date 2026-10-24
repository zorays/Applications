# PSX EClear screenshots → Ollama Cloud → Google Drive → GitHub

## Queue cleanup upgrade — 24 October 2026

GitHub source and offline tests now support a safe, unattended image lifecycle. The four Google Drive folders already exist inside the [PSX Wealth Agent inbox](https://drive.google.com/drive/folders/1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek):

| Folder | Purpose |
|---|---|
| [Screenshots](https://drive.google.com/drive/folders/1Swy8I2Q709D_vtXv29S8ooL6HAuO97Op) | Incoming cropped OHLCV screenshots named `SYMBOL__pageNN.png` |
| [Processed](https://drive.google.com/drive/folders/1UbGV3OeeB9owcfOrLlZt2VdYtk9ejowJ) | Valid screenshot originals archived after proof is saved |
| [Needs Correction](https://drive.google.com/drive/folders/1OatGZhRuiw71fw7khbAPW7V0u-AQbVJR) | Invalid OHLCV, impossible dates or malformed vision output; original preserved |
| [OCR Evidence](https://drive.google.com/drive/folders/1RVQIndzn5dxd5cRlVZGFM9YDUQNPHq3W) | Per-image `.part.json` and `.failure.json` evidence; NEVER clear automatically |

**Critical live activation step:** GitHub cannot change the installed `Code.gs` of your existing Google Apps Script project with the connected GitHub/Drive permissions. Open your existing **PSX Wealth Agent - Ollama Vision** project, replace the **entire Code.gs** with the updated [cloud_ollama_drive.gs](https://github.com/zorays/Applications/blob/master/psx-wealth-agent/cloud_ollama_drive.gs), and save. Your existing Script Properties (including `OLLAMA_API_KEY`) should be retained. Select **setupVisionCloud**, Run, and allow requested permissions. This reinstalls the 15-minute `scanVisionInbox` trigger. You **do not** need to Deploy as a Web App.

After activation, run `scanVisionInbox` manually to test one batch. It handles at most **3 new vision requests per run**, distributed round-robin across available symbols (for example DSIL and BNWM), so an older single-symbol backlog cannot monopolize the model. Existing valid proof files are read from OCR Evidence even if their screenshots have moved to Processed; moving images never removes their source candles from the merged CSV. Previously rejected, identical screenshots do not incur more Ollama API calls. Transport, Drive and API quota failures are deferred and remain in Screenshots for subsequent attempts, rather than being misclassified as invalid market data.

For a rejected page, open its `.failure.json` evidence file, correct the crop or source image, and move the corrected image from Needs Correction back into Screenshots. Simply reuploading identical bytes intentionally does **not** cause another paid OCR call. A fresh, corrected image must change the SHA-256 checksum.

Consolidation creates `SYMBOL.vision.csv` and `SYMBOL.vision.review.json` **in the parent inbox**. New or changed evidence is always `reviewed=false`. Manually compare each row with EClear, then run the existing `approveSymbol('TICKER')` method through a parameter-free Apps Script wrapper (for example `function approveBNWM(){approveSymbol('BNWM')}`). Only a matching approved CSV and signed-off provenance can be imported by [PSX Wealth Research](https://github.com/zorays/Applications/actions/workflows/psx-wealth-agent.yml). Scheduled GitHub research does not place orders, and OCR-only signals are not an automatic buy or sell instruction.

**What is already done:** Drive archive/quarantine folders created; GitHub source updated and mocked tests passing. **What is not yet live:** The connected tools cannot deploy Apps Script source into your existing standalone script, so the replacement-and-trigger step above remains necessary. We have not run the cloud pipeline against your private screenshots or your real Ollama key.

---

**Prior deployment note (October 23, 2026):** This paragraph describes the original setup before the queue lifecycle upgrade. Refer to the October 24 section above for the latest source and activation requirements.

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

## Troubleshooting: Apps Script says "Image DSIL__page01.png: extraction failed (Error)"

This log comes from an older version of the script which hid the actual exception. The Drive images exist, are PNGs, and are each about 332 KB, so the missing file / image-size / MIME-type checks are not the apparent cause.

1. **Temporarily pause recurring scans** in Apps Script > Triggers (alarm-clock icon) > delete the `scanVisionInbox` trigger while diagnosing. It can be recreated with `setupVisionCloud`.
2. Replace the **entire Code.gs** with the latest `cloud_ollama_drive.gs` from GitHub. Save; do not paste API keys into source.
3. In Apps Script function dropdown choose **diagnoseDSILPage01**, Run, view Execution log. This makes **one** Ollama Cloud request for the first screenshot and logs only its response structure and a safe failure reason, not your key or the full model output.
4. Interpret the outcome: HTTP 401/403 = wrong or unauthorized API key; HTTP 404 = invalid model/endpoint; HTTP 429 = quota; unexpected JSON = model did not return strict JSON; invalid OHLC/date/volume = OCR needs a better crop and/or prompt; `Image ticker mismatch` = image or model result has wrong ticker.
5. If one-page diagnostic passes, run `scanVisionInbox` manually. Expect `DSIL.vision.csv` and `DSIL.vision.review.json` with `reviewed=false`. Activate `setupVisionCloud` again only when this succeeds.
6. Never approve CSV generated by the model until visible dates/prices/volumes have been compared to EClear.
