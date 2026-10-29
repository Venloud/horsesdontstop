import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import worker, { classifyLiveness } from "./worker.mjs";

const TOKEN = "a".repeat(64);
const TOPIC = "b".repeat(48);
const W1 = "paper-" + "1".repeat(32);
const W2 = "paper-" + "2".repeat(32);
const savedFetch = globalThis.fetch;
const storedDateNow = Date.now;
afterEach(() => {
  globalThis.fetch = savedFetch;
  Date.now = storedDateNow;
});

class FakeD1 {
  constructor() { this.liveness = null; this.alarm = null; this.fail = false; }
  prepare(sql) {
    const store = this;
    return {
      args: [],
      bind(...args) { this.args = args; return this; },
      async first() {
        if (store.fail) throw new Error("DB password leak");
        if (sql.startsWith("INSERT INTO liveness")) {
          const [worker_id, sequence, status, observed_ms, before] = this.args;
          const prev = store.liveness;
          if (prev && !(
             (prev.worker_id === worker_id && sequence > prev.sequence) ||
             (prev.worker_id !== worker_id && prev.observed_ms < before)
          )) return null;
          store.liveness = { worker_id, sequence, status, observed_ms };
          return { worker_id };
        }
        if (sql.includes("FROM liveness")) return store.liveness;
        if (sql.includes("FROM alarm_state")) return store.alarm;
        throw new Error("Unexpected D1 SELECT");
      },
      async run() {
        if (store.fail) throw new Error("DB password leak");
        if (sql.startsWith("INSERT INTO alarm_state")) {
          const [code, delivered_ms] = this.args;
          store.alarm = { code, delivered_ms };
          return { success: true };
        }
        throw new Error("Unexpected D1 write");
      }
    };
  }
}
const env = (db = new FakeD1()) => ({DB: db, BEAT_TOKEN: TOKEN, NTFY_TOPIC: TOPIC});
function req({token=TOKEN, body={worker_id:W1,sequence:1,status:"closed_flat_idle"},
              method="POST", url="https://stage.workers.dev/v1/heartbeat"}={}) {
  return new Request(url, {method, headers:{"authorization":"Bearer "+token,
    "content-type":"application/json"}, body:JSON.stringify(body)});
}
async function post(e, attrs={}) { return worker.fetch(req(attrs),e); }

test("authenticated paper worker check-in is accepted without public status", async () => {
  Date.now = () => 1000000;
  const e=env();
  const first=await post(e);
  assert.equal(first.status,204);
  assert.equal(await first.text(),"");
  assert.equal(e.DB.liveness.status,"closed_flat_idle");
  assert.equal(e.DB.liveness.worker_id,W1);
});

test("invalid auth, blank token, other endpoints and bad JSON fail before writes", async () => {
  const e=env();
  for(const token of ["bad","B".repeat(64), ""]) {
    assert.equal((await post(e,{token})).status,401);
  }
  assert.equal((await post(e,{url:"https://stage.workers.dev/not-v1"})).status,404);
  assert.equal((await post({...e, BEAT_TOKEN:"bad"})).status,401);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:0,status:"closed_flat_idle"}})).status,400);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:1,status:"healthy_to_trade"}})).status,400);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:1,status:"closed_flat_idle",token:"leak"}})).status,400);
  assert.equal(e.DB.liveness,null);
});

test("duplicate/out-of-order and competing workers cannot refresh an active lease", async () => {
  let now=1000000; Date.now=()=>now;
  const e=env();
  assert.equal((await post(e)).status,204);
  assert.equal((await post(e)).status,409);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:0,status:"poll_failed"}})).status,400);
  assert.equal((await post(e,{body:{worker_id:W2,sequence:1,status:"closed_flat_idle"}})).status,409);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:2,status:"poll_failed"}})).status,204);
  assert.equal(e.DB.liveness.status,"poll_failed");
  now+=120001;
  assert.equal((await post(e,{body:{worker_id:W2,sequence:1,status:"closed_flat_idle"}})).status,204);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:3,status:"closed_flat_idle"}})).status,409);
});

test("storage failures return sanitized 503, not false acceptance", async () => {
  const e=env();e.DB.fail=true;
  const out=await post(e);
  assert.equal(out.status,503);
  assert.doesNotMatch(await out.text(),/password|token/i);
});

test("classify closed-flat as LIVE PROCESS only, not trade readiness",()=>{
  assert.equal(classifyLiveness(null,100000),"missing_heartbeat");
  assert.equal(classifyLiveness({observed_ms:0,status:"closed_flat_idle"},150000),"stale_heartbeat");
  assert.equal(classifyLiveness({observed_ms:100000,status:"closed_flat_idle"},100000),null);
  assert.equal(classifyLiveness({observed_ms:100000,status:"read_only_poll_ok"},100000),null);
  assert.equal(classifyLiveness({observed_ms:100000,status:"poll_failed"},100000),"worker_poll_failed");
  assert.equal(classifyLiveness({observed_ms:100000,status:"UNKNOWN"},100000),"unrecognized_liveness");
  assert.equal(classifyLiveness({observed_ms:100000,status:"closed_flat_idle"},80000),"stale_heartbeat");
});

