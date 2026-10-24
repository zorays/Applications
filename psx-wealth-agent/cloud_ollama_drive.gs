/**
 * PSX Wealth Agent — cloud Ollama Vision OCR (Google Apps Script).
 *
 * Google Drive Inbox/Screenshots/ -> Ollama Cloud -> Inbox/SYMBOL.vision.csv
 *                                               -> Inbox/SYMBOL.vision.review.json
 * OCR is UNREVIEWED until approveSymbol() is explicitly run.
 * GitHub reads only reviewed files and blocks automated trading alerts on OCR.
 *
 * Setup: add OLLAMA_API_KEY to Script Properties, run setupVisionCloud(),
 * then add screenshots named SYMBOL__page01.png inside Screenshots.
 * Do not include account details or personally identifying data in images.
 */
const PSX = Object.freeze({
  FOLDER_ID: '1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek',
  IMAGE_FOLDER: 'Screenshots',
  PART_FOLDER: 'OCR Evidence',
  PROCESSED_FOLDER: 'Processed',
  REJECTED_FOLDER: 'Needs Correction',
  MODEL_FALLBACK: 'gemma4:cloud',
  PKT: 'Asia/Karachi',
  MAX_IMAGE_BYTES: 5 * 1024 * 1024,
  MAX_PER_TICK: 3,
  MAX_SCREEN_ROWS: 100,
  MAX_SYMBOL_ROWS: 1000,
  // 251 trading sessions usually span 12-16 calendar months, not 3 years.
  MAX_HISTORY_CALENDAR_DAYS: 480,
  NAME_PATTERN: /^([A-Z][A-Z0-9]{1,11})__.+\.(png|jpg|jpeg|webp)$/i,
  CSV_COLS: ['date','open','high','low','close','volume'],
});
// Source anchors are confirmed by visually reading the actual screenshot.
// They are *validation gates*, not fabricated values for the vision model.
const SCREENSHOT_ANCHORS = Object.freeze({
  'DSIL__page01.png': Object.freeze({
    newest:'2026-10-22', oldest:'2026-10-06', rows:13
  })
});

// Run once as your Google account, then authorize Drive and external requests.
function setupVisionCloud() {
  const key = PropertiesService.getScriptProperties().getProperty('OLLAMA_API_KEY');
  if (!key) throw new Error('Set OLLAMA_API_KEY in Script Properties first.');
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  ensureSubfolder_(root, PSX.IMAGE_FOLDER);
  ensureSubfolder_(root, PSX.PART_FOLDER);
  ensureSubfolder_(root, PSX.PROCESSED_FOLDER);
  ensureSubfolder_(root, PSX.REJECTED_FOLDER);
  for (const trig of ScriptApp.getProjectTriggers()) {
    if (trig.getHandlerFunction() === 'scanVisionInbox') ScriptApp.deleteTrigger(trig);
  }
  ScriptApp.newTrigger('scanVisionInbox').timeBased().everyMinutes(15).create();
  console.log('Cloud OCR trigger ready. Drop cropped OHLC history screenshots into the Screenshots subfolder.');
}

// Run as needed; installable trigger runs this every 15 minutes.
function scanVisionInbox() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    console.log('Another OCR pass is in progress; skipped.');
    return;
  }
  try {
    return scanVisionInbox_();
  } finally {
    lock.releaseLock();
  }
}


/**
 * Durable image lifecycle:
 * Screenshots -> validation -> Processed (success) / Needs Correction (bad OCR).
 * All OCR evidence stays in OCR Evidence, independent of input file location.
 * Transient API/Drive failures remain queued for a later trigger.
 * Nothing is permanently deleted and NO OCR result is auto-approved.
 */
