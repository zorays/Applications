/**
 * PSX Wealth Agent — cloud Gemini Vision OCR (Google Apps Script).
 *
 * Google Drive Inbox/Screenshots/ -> Gemini Cloud -> Inbox/SYMBOL.vision.csv
 *                                               -> Inbox/SYMBOL.vision.review.json
 * OCR is UNREVIEWED until approveSymbol() is explicitly run.
 * GitHub reads only reviewed files and blocks automated trading alerts on OCR.
 *
 * Setup: add GEMINI_API_KEY to Script Properties, run setupVisionCloud(),
 * then add screenshots named SYMBOL__page01.png inside Screenshots.
 * Do not include account details or personally identifying data in images.
 */
const PSX = Object.freeze({
  FOLDER_ID: '1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek',
  IMAGE_FOLDER: 'Screenshots',
  PART_FOLDER: 'OCR Evidence',
  MODEL_FALLBACK: 'gemini-3.8-flash',
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
  const key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) throw new Error('Set GEMINI_API_KEY in Script Properties first.');
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
      const reply = callGemini_(bytes, file.getMimeType(), entry.symbol);
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
      // Suppress service-key contents, prompt or image data from logs.
      console.error('Image ' + file.getName() + ': extraction failed (' + err.name + ')');
      if (/quota|429|RESOURCE_EXHAUSTED/i.test(String(err))) break;
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
      const unchanged = oldCsv && priorMeta &&
          priorMeta.csv_sha256 === checksum &&
          priorMeta.row_count === merged.length;
      if (!unchanged) replaceText_(root, filename, csv, 'text/csv');
      const approved = Boolean(unchanged && priorMeta.reviewed === true);
      const metadata = {
        symbol:symbol, source:'gemini_cloud_vision_from_broker_screenshots',
        extractor:'gemini_cloud_vision', csv_sha256:checksum,
        row_count:merged.length, reviewed:approved,
        reviewed_at: approved ? priorMeta.reviewed_at : null,
        image_hashes: parts.map(x=>x.imageSha256).sort(),
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

function callGemini_(bytes, mime, symbol) {
  if (!['image/png','image/jpeg','image/webp'].includes(mime)) {
    throw new Error('Unsupported image MIME type');
  }
  const key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) throw new Error('Missing Gemini API key');
  const endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' +
                    encodeURIComponent(model_()) + ':generateContent';
  const prompt = [
    'Read ONLY the visible OHLC History table for PSX symbol ' + symbol + '.',
    'Transcribe Date, Open, High, Low, Close and Volume EXACTLY as printed.',
    'Return symbol and visible_rows in the given JSON schema.',
    'Format dates as YYYY-MM-DD, numbers as decimal strings without currency marks.',
    'Exclude market-watch rows, account details, Change column, summary min/max, chart ticks,',
    'duplicate offscreen rows and any value you cannot read confidently.',
    'Do not invent or infer missing rows or replace unclear digits. Prefer omission.',
    'If no legible OHLC History table appears, return an empty visible_rows array.'
  ].join(' ');
  const rowProps = {};
  PSX.CSV_COLS.forEach(k=>rowProps[k]={type:'STRING'});
  const body = {
    contents:[{role:'user',parts:[
      {text:prompt},
      {inlineData:{mimeType:mime,data:Utilities.base64Encode(bytes)}}
    ]}],
    generationConfig:{
      temperature:0,
      responseMimeType:'application/json',
      responseSchema:{
        type:'OBJECT',
        properties:{
          symbol:{type:'STRING'},
          visible_rows:{
            type:'ARRAY', items:{
              type:'OBJECT',properties:rowProps,required:PSX.CSV_COLS
            }
          }
        },
        required:['symbol','visible_rows']
      }
    }
  };
  const r = UrlFetchApp.fetch(endpoint,{
    method:'post',
    contentType:'application/json',
    headers:{'x-goog-api-key':key},
    payload:JSON.stringify(body),
    muteHttpExceptions:true
  });
  if (r.getResponseCode() !== 200) {
    // Keep API error body and credentials out of logs.
    const error = new Error('Gemini HTTP ' + r.getResponseCode());
    error.name = r.getResponseCode() === 429 ? 'QuotaExceeded' : 'GeminiRequestFailed';
    throw error;
  }
  const result = JSON.parse(r.getContentText());
  const parts = result.candidates && result.candidates[0] &&
    result.candidates[0].content && result.candidates[0].content.parts;
  if (!parts || !parts.length) throw new Error('Gemini returned no JSON content');
  return JSON.parse(parts.map(p=>p.text||'').join(''));
}

function model_() {
  return PropertiesService.getScriptProperties().getProperty('GEMINI_MODEL') ||
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
        day > today || [0,6].includes(d.getUTCDay())) throw new Error('Invalid OHLC date');
    const obj = {date:day};
    for (const col of PSX.CSV_COLS.slice(1)) obj[col] = numeric_(row[col], col==='volume');
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
