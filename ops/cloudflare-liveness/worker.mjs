/**
 * Independent PAPER worker liveness receiver and cron watchdog.
 * STAGED ONLY, never reads Alpaca/Neon credentials, cannot authorize trades.
 * One D1 row per deployment; POST heartbeat every 30-90s from an external host.
 * One-minute cron can be late. It is NOT a hard 120s alert-time guarantee.
 */

const WORKER_ID = /^paper-[a-f0-9]{32}$/;
const BEAT_STATES = new Set(["read_only_poll_ok", "closed_flat_idle", "poll_failed"]);
const SLOW_OR_MISSING_MS = 120_000;
const ALARM_COOLDOWN_MS = 60 * 60 * 1000;

function validSecret(s) {
  return typeof s === "string" && /^[A-Za-z0-9_-]{32,128}$/.test(s);
}

async function compareAuth(header, secret) {
  if (!validSecret(secret) || typeof header !== "string" || !header.startsWith("Bearer ")) {
    return false;
  }
  const supplied = header.slice("Bearer ".length);
  if (!validSecret(supplied)) return false;
  const encoder = new TextEncoder();
  const [lhs, rhs] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(supplied)),
    crypto.subtle.digest("SHA-256", encoder.encode(secret)),
  ]);
  const a = new Uint8Array(lhs);
  const b = new Uint8Array(rhs);
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) mismatch |= a[i] ^ b[i];
  return mismatch === 0;
}

function response(code, text) {
  return new Response(text, {
    status: code,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

export function classifyLiveness(row, nowMs) {
  if (!row) return "missing_heartbeat";
  const age = nowMs - row.observed_ms;
  if (!Number.isFinite(age) || age < -10_000 || age > SLOW_OR_MISSING_MS) {
    return "stale_heartbeat";
  }
  if (row.status === "poll_failed") return "worker_poll_failed";
  if (row.status !== "read_only_poll_ok" && row.status !== "closed_flat_idle") {
    return "unrecognized_liveness";
  }
  // Still running != verified protective stops or permitted order entry.
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/heartbeat" || request.method !== "POST") {
      return response(404, "Not found");
    }
    if (!(await compareAuth(request.headers.get("authorization"), env.BEAT_TOKEN))) {
      return response(401, "Unauthorized");
    }
    if (!env.DB || !validSecret(env.BEAT_TOKEN)) return response(503, "Unavailable");
    const bodySize = request.headers.get("content-length");
    if (bodySize && (+bodySize > 512 || +bodySize < 0)) return response(413, "Too large");
    let data;
    try {
      const raw = await request.text();
      if (raw.length > 512) return response(413, "Too large");
      data = JSON.parse(raw);
    } catch {
      return response(400, "Malformed heartbeat");
    }
    if (!data || Array.isArray(data) || typeof data !== "object" ||
        Object.keys(data).sort().join(",") !== "sequence,status,worker_id" ||
        !WORKER_ID.test(data.worker_id) ||
        !Number.isSafeInteger(data.sequence) || data.sequence < 1 ||
        data.sequence > 2147483647 ||
        !BEAT_STATES.has(data.status)) {
      return response(400, "Invalid heartbeat");
    }
    const now = Date.now();
    try {
      const row = await env.DB.prepare(
        `INSERT INTO liveness (singleton, worker_id, sequence, status, observed_ms)
         VALUES (1, ?1, ?2, ?3, ?4)
         ON CONFLICT(singleton) DO UPDATE SET
           worker_id=excluded.worker_id, sequence=excluded.sequence,
           status=excluded.status, observed_ms=excluded.observed_ms
         WHERE (liveness.worker_id=excluded.worker_id AND excluded.sequence>liveness.sequence)
           OR (liveness.worker_id<>excluded.worker_id AND liveness.observed_ms<?5)
         RETURNING worker_id`
      ).bind(data.worker_id, data.sequence, data.status, now, now-SLOW_OR_MISSING_MS).first();
      if (!row || row.worker_id !== data.worker_id) return response(409, "Duplicate or competing worker");
      // No response body contains worker IDs, status or privileged details.
      return response(204, "");
    } catch {
      return response(503, "Liveness storage unavailable");
    }
  },

  async scheduled(_event, env) {
    if (!env.DB) throw new Error("Liveness database missing");
    let latest;
    try {
      latest = await env.DB.prepare(
        "SELECT worker_id, sequence, status, observed_ms FROM liveness WHERE singleton=1"
      ).first();
    } catch {
      throw new Error("Liveness read failed");
    }
    const now = Date.now();
    const issue = classifyLiveness(latest, now);
    if (!issue) return;
    const topic = env.NTFY_TOPIC;
    if (!validSecret(topic)) throw new Error("Private notification topic missing");
    let previous;
    try {
      previous = await env.DB.prepare(
        "SELECT code, delivered_ms FROM alarm_state WHERE singleton=1"
      ).first();
    } catch {
      throw new Error("Alert deduplication state unavailable");
    }
    if (previous && previous.code === issue &&
        now - previous.delivered_ms >= 0 &&
        now - previous.delivered_ms < ALARM_COOLDOWN_MS) return;
    // Fixed safe message only, no bearer token, worker ID, financial data or DSN.
    let delivered = false;
    try {
      const reply = await fetch("https://ntfy.sh/" + topic, {
        method: "POST",
        headers: {
          "Title": "horsesdontstop PAPER worker liveness",
          "Priority": "urgent",
          "Content-Type": "text/plain; charset=utf-8",
        },
        body: "PAPER supervisor liveness ALERT: " + issue +
          ". Check the worker host and Alpaca PAPER account. " +
          "This is NOT trading authorization.",
      });
      delivered = reply.ok;
    } catch { /* Deliberately do not reveal transport or topic in errors. */ }
    if (!delivered) throw new Error("Independent notification delivery failed");
    try {
      // Persist alert receipt ONLY after ntfy's 2xx. Duplicate alerts after
      // a crashed write are preferable to silently losing an incident.
      await env.DB.prepare(
        `INSERT INTO alarm_state(singleton, code, delivered_ms) VALUES (1, ?1, ?2)
         ON CONFLICT(singleton) DO UPDATE SET
           code=excluded.code, delivered_ms=excluded.delivered_ms`
      ).bind(issue, now).run();
    } catch {
      throw new Error("Alert delivered but receipt persistence failed");
    }
  },
};