function scanVisionInbox_() {
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  const images = ensureSubfolder_(root, PSX.IMAGE_FOLDER);
  const evidence = ensureSubfolder_(root, PSX.PART_FOLDER);
  const processed = ensureSubfolder_(root, PSX.PROCESSED_FOLDER);
  const rejected = ensureSubfolder_(root, PSX.REJECTED_FOLDER);
  const indexed = [];
  const it = images.getFiles();
  while (it.hasNext()) {
    const file = it.next();
    const match = file.getName().match(PSX.NAME_PATTERN);
    if (!match) {
      console.error('Quarantining improperly named screenshot (expected SYMBOL__pageNN.png).');
      try { file.moveTo(rejected); } catch (e) { console.error('Cannot move invalid filename: ' + safeVisionFailure_(e)); }
      continue;
    }
    indexed.push({file:file, symbol:match[1].toUpperCase(),
                  stamp:file.getLastUpdated().getTime()});
    if (indexed.length > 600) throw new Error('Screenshots queue exceeds 600 files');
  }
  indexed.sort((a,b) => a.stamp - b.stamp ||
    a.file.getName().localeCompare(b.file.getName()));

  // Build a proof index ONCE; archived originals must still contribute to CSV.
  const partsBySymbol = {};
  const goodFingerprints = new Set();
  const badFingerprints = new Set();
  const proofs = evidence.getFiles();
  while (proofs.hasNext()) {
    const f = proofs.next(), name = f.getName();
    if (!/\.(part|failure)\.json$/.test(name)) continue;
    try {
      const p = JSON.parse(f.getBlob().getDataAsString('UTF-8'));
      if (!p || !/^[A-Z][A-Z0-9]{1,11}$/.test(p.symbol || '') ||
          !/^[a-f0-9]{64}$/.test(p.imageSha256 || '') ||
          typeof p.imageName !== 'string') continue;
      const fingerprint = visionFingerprint_(p.symbol, p.imageName, p.imageSha256);
      if (name.endsWith('.part.json') && Array.isArray(p.rows)) {
        (partsBySymbol[p.symbol] || (partsBySymbol[p.symbol]=[])).push(p);
        goodFingerprints.add(fingerprint);
      } else if (name.endsWith('.failure.json')) badFingerprints.add(fingerprint);
    } catch (_) {
      console.error('Ignoring malformed stored OCR evidence.');
    }
  }

  let attempted = 0, archived = 0, quarantined = 0, deferred = 0;
  const changed = new Set();
  for (const entry of indexed) {
    const file = entry.file;
    const symbol = entry.symbol;
    let bytes, sha;
    try {
      if (file.getSize() > PSX.MAX_IMAGE_BYTES) {
        quarantineFile_(file,rejected,evidence,symbol,'file_size',
                        'Screenshot exceeds size limit',null);
        quarantined++;
        continue;
      }
      bytes = file.getBlob().getBytes();
      if (bytes.length > PSX.MAX_IMAGE_BYTES) {
        quarantineFile_(file,rejected,evidence,symbol,'file_size',
                        'Screenshot exceeds size limit',null);
        quarantined++;
        continue;
      }
      sha = sha256_(bytes);
    } catch (err) {
      console.error('Deferred Drive read for ' + file.getName() + ': ' + safeVisionFailure_(err));
      deferred++;
      continue;
    }
    const fingerprint = visionFingerprint_(symbol,file.getName(),sha);
    if (goodFingerprints.has(fingerprint)) {
      try {
        file.moveTo(processed);
        archived++;
        // Recover CSV when an earlier execution saved proof but failed to consolidate.
        if (!findOne_(root,symbol + '.vision.csv')) changed.add(symbol);
      } catch (e) {
        deferred++;
        console.error('Evidence exists but archival failed: ' + safeVisionFailure_(e));
      }
      continue;
    }
    if (badFingerprints.has(fingerprint)) {
      try { file.moveTo(rejected); quarantined++; }
      catch (e) { deferred++; console.error('Known rejection move failed: ' + safeVisionFailure_(e)); }
      continue;
    }
    if (attempted >= PSX.MAX_PER_TICK) { deferred++; continue; }
    attempted++;
    let stage = 'ollama_api';
    try {
      const reply = callOllama_(bytes,file.getMimeType(),symbol,file.getName());
      stage = 'ticker_check';
      if (reply.symbol.toUpperCase() !== symbol) throw new Error('Image ticker mismatch');
      stage = 'ohlcv_validation';
      const clean = validateRows_(reply.visible_rows, PSX.MAX_SCREEN_ROWS);
      verifyScreenshotAnchor_(file.getName(),clean);
      if (!clean.length) throw new Error('No legible full OHLC rows found');
      stage = 'saving_evidence';
      const part = {
        symbol:symbol, imageId:file.getId(), imageName:file.getName(),
        imageSha256:sha, rows:clean, model:model_(),
        extractedAt:new Date().toISOString()
      };
      replaceText_(evidence,symbol + '__' + file.getId() + '.part.json',
                   JSON.stringify(part),'application/json');
      goodFingerprints.add(fingerprint);
      (partsBySymbol[symbol] || (partsBySymbol[symbol]=[])).push(part);
      changed.add(symbol);
      try { file.moveTo(processed); archived++; }
      catch (e) { deferred++; console.error('Saved evidence; move retry next run: ' + safeVisionFailure_(e)); }
      console.log('Validated ' + clean.length + ' visible candles for ' + symbol);
    } catch (err) {
      const reason = safeVisionFailure_(err);
      console.error('Image ' + file.getName() + ': ' + stage + ' — ' + reason);
      if (isPermanentVisionFailure_(stage,err)) {
        try {
          quarantineFile_(file,rejected,evidence,symbol,stage,reason,sha);
          badFingerprints.add(fingerprint);
          quarantined++;
          revokeSymbolReview_(root,symbol,'Invalid replacement screenshot requires manual review');
        } catch (e) {
          deferred++;
          console.error('Quarantine write/move failed; will retry: ' + safeVisionFailure_(e));
        }
      } else {
        deferred++;
        // Quotas and authentication failures should not consume remaining images.
        if (/429|401|403|quota|missing.*key/i.test(reason)) break;
      }
    }
  }

  // Preserve previously archived pages. New screenshots with an identical
  // page name supersede earlier OCR proof; conflicts across pages fail closed.
  for (const symbol of Object.keys(partsBySymbol)) {
    if (!findOne_(root,symbol + '.vision.csv')) changed.add(symbol);
  }
  let prepared = 0;
  for (const symbol of changed) {
    try {
      const byName = {};
      for (const p of partsBySymbol[symbol] || []) {
        const old = byName[p.imageName];
        if (!old || String(old.extractedAt || '') < String(p.extractedAt || '')) {
          byName[p.imageName] = p;
        }
      }
      const parts = Object.keys(byName).sort().map(k=>byName[k]);
      if (!parts.length) continue;
      const merged = mergeParts_(parts);
      if (!merged.length) continue;
      const csv = toCsv_(merged);
      const checksum = sha256_(Utilities.newBlob(csv,'text/csv').getBytes());
      const filename = symbol + '.vision.csv';
      const metaName = symbol + '.vision.review.json';
      const oldCsv = findOne_(root,filename);
      const oldMetaFile = findOne_(root,metaName);
      let priorMeta = null;
      if (oldMetaFile) {
        try { priorMeta = JSON.parse(oldMetaFile.getBlob().getDataAsString('UTF-8')); }
        catch (_) {}
      }
      const hashes = parts.map(p=>p.imageSha256).sort();
      const unchanged = Boolean(oldCsv && priorMeta &&
        priorMeta.csv_sha256 === checksum && priorMeta.row_count === merged.length &&
        Array.isArray(priorMeta.image_hashes) &&
        JSON.stringify(priorMeta.image_hashes.slice().sort()) === JSON.stringify(hashes));
      if (!unchanged) replaceText_(root,filename,csv,'text/csv');
      const approved = Boolean(unchanged && priorMeta.reviewed === true);
      replaceText_(root,metaName,JSON.stringify({
        symbol:symbol,source:'ollama_cloud_vision_from_broker_screenshots',
        extractor:'ollama_cloud_vision',csv_sha256:checksum,row_count:merged.length,
        reviewed:approved,reviewed_at:approved ? priorMeta.reviewed_at : null,
        image_hashes:hashes,generated_at:new Date().toISOString(),
        note:'OCR needs human row-by-row verification; never auto approve trading.'
      },null,2),'application/json');
      prepared++;
      console.log('Prepared ' + filename + ': ' + merged.length +
                  ' historical candles; reviewed=' + approved);
    } catch (e) {
      revokeSymbolReview_(root,symbol,'Evidence merge failed');
      console.error('Cannot consolidate ' + symbol + ': ' + safeVisionFailure_(e));
    }
  }
  console.log('OCR queue: incoming=' + indexed.length + ', calls=' + attempted +
    ', archived=' + archived + ', quarantined=' + quarantined +
    ', deferred=' + deferred + ', candidates=' + prepared);
  return {images:indexed.length,attempted:attempted,archived:archived,
          quarantined:quarantined,deferred:deferred,changed:prepared};
}