test("cron sends fixed critical alert for missing/stale heartbeat, then deduplicates", async () => {
  let now=1000000; Date.now=()=>now;
  const e=env(); const sent=[];
  globalThis.fetch=async (url,init)=>{
    sent.push({url,init}); return {ok:true};
  };
  await worker.scheduled(null,e);
  assert.equal(sent.length,1);
  assert.equal(e.DB.alarm.code,"missing_heartbeat");
  assert.match(sent[0].init.body,/NOT trading authorization/);
  assert.ok(!sent[0].init.body.includes(TOKEN));
  await worker.scheduled(null,e);
  assert.equal(sent.length,1);
  now+=3600001;
  await worker.scheduled(null,e);
  assert.equal(sent.length,2);
  await post(e);
  await worker.scheduled(null,e);
  assert.equal(sent.length,2);
  now+=121000;
  await worker.scheduled(null,e);
  assert.equal(sent.length,3);
  assert.equal(e.DB.alarm.code,"stale_heartbeat");
});

test("poll_failed triggers independent alert immediately, even with timely beats",async()=>{
  let now=1000000; Date.now=()=>now;
  const e=env(), sent=[];
  globalThis.fetch=async (_url,init)=>{sent.push(init.body);return {ok:true}};
  assert.equal((await post(e,{body:{worker_id:W1,sequence:1,status:"poll_failed"}})).status,204);
  await worker.scheduled(null,e);
  assert.equal(sent.length,1);
  assert.match(sent[0],/worker_poll_failed/);
  now+=30000;
  await post(e,{body:{worker_id:W1,sequence:2,status:"closed_flat_idle"}});
  await worker.scheduled(null,e);
  assert.equal(sent.length,1);
});

test("notification 500 or network exception is not marked sent and retries",async()=>{
  Date.now=()=>1000000;
  const e=env();
  globalThis.fetch=async()=>({ok:false});
  await assert.rejects(worker.scheduled(null,e),/delivery failed/);
  assert.equal(e.DB.alarm,null);
  globalThis.fetch=async()=>{throw new Error("SECRET TOPIC "+TOPIC)};
  await assert.rejects(worker.scheduled(null,e),/delivery failed/);
  assert.equal(e.DB.alarm,null);
  assert.equal(e.DB.liveness,null);
});

test("missing topic and D1 failure fail closed on critical cron",async()=>{
  Date.now=()=>1000000;
  const e=env();delete e.NTFY_TOPIC;
  await assert.rejects(worker.scheduled(null,e),/topic missing/);
  e.DB.fail=true;
  await assert.rejects(worker.scheduled(null,e),/Liveness read failed/);
});


test("authenticated diagnostic repeats without changing active lease or alarms", async()=>{
  Date.now=()=>1000000;
  const e=env(); await post(e);
  const previous=structuredClone(e.DB.liveness);
  e.DB.alarm={code:"stale_heartbeat",delivered_ms:123};
  const alarm=structuredClone(e.DB.alarm);
  for(let i=0;i<3;i++){
    const r=await worker.fetch(new Request("https://stage.workers.dev/v1/diagnostic",{
      headers:{authorization:"Bearer "+TOKEN}}),e);
    assert.equal(r.status,204);assert.equal(await r.text(),"");
    assert.deepEqual(e.DB.liveness,previous);assert.deepEqual(e.DB.alarm,alarm);
  }
  assert.equal((await post(e)).status,409);
  assert.equal((await post(e,{body:{worker_id:W2,sequence:1,status:"poll_failed"}})).status,409);
  assert.equal((await post(e,{body:{worker_id:W1,sequence:2,status:"closed_flat_idle"}})).status,204);
});

test("diagnostic auth, method, storage failures cannot write or report health", async()=>{
  const e=env();
  const url="https://stage.workers.dev/v1/diagnostic";
  for(const headers of [{},{authorization:"Bearer bad"}]){
    assert.equal((await worker.fetch(new Request(url,{headers}),e)).status,401);
  }
  assert.equal((await worker.fetch(req({url}),e)).status,404);
  e.DB.fail=true;
  assert.equal((await worker.fetch(new Request(url,{headers:{authorization:"Bearer "+TOKEN}}),e)).status,503);
  assert.equal(e.DB.liveness,null);assert.equal(e.DB.alarm,null);
});
