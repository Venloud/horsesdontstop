# Always-on PAPER observer and opening equity, staging only

The supervisor and equity daemon are **two distinct least-privileged Python
processes**. The former can only read broker/Neon journal and write its
restricted heartbeat. The latter can only read Alpaca PAPER and insert
time-gated equity snapshots using the restricted executor role.
Neither component has a broker submission path or flips any trade switch.

**Current state:** no production heartbeat table/role, no approved always-on
host, no externally deployed Cloudflare receiver or actual notification
receipt. This repository contains deployable service templates, NOT evidence
that the services run. The default production kill remains ON and
submissions OFF. Do not deploy using an admin Neon connection.

## Separation and startup

Install Python 3.11+, dependencies from requirements.txt, and configure an
independent secure process manager on a persistent VM with service secrets
not committed to GitHub:

* Observer: ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY,
  PAPER_JOURNAL_DSN (SELECT-only), PAPER_HEARTBEAT_DSN
  (hds_paper_heartbeat role), PAPER_MONITOR_NTFY_TOPIC
  and PAPER_EXTERNAL_LIVENESS_URL / PAPER_EXTERNAL_LIVENESS_TOKEN.
* Equity process: the same PAPER broker read credentials,
  PAPER_EXECUTOR_DSN (narrow role for time-gated equity INSERT)
  and PAPER_MONITOR_NTFY_TOPIC. **Never put executor credentials in
  the observer or Cloudflare liveness receiver.**

The observer CLI requires `--external-liveness` to avoid falsely
claiming a dead-worker alarm from an in-process notifier. Host process
failures require an **independent deployed** Cloudflare/D1 heartbeat
watcher and verified ntfy subscription. Internal broker/heartbeat/liveness
errors attempt fixed-string critical ntfy alerts with cooldown; delivery
failure is counted and never treated as success. The Cloudflare receiver
is on a separate infra service and its cron does not guarantee
notification within 120 seconds.

Use the staged service templates in `ops/systemd/` only after installing
verified restricted role credentials, deploying the external receiver
and testing a real notification on a private device. Do not enable
`--idle-neon-when-broker-closed-flat` without a detailed risk review.

## Opening equity

The equity process checks every 15 seconds. It writes at most one actual
broker-observed row during **09:30 to 09:34:59 New York** on weekdays,
and the journal rechecks the **database server** time before INSERT.
Between 09:35 and 09:36:59, if the broker market is open it checks the
durable opening row and alerts if missing; there is no late backfill.
A missing baseline blocks all execution. The daemon cannot alone
attest complete historical equity or audited external cash flows.
A continuously running service can still fail due to provider outages,
host failure, credentials, or early calendar changes.

## Runtime verification still required

1. Provision production heartbeat schema and restricted accounts only
   after separate approval, test the actual roles and two independent
   PostgreSQL sessions, keep kill ON and submissions OFF.
2. Deploy persistent observer and equity collector separately, verify
   systemd status/restarts, real broker READs and Neon lease.
3. Deploy Cloudflare/D1 liveness watcher, subscribe to private ntfy,
   deliberately terminate observer and verify an alert arrives.
4. Force a broker/Neon read failure, validate sanitized alert and
   heartbeat failure, including alert-transport failure handling.
5. Observe a real trading-day opening record and independent SELECT.
   Check holiday, DST, delayed request, restart and duplicate behaviour.
6. Verify broker stop child with exact filled position, nested linked
   OTO stop ID, price, quantity, active status and **GTC time-in-force**.
   No genuine protective order has yet been placed.
7. Keep `PAPER_ORDER_SUBMISSIONS_RELEASED=False`, production kill ON and
   Neon submissions OFF until risk, market calendar, authorization and
   actual post-fill stop proofs are completed.

CI covers synthetic simulations, not uptime, vendor availability or
actual broker execution.