function visionFingerprint_(symbol,name,sha) {
  return symbol + '|' + name + '|' + sha;
}
function isPermanentVisionFailure_(stage,error) {
  if (stage === 'ticker_check' || stage === 'ohlcv_validation') return true;
  // Malformed JSON is an OCR formatting problem, not a transport failure.
  return stage === 'ollama_api' && error &&
    (error.name === 'SyntaxError' ||
     /Invalid Ollama text content|Malformed Ollama response JSON/.test(String(error.message || '')));
}
function quarantineFile_(file,folder,evidence,symbol,stage,reason,sha) {
  const record = {
    symbol:symbol,imageId:file.getId(),imageName:file.getName(),
    imageSha256:sha || '',stage:stage,reason:reason,
    failedAt:new Date().toISOString(),status:'NEEDS_HUMAN_CORRECTION'
  };
  // Failure record FIRST: retry and auditing survive a failed moveTo().
  replaceText_(evidence,symbol + '__' + file.getId() + '.failure.json',
               JSON.stringify(record,null,2),'application/json');
  file.moveTo(folder);
  console.log('Quarantined ' + file.getName() + ': ' + reason);
}
function revokeSymbolReview_(root,symbol,reason) {
  const f = findOne_(root,symbol + '.vision.review.json');
  if (!f) return;
  try {
    const meta=JSON.parse(f.getBlob().getDataAsString('UTF-8'));
    if (meta.reviewed) {
      meta.reviewed=false;
      meta.reviewed_at=null;
      meta.review_revoked_reason=reason;
      replaceText_(root,symbol + '.vision.review.json',
                   JSON.stringify(meta,null,2),'application/json');
    }
  } catch (_) { console.error('Could not revoke approval for ' + symbol); }
}

