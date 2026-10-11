# PAPER journal writer is staged, but execution remains OFF

**Verified on:** 2026-10-11. U.S. equity markets closed (Sunday).

## Verified completed
- Neon production project `misty-shadow-43891652`, branch
  `production`, database `neondb`: four existing `paper_*` journal tables.
- Existing `hds_paper_monitor` role is SELECT-only and its GitHub
  Actions `PAPER_JOURNAL_DSN` secret is verified with a live
  Alpaca PAPER read-only connection.
- Separate SQL-created restricted role `hds_paper_executor`, with no
  `neon_superuser` membership, no CREATEROLE/CREATEDB/REPLICATION,
  NOINHERIT, SELECT on `paper_*`, INSERT only on
  `paper_intents`, `paper_audit_events` and
  `paper_equity_snapshots`, column-scoped UPDATE only on status,
  broker_order_id and updated_at for `paper_intents`, and sequence
  USAGE for audit/snapshot IDs. **No UPDATE of `paper_control`,
  no DELETE, no TRUNCATE.** This role now has a privately configured password, and its independent GitHub Actions connection has passed.
- `bot/paper_execution_journal.py`: permission attestation rejects
  owner/admin or incomplete roles, verifies a proposed protected
  intent, locks `paper_control` with `SELECT ... FOR SHARE`,
  and atomically refuses to INSERT a unique intent while the
  kill switch is ON or the submission switch is OFF. A database
  transaction advisory lock also serializes competing run attempts,
  and a one-intent-per-New-York-trading-day constraint blocks
  overlapping buys of two different tickers. Duplicates cannot
  create a second intent. Broker-response uncertainty can
  be marked `unknown`, never blindly retried.
- Automated GitHub Actions research tests:
  https://github.com/Venloud/horsesdontstop/actions/runs/38115779024,
  **134 passed**, one warning. Production database control remains
  `kill_switch_active = TRUE`, `submissions_enabled=FALSE` with
  zero order intents/fills. No trading submission capability is
  connected to this library yet.
- Live IEX stock market scan preview:
  https://github.com/Venloud/horsesdontstop/actions/runs/38115475872,
  **27 specialized tests passed**, Alpaca PAPER $500 equity, market
  closed, zero broker orders. Scan is automatically scheduled
  during weekdays and will skip stale IEX quotes.

## Executor credential connection verified — completed

Owner privately configured `hds_paper_executor` password in Neon
production and placed its direct PostgreSQL URI in GitHub Actions
`PAPER_EXECUTOR_DSN`. The original secret had the WRONG role,
so initial read-only CI integration failed. After the owner
corrected the secret, GitHub verified the intended role and
database, restricted PostgreSQL permissions, and locked controls:

**Successful CI read-only executor test:**
https://github.com/Venloud/horsesdontstop/actions/runs/38118901207

- **30 focused tests passed**.
- `PAPER EXECUTOR IDENTITY CHECK PASSED`:
  role `hds_paper_executor`, database `neondb`.
- `PAPER EXECUTOR READ-ONLY CHECK PASSED`:
  `kill_switch_active=True`, `submissions_enabled=False`,
  `intents=0`, `audits=0`, `equity_snapshots=0`.
- **ZERO PostgreSQL writes, ZERO broker orders**.
- The workflow was returned to **manual-only** after successful
  verification, commit `14a72f114310858ef8971b2c70f2f2f1940a25fb`.
- Code remains in `bot/paper_executor_preflight.py` and
  `bot/paper_executor_role_check.py`; the credentials are
  never printed or committed.

**No further password setup or GitHub secret update is needed.**
Do not replace the existing independent observer
`PAPER_JOURNAL_DSN` secret.

## Not yet implemented or authorized

- Actually calling Alpaca `TradingClient.submit_order()` in a
  supervised worker after risk checks. No orders have been submitted.
- A persistent always-on cloud worker, reliable independent heartbeat
  and alerts, recovering order IDs after broker/network timeouts,
  verifying attached OTO stop legs during and after fills, market
  holiday/DST, gap control and cash/position reconciliation.
- Authenticated writer integration with an isolated test database and
  transactional outage/parallel-reservation tests against **real**
  Postgres. Offline tests alone don't prove database transaction
  behavior.
- Explicit owner approval of a **specific small supervised PAPER
  order** after receiving and validating a fresh live IEX quote,
  proposed symbol, quantity, maximum buy limit and stop-loss price.
  Suggested first supervised risk budget remains 1% of the latest
  $500 virtual equity or about $5 nominal stop-distance risk. A stop
  can fill worse during a gap; losses are not guaranteed capped.
- Independent forward performance validation 8–12 weeks after actual
  broker paper fills, not just historical backtests.

Neon Functions are Node.js/TypeScript, whereas the current trading
bot is Python. GitHub cron and Neon scheduled functions are
**best-effort snapshots**, not a guaranteed 24/7 transaction manager
for open trades. Free reliable worker hosting is still unresolved.

**No live money, automatic deposits or real-money credentials.**
