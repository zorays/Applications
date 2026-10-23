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
  MODEL_FALLBACK: 'gemma4:cloud',
  PKT: 'Asia/Karachi',
  MAX_IMAGE_BYTES: 5 * 1024 * 1024,
  MAX_PER_TICK: 3,
  MAX_SCREEN_ROWS: 100,
  MAX_SYMBOL_ROWS: 1000,
  NAME_PATTERN: /^([A-Z][A-Z0-9]{1,11})__.+\.(png|jpg|jpeg|webp)$/i,
  CSV_COLS: ['date','open','high','low','close','volume'],
});

// Run once as your Google account, then authorize Drive and external requests.
function setupVisionCloud() {
  const key = PropertiesService.getScriptProperties().getProperty('OLLAMA_API_KEY');
  if (!key) throw new Error('Set OLLAMA_API_KEY in Script Properties first.');
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  ensureSubfolder_(root, PSX.IMAGE_FOLDER);
  ensureSubfolder_(root, PSX.PART_FOLDER);
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

function scanVisionInbox_() {
  const root = DriveApp.getFolderById(PSX.FOLDER_ID);
  const images = ensureSubfolder_(root, PSX.IMAGE_FOLDER);
  const evidence = ensureSubfolder_(root, PSX.PART_FOLDER);
  const indexed = [];
  const it = images.getFiles();
  while (it.hasNext()) {
    const file = it.next();
    const match = file.getName().match(PSX.NAME_PATTERN);
    if (!match) continue;
    indexed.push({file:file, symbol:match[1].toUpperCase(),
                  stamp:file.getLastUpdated().getTime()});
    if (indexed.length > 600) {
      throw new Error('More than 600 screenshots: archive old ones before continuing.');
    }
  }
  indexed.sort((a,b) => a.stamp - b.stamp || a.file.getName().localeCompare(b.file.getName()));
  let attempted = 0;
  const changed = new Set();
  const allBySymbol = {};
  for (const entry of indexed) {
    (allBySymbol[entry.symbol] || (allBySymbol[entry.symbol] = [])).push(entry);
    const file = entry.file;
    const evidenceName = entry.symbol + '__' + file.getId() + '.part.json';
    const previousFile = findOne_(evidence, evidenceName);
    let prior = null;
    if (previousFile) {
      try { prior = JSON.parse(previousFile.getBlob().getDataAsString('UTF-8')); }
      catch (_) { prior = null; }
    }
    // Check checksum on each candidate, including files that were replaced.
    let bytes;
    try {
      if (file.getSize() > PSX.MAX_IMAGE_BYTES) {
        console.error('OCR skip oversized image: ' + file.getName());
        continue;
      }
      bytes = file.getBlob().getBytes();
      if (bytes.length > PSX.MAX_IMAGE_BYTES) continue;
    } catch (err) {
      console.error('Could not read screenshot ' + file.getName() + ': ' + String(err));
      continue;
    }
    const sha = sha256_(bytes);
    if (prior && prior.imageSha256 === sha && prior.imageName === file.getName() &&
        Array.isArray(prior.rows)) {
      continue;
    }
    if (attempted >= PSX.MAX_PER_TICK) continue;
    attempted++;
    try {
      const reply = callOllama_(bytes, file.getMimeType(), entry.symbol);
      if (reply.symbol.toUpperCase() !== entry.symbol) throw new Error('Image ticker mismatch');
      const clean = validateRows_(reply.visible_rows, PSX.MAX_SCREEN_ROWS);
      // Empty crops are not stored; image can be retried after correction.
      if (!clean.length) throw new Error('No legible full OHLC rows found');
      const part = {
        symbol: entry.symbol, imageId: file.getId(), imageName:file.getName(),
        imageSha256:sha, rows:clean, model: model_(),
        extractedAt: new Date().toISOString()
      };
      replaceText_(evidence, evidenceName, JSON.stringify(part), 'application/json');
      changed.add(entry.symbol);
      console.log('Extracted ' + clean.length + ' visible candles for ' + entry.symbol);
    } catch (err) {
      // Safe diagnostics: never log API keys, request bodies, images, or model output.
      const reason = safeVisionFailure_(err);
      console.error('Image ' + file.getName() + ': extraction failed — ' + reason);
      if (/quota|429|RESOURCE_EXHAUSTED/i.test(reason)) break;
    }
  }

  // Export one consolidated CSV per changed symbol. For unchanged symbols,
  // do not regenerate or silently revoke an existing human approval.
  for (const symbol of changed) {
    try {
      const currentImages = allBySymbol[symbol] || [];
      const parts = [];
      for (const item of currentImages) {
        const partFile = findOne_(evidence, symbol + '__' + item.file.getId() + '.part.json');
        if (!partFile) continue;
        const p = JSON.parse(partFile.getBlob().getDataAsString('UTF-8'));
        // A previously extracted file may have been replaced while a batch
        // was processing. Do not silently include mismatched data.
        const liveHash = sha256_(item.file.getBlob().getBytes());
        if (p.symbol === symbol && p.imageSha256 === liveHash &&
            p.imageName === item.file.getName() && Array.isArray(p.rows)) parts.push(p);
      }
      if (!parts.length) continue;
      const merged = mergeParts_(parts);
      if (!merged.length) continue;
      const csv = toCsv_(merged);
      const checksum = sha256_(Utilities.newBlob(csv, 'text/csv').getBytes());
      const filename = symbol + '.vision.csv';
      const metaName = symbol + '.vision.review.json';
      const oldCsv = findOne_(root, filename);
      const oldMetaFile = findOne_(root, metaName);
      let priorMeta = null;
      if (oldMetaFile) {
        try { priorMeta = JSON.parse(oldMetaFile.getBlob().getDataAsString('UTF-8')); }
        catch (_) {}
      }
      const currentHashes = parts.map(x=>x.imageSha256).sort();
      const unchanged = oldCsv && priorMeta &&
          priorMeta.csv_sha256 === checksum &&
          priorMeta.row_count === merged.length &&
          Array.isArray(priorMeta.image_hashes) &&
          JSON.stringify(priorMeta.image_hashes.slice().sort()) ===
              JSON.stringify(currentHashes);
      if (!unchanged) replaceText_(root, filename, csv, 'text/csv');
      const approved = Boolean(unchanged && priorMeta.reviewed === true);
      const metadata = {
        symbol:symbol, source:'ollama_cloud_vision_from_broker_screenshots',
        extractor:'ollama_cloud_vision', csv_sha256:checksum,
        row_count:merged.length, reviewed:approved,
        reviewed_at: approved ? priorMeta.reviewed_at : null,
        image_hashes: currentHashes,
        generated_at: new Date().toISOString(),
        note:'OCR transcription is NOT buy/sell authorization. Check every row before approval.'
      };
      replaceText_(root, metaName, JSON.stringify(metadata, null, 2), 'application/json');
      console.log('Prepared ' + filename + ': ' + merged.length +
                  ' unique rows, verified=' + approved);
    } catch (err) {
      console.error('Skipped consolidation for ' + symbol + ': ' + String(err).slice(0,180));
    }
  }
  console.log('OCR cycle: checked ' + indexed.length + ' images; inferred ' +
              attempted + ' new/changed crops; wrote ' + changed.size + ' symbol candidates.');
  return {images:indexed.length, attempted:attempted, changed:changed.size};
}

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

function callOllama_(bytes, mime, symbol) {
  if (!['image/png','image/jpeg','image/webp'].includes(mime)) {
    throw new Error('Unsupported image MIME type');
  }
  const key = PropertiesService.getScriptProperties().getProperty('OLLAMA_API_KEY');
  if (!key) throw new Error('Missing Ollama Cloud API key');
  // Official Ollama hosted API accepts base64 screenshots in images[].
  const endpoint = 'https://ollama.com/api/chat';
  const prompt = [
    'You are transcribing an image of a Pakistan Stock Exchange OHLC History table.',
    'Return ONLY JSON: {"symbol":"' + symbol + '","visible_rows":[',
    '{"date":"YYYY-MM-DD","open":"0.00","high":"0.00","low":"0.00","close":"0.00","volume":"0"}',
    ']}. The numeric examples are schema placeholders, NOT data.',
    'Read ONLY visible Date/Open/High/Low/Close/Volume rows for ' + symbol + '.',
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
    const reply = callOllama_(file.getBlob().getBytes(), file.getMimeType(), 'DSIL');
    console.log('Ollama returned symbol=' + String(reply.symbol).slice(0,15) +
                '; candidate rows=' + reply.visible_rows.length);
    if (reply.symbol.toUpperCase() !== 'DSIL') throw new Error('Image ticker mismatch');
    const rows = validateRows_(reply.visible_rows, PSX.MAX_SCREEN_ROWS);
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
    const obj = {date:day};
    for (const col of PSX.CSV_COLS.slice(1)) obj[col] = numeric_(row[col], col==='volume');
    // EClear sometimes displays zero-filled non-trading placeholders.
    // Exclude ONLY an entirely zero row; never turn it into a candle.
    if (PSX.CSV_COLS.slice(1).every(col=>obj[col] === 0)) continue;
    if ([0,6].includes(d.getUTCDay())) throw new Error('Nonzero weekend OHLC row');
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