// Recheck quarantined screenshot only when the user intentionally returns a
// corrected/cropped file into Screenshots. No automatic retry of bad OCR.

// Explicit one-time user action after comparing ALL rows to EClear.
function approveSymbol(symbol) {
  symbol = String(symbol || '').toUpperCase().trim();
  if (!/^[A-Z][A-Z0-9]{1,11}$/.test(symbol)) throw new Error('Invalid PSX ticker');
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  const data = findOne_(root, symbol + '.vision.csv');
  const proof = findOne_(root, symbol + '.vision.review.json');
  if (!data || !proof) throw new Error('No OCR CSV and matching provenance to approve');
  const csv = data.getBlob().getDataAsString('UTF-8');
  const meta = JSON.parse(proof.getBlob().getDataAsString('UTF-8'));
  if (sha256_(Utilities.newBlob(csv).getBytes()) !== meta.csv_sha256) {
    throw new Error('CSV changed since generation; cannot approve');
  }
  const lines = csv.trim().split(/\r?\n/);
  if (lines.length - 1 !== meta.row_count || meta.row_count < 1) {
    throw new Error('CSV row count differs from extracted evidence');
  }
  const records = lines.slice(1).map(line=>{
    const fields = line.split(',');
    if (fields.length !== 6) throw new Error('Malformed CSV');
    const record = {};
    PSX.CSV_COLS.forEach((key,i) => record[key] = fields[i]);
    return record;
  });
  validateRows_(records, PSX.MAX_SYMBOL_ROWS);
  meta.reviewed = true;
  meta.reviewed_at = new Date().toISOString();
  replaceText_(root, symbol + '.vision.review.json', JSON.stringify(meta, null, 2), 'application/json');
  console.log(symbol + ' transcription marked manually reviewed. Trading approvals are still disabled for OCR.');
}

