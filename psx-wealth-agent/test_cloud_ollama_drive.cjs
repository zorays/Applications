/* Cloud Apps Script offline validation, mocked Drive and Ollama; no API credentials. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const crypto = require("node:crypto");
const source = fs.readFileSync(__dirname + "/cloud_ollama_drive.gs", "utf8");

class FakeBlob {
  constructor(bytes) { this.bytes = Buffer.from(bytes); }
  getBytes() { return [...this.bytes]; }
  getDataAsString() { return this.bytes.toString("utf8"); }
}
let seq=0;
class FakeFile {
  constructor(name, text, mime="text/plain") {
    this.name=name; this.bytes=Buffer.from(text); this.mime=mime; this.id="id"+(++seq);
    this.stamp=new Date();
  }
  getName(){return this.name;}
  getBlob(){return new FakeBlob(this.bytes);}
  getId(){return this.id;}
  getSize(){return this.bytes.length;}
  getMimeType(){return this.mime;}
  getLastUpdated(){return this.stamp;}
  setContent(value){this.bytes=Buffer.from(value); this.stamp=new Date();return this;}
  moveTo(folder){
    for(const parent of [root,...root.folders]){
      const i=parent.files.indexOf(this);
      if(i>=0)parent.files.splice(i,1);
    }
    folder.files.push(this);
    return this;
  }
}
const iter=a=>({index:0,hasNext(){return this.index<a.length},next(){return a[this.index++]}});
class FakeFolder {
  constructor(name){this.name=name;this.files=[];this.folders=[];}
  getFiles(){return iter(this.files);}
  getFilesByName(name){return iter(this.files.filter(f=>f.name===name));}
  getFoldersByName(name){return iter(this.folders.filter(f=>f.name===name));}
  createFile(name,contents,mime){let f=new FakeFile(name,contents,mime);this.files.push(f);return f;}
  createFolder(name){let f=new FakeFolder(name);this.folders.push(f);return f;}
}
const root=new FakeFolder("PSX Wealth Agent - OHLCV Inbox");
let calls=0, triggering=0;
const sha = data=>[...crypto.createHash("sha256").update(Buffer.from(data)).digest()];
const context=vm.createContext({
  console,
  Date,
  Set,
  JSON,
  Math,
  Number,
  String,
  Object,
  Utilities:{
    formatDate(){return "2026-10-22";},
    base64Encode(bytes){return Buffer.from(bytes).toString("base64");},
    newBlob(str){return new FakeBlob(Buffer.from(str));},
    DigestAlgorithm:{SHA_256:"sha256"},
    computeDigest(alg,bytes){assert.equal(alg,"sha256"); return sha(bytes);}
  },
  PropertiesService:{getScriptProperties(){return {getProperty(key){
    if (key==="OLLAMA_API_KEY") return "DUMMY_FOR_OFFLINE_TEST_ONLY";
    if (key==="OLLAMA_CLOUD_MODEL") return "gemma4:cloud";
    return null;}}}},
  DriveApp:{getFolderById(id){assert.equal(id,"1kkQ7McRCcVDGZWHM8xrkN4-5dzMhT-ek");return root;}},
  LockService:{getScriptLock(){return {tryLock:()=>true,releaseLock:()=>{}}}},
  ScriptApp:{getProjectTriggers(){return []},newTrigger(){return {timeBased(){return {everyMinutes(n){assert.equal(n,15);return {create(){triggering++;}}}}}}}},
  UrlFetchApp:{fetch(endpoint,options){
    assert.equal(endpoint,'https://ollama.com/api/chat');
    assert.equal(options.headers["Authorization"],"Bearer DUMMY_FOR_OFFLINE_TEST_ONLY");
    assert.equal(options.method,"post");
    const data=JSON.parse(options.payload);
    assert.equal(data.model,"gemma4:cloud");
    assert.equal(data.stream,false);
    calls++;
    const imageText=Buffer.from(data.messages[0].images[0],"base64").toString();
    const first=imageText.startsWith("PAGE01");
    const changed=imageText.includes("UPDATED");
    if(imageText==="API429") return {getResponseCode(){return 429},getContentText(){return "{}"}};
    if(imageText==="BAD"){
      return {getResponseCode(){return 200},
        getContentText(){return JSON.stringify({message:{content:JSON.stringify({
          symbol:"SAZEW",visible_rows:[{date:"2026-10-22",open:10,high:9,low:8,close:10,volume:50}]
        })}})}};
    }
    const rows=[{
      date:first ? "2026-10-21" : "2026-10-20",
      open:first?"1475":"1400",high:first?"1533":"1460",
      low:first?"1475":"1390",close: first?(changed?"1501.00":"1496.46"):"1450.02",
      volume:first?"892,752":"459,393"
    }];
    return {
      getResponseCode(){return 200},
      getContentText(){return JSON.stringify({message:{role:"assistant",content:JSON.stringify({symbol:"SAZEW",visible_rows:rows})}})}
    };
  }}
});
vm.runInContext(source,context);
context.testVisionValidation();
assert.deepEqual(JSON.parse(JSON.stringify(context.validateRows_(
  [{date:"2026-05-09",open:"0",high:"0",low:"0",close:"63.85",volume:"0"}], 10))), []);
assert.deepEqual(JSON.parse(JSON.stringify(context.validateRows_(
  [{date:"2025-11-22",open:"0",high:"0",low:"0",close:"61.49",volume:"0"}], 10))), []);
assert.throws(() => context.validateRows_(
  [{date:"2026-05-11",open:"0",high:"0",low:"0",close:"63.85",volume:"0"}], 10),
  /Impossible OHLCV relationship/);

// The model once hallucinated "2023" dates from an October 2026 DSIL source.
assert.throws(() => context.validateRows_(
  [{date:"2023-10-21",open:"24.85",high:"27.29",low:"24.80",close:"27.21",volume:"6312751"}], 10),
  /Historical date outside 480-day OCR window/);
assert.throws(() => context.verifyScreenshotAnchor_("DSIL__page01.png",
  [{date:"2026-10-22"}]),
  /Screenshot date anchor mismatch/);
assert.equal(context.verifyScreenshotAnchor_("SAZEW__page01.png",
  [{date:"2026-10-22"}]), undefined);

assert.equal(context.safeVisionFailure_(new Error("Image ticker mismatch")), "Image ticker mismatch");
assert.equal(context.safeVisionFailure_(new Error("No legible full OHLC rows found")), "No legible full OHLC rows found");
assert.equal(context.safeVisionFailure_(new Error("Ollama Cloud HTTP 401")), "Ollama Cloud HTTP 401");
assert.equal(context.safeVisionFailure_(new SyntaxError("Unexpected model text")), "Ollama returned non-JSON or malformed JSON");
assert.equal(context.safeVisionFailure_(new Error("TOKEN_API_KEY_SENSITIVE")), "Unexpected processing error (Error)");
context.setupVisionCloud();
assert.equal(triggering,1);
const inbox=root.folders.find(x=>x.name==="Screenshots");
assert.ok(inbox);
inbox.files.push(new FakeFile("SAZEW__page01.png","PAGE01","image/png"));
inbox.files.push(new FakeFile("SAZEW__page02.png","PAGE02","image/png"));
const first=context.scanVisionInbox();
assert.equal(first.changed,1);
assert.equal(first.archived,2);
assert.equal(calls,2);
const archived=root.folders.find(x=>x.name==="Processed");
const rejected=root.folders.find(x=>x.name==="Needs Correction");
const proofFolder=root.folders.find(x=>x.name==="OCR Evidence");
assert.equal(inbox.files.length,0);
assert.equal(archived.files.length,2);
assert.equal(rejected.files.length,0);
assert.equal(proofFolder.files.filter(f=>f.name.endsWith(".part.json")).length,2);
const csv=root.files.find(f=>f.name==="SAZEW.vision.csv");
const meta=root.files.find(f=>f.name==="SAZEW.vision.review.json");
assert.ok(csv&&meta);
assert.equal(csv.bytes.toString().trim().split("\n").length,3);
assert.equal(JSON.parse(meta.bytes).reviewed,false);
assert.equal(JSON.parse(meta.bytes).source,"ollama_cloud_vision_from_broker_screenshots");
context.approveSymbol("SAZEW");
assert.equal(JSON.parse(meta.bytes).reviewed,true);
// Re-reading same screenshots must not spend another vision API call or reset approval.
context.scanVisionInbox();
assert.equal(calls,2);
assert.equal(JSON.parse(meta.bytes).reviewed,true);
// Changed OCR input must cause a new request and revoke the old approval.
const modified=archived.files.find(f=>f.name==="SAZEW__page01.png");
modified.setContent("PAGE01-UPDATED");
modified.moveTo(inbox);
context.scanVisionInbox();
assert.equal(calls,3);
assert.equal(JSON.parse(meta.bytes).reviewed,false);
assert.ok(csv.bytes.toString().includes("1501"));
assert.equal(archived.files.length,2);
assert.equal(inbox.files.length,0);
// Failed OHLC is quarantined ONCE; evidence retains safe error and approval revoked.
inbox.files.push(new FakeFile("SAZEW__page03.png","BAD","image/png"));
const bad=context.scanVisionInbox();
assert.equal(bad.quarantined,1);
assert.equal(rejected.files.length,1);
assert.equal(inbox.files.length,0);
assert.equal(JSON.parse(meta.bytes).reviewed,false);
assert.ok(proofFolder.files.some(f=>f.name.endsWith(".failure.json")));
// Reupload a known-bad capture: no Ollama calls are spent on it.
const beforeDuplicate=calls;
inbox.files.push(new FakeFile("SAZEW__page03.png","BAD","image/png"));
context.scanVisionInbox();
assert.equal(calls,beforeDuplicate);
assert.equal(rejected.files.length,2);
// Transient HTTP429 must NOT quarantine; later execution may retry it.
inbox.files.push(new FakeFile("SAZEW__page04.png","API429","image/png"));
const outage=context.scanVisionInbox();
assert.equal(outage.quarantined,0);
assert.equal(inbox.files.length,1);
assert.equal(rejected.files.length,2);
// An archived page remains part of the consolidated CSV, although no images
// remain in the inbox to read.
const stableCsv=csv.bytes.toString();
context.scanVisionInbox();
assert.equal(csv.bytes.toString(),stableCsv);
console.log("Cloud Ollama queue tests passed: archiving, evidence merge, OCR quarantine, duplicate suppression, transient retry and approval revocation.");
