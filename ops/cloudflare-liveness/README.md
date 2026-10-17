# STAGED external PAPER worker liveness, Cloudflare Workers + D1

**STATUS: CODE ONLY. NOT DEPLOYED. NOT A BROKER WATCHDOG OR A TRADING RELEASE.**

This is a second, out-of-band signal to distinguish a crashed Python
supervisor from intentional off-hours Neon idle. A Cloudflare Worker with
D1 stores a *minimal authenticated process tick*, while an independent
one-minute Cron Trigger checks whether a new tick has arrived and optionally
posts a **critical** message to a private NTFY topic.

A beat proves only a recent HTTP request by an authorized sender. It
does **NOT** prove the broker account is flat, that protective stops are
resting, that a broker API response was real, or that trading may start.

## Project files

- `worker.mjs`: Cloudflare external liveness receiver + scheduled critical
  alert classifier. No Alpaca or Neon credentials.
- `schema.sql`: two singleton Cloudflare **D1** tables. No changes to Neon.
- `worker.test.mjs`: Node native offline tests, mock D1/HTTP, no secrets.
- `wrangler.toml`: placeholders only, no live deployment or API token.
- `../../bot/paper_external_liveness.py`: opt-in Python POST sender,
  bound to the Cloudflare workers.dev HTTPS endpoint.
- `../../bot/paper_supervisor_loop.py`: optional
  `--external-liveness` flag. `--idle-neon-when-broker-closed-flat`
  now **requires** this external liveness sink, even for tests.
  A missed/failed external POST invalidates the cached flat baseline,
  reports liveness failure and returns a nonzero bounded-worker exit code.

## Resource estimate, not a free-host promise

For **one** process ticking every 30 seconds:

| Resource | Approximate use |
|---|---:|
| Authenticated Cloudflare Worker POST requests | 2,880/day |
| Cloudflare scheduled invocations, 1-minute cron | 1,440/day |
| D1 singleton writes, excluding critical alert receipts | 2,880/day |
| D1 monitoring reads | 1,440–2,880/day |

These are within the publicized *per-account* free quotas for one dedicated
worker, **if the account has capacity left**. Other workloads, retry storms,
quotas and plan changes must be checked. Cloudflare Workers Free currently
advertises 100,000 requests/day, 5 scheduled triggers/account, while D1
currently advertises 100,000 rows written/day and 5 million read/day.
September 2026 D1 free-tier limits are actually enforced, meaning
overages **return errors** rather than guarantee uninterrupted service.
Official docs:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/wrangler/configuration/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/changelog/product/d1/

The Cron Trigger runs **about once per minute**, and 120 seconds of
staleness plus scheduler timing can produce **more than 120 seconds**
before an alert. No hard 120-second dead-man SLA is established.
Another independent heartbeat watcher/endpoint may be required, or the
120s order-release gate must remain closed. The Cloudflare worker cannot
detect an outage of its OWN infrastructure or prove that ntfy delivered
the message to the owner's phone.

## Optional future Cloudflare setup, NOT performed here

1. User must authorize Cloudflare account connection, any plan and its
   billing terms. Deploy only on a Free plan with enough remaining quota.
2. From `ops/cloudflare-liveness`, create a **separate D1 database** named
   `horsesdontstop-liveness` with Wrangler, record the resulting
   `database_id` **in the deployment config only** (not passwords).
3. Apply `schema.sql` to THAT D1 database, **not to Neon**. Verify the
   D1 binding is named `DB`, with `wrangler.toml` pointing to it.
4. Create random independent 64-character bearer key, and a different
   private 48+ character NTFY topic. Set **Cloudflare secrets**
   `BEAT_TOKEN` and `NTFY_TOPIC`, not source-code strings.
   Subscribe the owner's NTFY receiver before testing.
5. Deploy Cloudflare Worker with one-minute `* * * * *` cron.
   Verify unauthenticated/replayed beats are rejected (401/409),
   then intentionally withhold a test beat and **physically confirm**
   notification delivery. A Cloudflare HTTP 2xx alone is not proof of
   recipient delivery.
6. On a **separate, reviewed, trusted host** for the READ-ONLY Python
   supervisor, store `PAPER_EXTERNAL_LIVENESS_URL` as the new
   `https://<worker>.<account>.workers.dev/v1/heartbeat` URL and
   `PAPER_EXTERNAL_LIVENESS_TOKEN` as the private bearer key. The
   Python reporter only sends process ID, increasing sequence and
   `read_only_poll_ok`, `closed_flat_idle`, or `poll_failed`.
   It does not send positions, stops, equity, credentials or order IDs.
7. Test `python -m bot.paper_supervisor_loop --max-ticks 3
   --external-liveness` FIRST, with genuine PAPER-only monitoring and
   separately restricted Neon heartbeat/journal roles. Add the
   `--idle-neon-when-broker-closed-flat` flag **only** after the
   independent observer proves heartbeat-liveness and off-hours
   behavior. No order submission switch should be changed.

The Python host and real `psycopg` TEMP run are **not deployed** by
creating this Cloudflare config. The original Neon heartbeat may go
stale while off-hours idling. The existing independent Neon inspector
will still mark that health **CRITICAL**, by design. A separate external
`alive` signal must not automatically satisfy the original Neon
watchdog gate or create broker order permission.

## Security boundaries

- Cloudflare token is a bearer secret, so TLS and restricted environment
  variables are required. Never log or commit it.
- Worker stores only an authorized process ID, monotonic sequence,
  status and **server-received** timestamp in D1. First worker claim
  is not a healthy broker attestation.
- Atomic singleton update rejects replay and a competing **fresh**
  worker. A new worker may claim the instance only after stale timeout.
  Broker order release remains OFF regardless of stored liveness.
- A failed Cloudflare POST cannot silently permit off-hours Neon idle.
- Cron's alert receipt is written to D1 only after the NTFY endpoint
  responds with 2xx. Crashes may produce duplicate alerts, preferable
  to suppressing unconfirmed alerts.
- This is a diagnostic and escalation aid. No financial safety system
  should rely solely on free Cron Trigger timing for a strict deadline.