// Intended for initial validation with a stock's price history.
// Edit only the ticker in this function, then click Run in Apps Script.
function approveSAZEWAfterManualVerification() {
  // Do NOT invoke until you have checked each CSV price/volume against EClear.
  approveSymbol('SAZEW');
}

function callOllama_(bytes, mime, symbol, filename) {
  if (!['image/png','image/jpeg','image/webp'].includes(mime)) {
    throw new Error('Unsupported image MIME type');
  }
  const key = PropertiesService.getScriptProperties().getProperty('OLLAMA_API_KEY');
  if (!key) throw new Error('Missing Ollama Cloud API key');
  // Official Ollama hosted API accepts base64 screenshots in images[].
  const endpoint = 'https://ollama.com/api/chat';
  const anchor = SCREENSHOT_ANCHORS[filename];
  const referenceDate = Utilities.formatDate(new Date(), PSX.PKT, 'yyyy-MM-dd');
  const prompt = [
    'You are transcribing a screenshot of a Pakistan Stock Exchange OHLC History table.',
    'The screenshot is from an export captured in 2026. Read the YEAR literally. The year is 2025 or 2026 on the provided 251-session images; 2023 is definitely wrong.',
    'Today is ' + referenceDate + '. If you cannot read a date correctly, omit the whole row.',
    anchor ? 'HUMAN-VERIFIED visible table: top date is ' + anchor.newest +
             ', bottom date is ' + anchor.oldest + ', exactly ' + anchor.rows +
             ' rows. Use these only to check the displayed table, never to infer missing prices.' : '',
    'Return ONLY JSON: {"symbol":"' + symbol + '","visible_rows":[',
    '{"date":"YYYY-MM-DD","open":"0.00","high":"0.00","low":"0.00","close":"0.00","volume":"0"}',
    ']}. The numeric examples are schema placeholders, NOT data.',
    'Read ONLY the centered modal popup titled OHLC History. Ignore watchlists, bid/ask depth, the side panels and summary statistics.',
    'Read ONLY visible Date/Open/High/Low/Close/Volume rows for ' + symbol + '.',
    'For each candle verify Low <= Open,Close <= High. If any digit is unclear, omit the entire row.',
    'Exclude non-trading EClear placeholders including weekends with Open=High=Low=0, Volume=0 and a carried-forward close.',
    'Read the dates in DD-MMM-YYYY format in the FIRST column. Never transform October 2026 into October 2023.',
    'Copy each row left-to-right and align all six columns exactly; do not swap Volume with Change.',
    'Volumes are whole share quantities, not decimal fractions. Never place a Change percentage or price in Volume.',
    'If a figure conflicts with daily Low/High, reread the exact original cell and omit if still uncertain.',
    'Use exact digits as displayed and valid YYYY-MM-DD dates.',
    'Do not include Change column, chart values, statistics, account data or other symbols.',
    'Do not invent, guess, extrapolate, or fill dates hidden offscreen.',
    'Omit uncertain rows entirely. If unreadable, return {"symbol":"' + symbol + '","visible_rows":[]}.',
    'Provide no markdown, backticks, commentary, or extra keys.'
  ].join(' ');
  const body = {
    model: model_(),
    messages:[{role:'user',content:prompt,images:[Utilities.base64Encode(bytes)]}],
    stream:false,options:{temperature:0}
    // NOTE: Ollama Cloud does not currently support the local API's
    // structured-output format parameter. Validate returned JSON ourselves.
  };
  const r = UrlFetchApp.fetch(endpoint,{
    method:'post',
    contentType:'application/json',
    headers:{'Authorization':'Bearer ' + key},
    payload:JSON.stringify(body),
    muteHttpExceptions:true
  });
  if (r.getResponseCode() !== 200) {
    const err = new Error('Ollama Cloud HTTP ' + r.getResponseCode());
    err.name = r.getResponseCode()===429 ? 'QuotaExceeded' : 'OllamaCloudRequestFailed';
    throw err;
  }
  const result = JSON.parse(r.getContentText());
  let value = result && result.message && result.message.content;
  if (typeof value !== 'string' || value.length > 120000) throw new Error('Invalid Ollama text content');
  value = value.trim();
  if (value.startsWith('```')) {
    value = value.replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'').trim();
  }
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' ||
      !Array.isArray(parsed.visible_rows) || typeof parsed.symbol !== 'string') {
    throw new Error('Malformed Ollama response JSON');
  }
  return parsed;
}

