# STAGED external PAPER worker liveness, Cloudflare Workers + D1

**October29: the existing GitHub-linked Worker deployed the merged main diagnostic route. Authenticated repeated GET returned204; missing/invalid authentication returned401; the actual Neon lease was unchanged. This is not trading readiness.**

The successful Cloudflare Workers Builds check on merge commit
`9f1f80975d79857579972460b6fc738a31d2da00` identifies deployment account
`96dc1a6a8ae1020c1c6f61530e230c09`. Wrangler login now works with Windows-keychain
storage. Correct-account management access is now verified. The existing D1
binding, both secret names/types and one-minute cron match configuration. A real
scheduled execution completed successfully. Diagnostic GETs left both D1 rows
and the Neon lease exactly unchanged while genuine supervisor beats continued.
The served JavaScript is byte-identical to the local Wrangler build; no manual
deployment is needed when those bytes match. This does not establish dead-host
alert timing or phone delivery.

The D1 list API reports `num_tables: 0`; database-info reports two application
tables, and `sqlite_schema` confirms `liveness` and `alarm_state`. Treat the list
value as inconsistent management metadata. Do not reset or recreate the database.

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
- `wrangler.toml`: existing Worker/D1 target; no API token or secret values.
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

## Historical setup procedure: do not recreate the existing resources

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


## Phase7 authenticated, non-mutating diagnostic

`GET /v1/diagnostic` uses the existing bearer authentication and performs one
SELECT-only D1 reachability check. HTTP204 proves authentication and storage
reachability only: it does not renew a heartbeat, change identity/sequence,
modify alarm receipts, or prove operational supervision. Missing authentication
returns401; unavailable storage returns503. Operational POST still requires
monotonic sequence and rejects duplicate/competing workers409.

The actual deployed endpoint returned404 on October28; authenticated malformed
heartbeat returned400 without an operational payload. The historical diagnostic
saved only HTTP_4XX; its original exact status cannot be recovered from that log.
409 is a plausible collision, not confirmed historical evidence.

Deploy this reviewed receiver to the existing Worker only after authenticated
Cloudflare deployment access is available. Preserve current Worker version,
D1 schema/binding, cron and secret bindings; do not rotate or print secrets.
No D1 schema migration is needed. Retain the previous Worker version for rollback.
Verify authenticated GET204, unauthenticated GET401 and unchanged D1 heartbeat
AND alarm rows before/after repeated probes. Verify cron alert receipts through
authorized read-only dashboard/log access. Never send a synthetic operational
beacon just to make the monitor green or treat409 as success.
