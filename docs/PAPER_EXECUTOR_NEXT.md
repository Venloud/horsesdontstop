# PAPER journal writer is staged, but execution remains OFF

**Current date:** 2026-10-11, U.S. equity markets closed (Sunday).

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
  no DELETE, no TRUNCATE.** This role currently has **no password**.
- `bot/paper_execution_journal.py`: permission attestation rejects
  owner/admin or incomplete roles, verifies a proposed protected
  intent, locks `paper_control` with `SELECT ... FOR SHARE`,
  and atomically refuses to INSERT a unique intent while the
  kill switch is ON or the submission switch is OFF. Duplicates
  cannot create a second intent. Broker-response uncertainty can
  be marked `unknown`, never blindly retried.
- Automated GitHub Actions research tests:
  https://github.com/Venloud/horsesdontstop/actions/runs/38115650912,
  **133 passed**, one warning. Production database control remains
  `kill_switch_active = TRUE`, `submissions_enabled=FALSE` with
  zero order intents/fills. No trading submission capability is
  connected to this library yet.
- Live IEX stock market scan preview:
  https://github.com/Venloud/horsesdontstop/actions/runs/38115475872,
  **27 specialized tests passed**, Alpaca PAPER $500 equity, market
  closed, zero broker orders. Scan is automatically scheduled
  during weekdays and will skip stale IEX quotes.

## Owner's next credential step (DO NOT share secrets here)

After reviewing role identity in Neon SQL Editor with production
`neondb` selected, set a strong unique password:

```sql
ALTER ROLE hds_paper_executor
PASSWORD 'YOUR_UNIQUE_STRONG_PASSWORD';
```

Save securely, never commit or paste password in chat.
Then in Neon Connection Details, select **production**, **neondb**,
**hds_paper_executor**, and direct connection. Copy the PostgreSQL URI
to GitHub `Venloud/horsesdontstop` **Settings > Secrets and variables
> Actions > New repository secret**, with **name**
`PAPER_EXECUTOR_DSN`. This credential must NEVER replace or change the
existing read-only `PAPER_JOURNAL_DSN` secret. Nothing should switch
the Neon kill switch to OFF yet.

**Do not set this credential up blindly.** Verify the writer role
still has only the narrow grants, never Neon administrator membership.

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