/**
 * Emit only known internal error strings, never remote response text or secrets.
 * In particular, a model OCR issue should not be reported simply as "Error".
 */
function safeVisionFailure_(error) {
  const message = String(error && error.message || '');
  const known = [
    /^Ollama Cloud HTTP [0-9]{3}$/,
    /^Unsupported image MIME type$/,
    /^Missing Ollama Cloud API key$/,
    /^Image ticker mismatch$/,
    /^Invalid visible rows$/,
    /^Malformed OCR record$/,
    /^Date must be YYYY-MM-DD$/,
    /^Invalid OHLC date$/,
    /^Historical date outside 480-day OCR window$/,
    /^Screenshot date anchor mismatch$/,
    /^Nonzero weekend OHLC row$/,
    /^Ambiguous numeric value$/,
    /^Invalid numeric magnitude$/,
    /^Impossible OHLCV relationship$/,
    /^No legible full OHLC rows found$/,
    /^Invalid Ollama text content$/,
    /^Malformed Ollama response JSON$/,
    /^Conflicting duplicated date [0-9-]{10}$/,
  ];
  if (known.some(pattern => pattern.test(message))) return message;
  const errorName = String(error && error.name || 'UnknownError');
  if (errorName === 'SyntaxError') return 'Ollama returned non-JSON or malformed JSON';
  if (errorName === 'QuotaExceeded') return 'Ollama quota/HTTP 429';
  if (errorName === 'OllamaCloudRequestFailed') return /^Ollama Cloud HTTP [0-9]{3}$/.test(message)
    ? message : 'Ollama HTTP request rejected';
  if (errorName === 'Exception') return 'Google Apps Script service exception (check Executions for details)';
  return 'Unexpected processing error (' +
    errorName.replace(/[^a-zA-Z0-9_]/g, '').slice(0,40) + ')';
}

/**
 * One-shot diagnostic for the uploaded DSIL__page01.png.
 * Unlike the scheduled run, this does NOT create CSV or spend credits on 3 images.
 * Prints data-shape/count and validated status, never OCR values or credentials.
 */
