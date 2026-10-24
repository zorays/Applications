# Native EClear OHLC History capture (no AI transcription)

EClear TradeCast 4.3.2 provides an **Inquiry → OHLC History** table with 251 BNWM records. This utility exports a **text/clipboard copy** to clean, auditable OHLCV CSV without guessing candle heights or invoking an AI vision model.

## Steps on Windows
1. In EClear TradeCast open **Inquiry → OHLC History**, select `REG`, type `BNWM`, and click **Load**. It should show **251 records found**.
2. Click inside a date/price cell in the history grid. Press **Ctrl+A**, then **Ctrl+C**. Paste into Notepad first to confirm the clipboard actually contains **all 251 rows**, not only one selected row. If your installed EClear grid does not support copying all, check the grid's right-click context menu; do not assume this method succeeded.
3. If all rows copied, download `eclear_ohlc_export.py` and run `py eclear_ohlc_export.py --symbol BNWM --clipboard` in a Windows terminal. Alternatively save the pasted clipboard as UTF-8 `.tsv` and run `py eclear_ohlc_export.py --symbol BNWM --input BNWM_clipboard.tsv`.
4. Inspect `BNWM.eclear.csv`, `BNWM.eclear.audit.json`, and `BNWM.eclear.rejected.json`. Invalid dates, zero prices, impossible OHLC ranges and conflicting duplicates are *excluded and explained*. A complete source display is not a guarantee that all 251 rows pass validation.
5. Upload the outputs to the [Chart References folder](https://drive.google.com/drive/folders/1wLbmtOn9sIlM2_gzWSI41QkpLS75YYVe) for inspection. The converter **does not automatically overwrite** `BNWM.vision.csv` or mark `reviewed=true`. Full broker-native integration into Wealth Research must use a dedicated provenance-aware import path.

Historical BNWM screenshot source: EClear TradeCast 4.3.2, Stock Hub Securities. No API token is required for clipboard conversion and the tool cannot place trades.

This is a standalone capture utility, not live software control or a proof that EClear supports multi-row copy in every installation.
