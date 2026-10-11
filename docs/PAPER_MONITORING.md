# Scheduled PAPER monitor and optional notifications

**Status:** Research-only, broker READ ONLY. Alpaca order submission is
still OFF in code **and** Neon production. No money is moved or traded.

## How it runs

- GitHub Action:
  `.github/workflows/neon-paper-preflight.yml`.
- On weekdays UTC 12:00–23:59, run every 15 minutes. Covers ordinary
  U.S. market trading hours during both EST and EDT and adds extra
  early/after-market observation.
- Outside that window, check at 00:00/04:00/08:00 UTC weekdays, and
  00:00/06:00/12:00/18:00 UTC on weekends.
- Tests run first; then `bot.paper_watchdog` checks restricted SELECT-only
  Neon journal, Alpaca PAPER account, broker positions/open orders and
  nominal stop coverage, and safety controls.
- The broker's existing PAPER API and restricted Neon connection are
  securely configured in GitHub Actions. No credentials go into code,
  public logs or artifacts.
- No new database tables or write privileges are added. No heartbeat
  is written; a DB writer would need separate authorization.
- `bot.monitor_policy` classifies read-only reports as:
  - `idle`: no exposure, mandatory paper trading disabled;
  - `warning`: degraded/incomplete data that needs review;
  - `critical`: cannot verify broker/Neon, possible unprotected
    exposure, unexpected write/switch change, or other dangerous state.
- A critical incident causes the CI job to fail visibly. No broker
  write/cancel/replace calls exist anywhere in this preflight.
- All scheduled executions use **best-effort GitHub Actions cron**.
  Runs can be delayed or skipped! This is not a continuous worker,
  independent liveness system or a guarantee of stop execution.

## Optional phone alert via NTFY

This is **opt-in**. Without `PAPER_MONITOR_NTFY_TOPIC` there is no
external push notification, only the GitHub Action failure signal.

1. Install the NTFY mobile app and create a completely private,
   high-entropy topic of 32–48 random letters/numbers. The topic is
   a bearer-like secret. Do not use your name, public server name
   or a guessable phrase.
2. Subscribe to that exact topic on your phone.
3. In GitHub `Venloud/horsesdontstop`, add a new Actions repository
   secret named **`PAPER_MONITOR_NTFY_TOPIC`**, value **only the topic
   string** (not the `https://ntfy.sh/` URL).
4. Test alert delivery using synthetic/mock tests, not by placing an
   Alpaca trade or switching off safety controls.
5. The notifier sends **critical** messages only and includes a stable
   incident fingerprint and generic instructions to inspect the
   Alpaca PAPER account. No database URLs, broker secrets, bearer
   tokens or account numbers are included.

**Caveats:** Notification cooldown currently lives *in memory* for
one worker process. Each GitHub Actions job starts fresh, so repeated
critical conditions can create repeat alerts at every scheduled run.
Notifications may be missed if GitHub does not start the job or if NTFY
is unavailable. Do not depend on these for open-position safety or
automatic intervention. This does NOT implement a persistent
exactly-once outbox or failover.

## Security boundaries

- Neon role `hds_paper_monitor` is SELECT-only on the four journal
  tables, no database/schema CREATE or admin privileges.
- Before any broker query, the monitor checks PostgreSQL effective
  permissions; owner/admin/write-enabled connection is rejected.
- Alpaca client always uses the PAPER endpoint.
- Trading execution switch in code is always False. Neon DB kill
  switch must remain True and submission switch False. Any unexpected
  change is a critical monitoring incident, NOT permission to trade.
- Stocks already held in Alpaca paper manually may require emergency
  attention if the protection report is critical; do NOT have the
  monitor place corrective orders on its own.

## What this DOES NOT replace

An **independent, truly available supervisor** for paper trading with
broker-side stop/OTO order verification, intraday quote and calendar
checks, durable journal writer, broker/client order ID reconciliation,
partial fills, order or platform timeouts, persistent heartbeat,
redundant alerts, idempotent restart and explicit owner approval.

Do not turn on paper submissions based only on healthy scheduled
monitoring. A calendar-triggered workflow is an observable periodic
snapshot, not an always-on trading process.

## Verification

The initial read-only GitHub/Neon/Alpaca connection was verified in
https://github.com/Venloud/horsesdontstop/actions/runs/38114132972,
with $500 PAPER equity and zero orders. The scheduled monitoring
additions are covered by new offline tests. On merging these changes
into `main`, inspect the first full `Neon + Alpaca Paper Preflight
(Read Only)` Actions run and its safe summarized report. Continue
to use manual dispatch for diagnosis when needed.