function diagnoseDSILPage01() {
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  const screenshots = ensureSubfolder_(root, PSX.IMAGE_FOLDER);
  const file = findOne_(screenshots, 'DSIL__page01.png');
  if (!file) throw new Error('DSIL__page01.png is missing from Screenshots');
  console.log('Diagnostic image found. MIME=' + file.getMimeType() +
              '; bytes=' + file.getSize() + '; model=' + model_());
  try {
    const reply = callOllama_(file.getBlob().getBytes(), file.getMimeType(), 'DSIL', file.getName());
    console.log('Ollama returned symbol=' + String(reply.symbol).slice(0,15) +
                '; candidate rows=' + reply.visible_rows.length);
    if (reply.symbol.toUpperCase() !== 'DSIL') throw new Error('Image ticker mismatch');
    // Diagnose each row separately, without logging untrusted text or credentials.
    // This lets us pinpoint a misread date/high/low/open/close instead of
    // reporting that the entire 12-row response is "impossible".
    let invalid = 0;
    reply.visible_rows.forEach((row, index) => {
      try {
        validateRows_([row], 1);
      } catch (error) {
        invalid++;
        const rawDate = row && typeof row.date === 'string' ? row.date : '';
        const dateLabel = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : '[invalid date]';
        const val = key => {
          const raw = String(row && row[key] !== undefined ? row[key] : '');
          return /^\d{1,12}(?:,\d{3})*(?:\.\d{1,8})?$/.test(raw) ? raw : '[invalid]';
        };
        console.error('Row ' + (index + 1) + ' (' + dateLabel + '): ' +
          safeVisionFailure_(error) + ' — O=' + val('open') +
          ', H=' + val('high') + ', L=' + val('low') +
          ', C=' + val('close') + ', Volume=' + val('volume'));
      }
    });
    if (invalid) {
      console.error('DSIL screenshot blocked: ' + invalid +
                    ' invalid row(s). Correct/crop and rerun; do not approve OCR CSV.');
      return;
    }
    const rows = validateRows_(reply.visible_rows, PSX.MAX_SCREEN_ROWS);
    verifyScreenshotAnchor_(file.getName(), rows);
    console.log('DSIL validation passed; accepted ' + rows.length +
                ' rows. Run scanVisionInbox to write CSV.');
  } catch (error) {
    console.error('DSIL diagnostic: ' + safeVisionFailure_(error));
  }
}

function model_() {
  return PropertiesService.getScriptProperties().getProperty('OLLAMA_CLOUD_MODEL') ||
         PSX.MODEL_FALLBACK;
}
function numeric_(raw, integer) {
  const text = String(raw).trim().replace(/,/g,'');
  if (!/^\d+(?:\.\d+)?$/.test(text)) throw new Error('Ambiguous numeric value');
  const number = Number(text);
  if (!Number.isFinite(number) || (integer && (!Number.isSafeInteger(number)))) {
    throw new Error('Invalid numeric magnitude');
  }
  return number;
}
function validateRows_(rows, limit) {
  if (!Array.isArray(rows) || rows.length > limit) throw new Error('Invalid visible rows');
  const seen = {};
  const today = Utilities.formatDate(new Date(), PSX.PKT, 'yyyy-MM-dd');
  for (const row of rows) {
    if (!row || typeof row !== 'object') throw new Error('Malformed OCR record');
    const day = String(row.date || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('Date must be YYYY-MM-DD');
    const d = new Date(day + 'T00:00:00Z');
    if (isNaN(d.getTime()) || d.toISOString().slice(0,10) !== day ||
        day > today) throw new Error('Invalid OHLC date');
    const daysOld = (Date.parse(today + 'T00:00:00Z') - d.getTime()) / 86400000;
    if (daysOld > PSX.MAX_HISTORY_CALENDAR_DAYS) {
      throw new Error('Historical date outside 480-day OCR window');
    }
    const obj = {date:day};
    for (const col of PSX.CSV_COLS.slice(1)) obj[col] = numeric_(row[col], col==='volume');
    // EClear can display nontrading placeholders, including Saturday lines
    // with O=H=L=0, volume=0 and an inherited previous close. These are
    // NOT candles. Drop only all-zero rows, or weekend zero-OHL placeholders;
    // any other partial-zero/OHLC inconsistency still fails closed.
    if (PSX.CSV_COLS.slice(1).every(col=>obj[col] === 0)) continue;
    const isWeekend = [0,6].includes(d.getUTCDay());
    if (isWeekend && obj.volume === 0 &&
        obj.open === 0 && obj.high === 0 && obj.low === 0) continue;
    if (isWeekend) throw new Error('Nonzero weekend OHLC row');
    if (obj.volume < 0 || Math.min(obj.open,obj.high,obj.low,obj.close) <= 0 ||
        obj.high < Math.max(obj.open,obj.close) ||
        obj.low > Math.min(obj.open,obj.close) || obj.low > obj.high) {
      throw new Error('Impossible OHLCV relationship');
    }
    const previous = seen[day];
    if (previous && JSON.stringify(previous) !== JSON.stringify(obj)) {
      throw new Error('Conflicting duplicated date ' + day);
    }
    seen[day] = obj;
  }
  return Object.keys(seen).sort().map(k=>seen[k]);
}
/**
 * Check anchored evidence; no candidate from a cropped screenshot should
 * pass merely because each OHLC tuple is numerically possible.
 * A fully visible page has 13 source rows; dates come from verified EClear.
 */
function verifyScreenshotAnchor_(filename, rows) {
  const a = SCREENSHOT_ANCHORS[filename];
  if (!a) return;
  const dates = rows.map(row=>row.date).sort();
  if (dates.length !== a.rows ||
      dates[0] !== a.oldest ||
      dates[dates.length - 1] !== a.newest) {
    throw new Error('Screenshot date anchor mismatch');
  }
}

function mergeParts_(parts) {
  const merged = {};
  for (const p of parts) {
    const rows = validateRows_(p.rows, PSX.MAX_SCREEN_ROWS);
    for (const row of rows) {
      if (merged[row.date] && JSON.stringify(merged[row.date]) !== JSON.stringify(row)) {
        throw new Error('Conflicting OCR screenshots for ' + row.date);
      }
      merged[row.date] = row;
    }
  }
  const dates = Object.keys(merged).sort();
  if (dates.length > PSX.MAX_SYMBOL_ROWS) throw new Error('Too many OHLC sessions');
  return dates.map(d=>merged[d]);
}
function toCsv_(rows) {
  const lines = [PSX.CSV_COLS.join(',')];
  rows.forEach(row=>lines.push(PSX.CSV_COLS.map(k=>String(row[k])).join(',')));
  return lines.join('\n') + '\n';
}
function sha256_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,bytes)
    .map(b=>('0'+(b & 255).toString(16)).slice(-2)).join('');
}
function findOne_(folder, filename) {
  const it = folder.getFilesByName(filename);
  return it.hasNext() ? it.next() : null;
}
function replaceText_(folder, name, value, mime) {
  const existing = findOne_(folder, name);
  if (existing) {
    existing.setContent(value);
    return existing;
  }
  return folder.createFile(name, value, mime);
}
function ensureSubfolder_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

// Test with pure mocked/fake rows in Apps Script before using real images.
function testVisionValidation() {
  const sample = [{
    date:'2026-10-21',open:'1475.00',high:'1533.00',
    low:'1475.00',close:'1496.46',volume:'892,752'
  }];
  const x = validateRows_(sample, 100);
  if (x.length!==1 || x[0].volume!==892752) throw new Error('OCR test failed');
  const csv = toCsv_(x);
  if (csv.indexOf('2026-10-21,1475,1533,1475,1496.46,892752')<0) {
    throw new Error('CSV serialization failed');
  }
  console.log('Cloud OCR format and validation test passed.');
}
