# Durable PAPER journal, production schema deployed (2026-10-11)

**Neon production PostgreSQL has the four schema tables, and the
read-only Python preflight successfully connected to them from GitHub
Actions on 2026-10-11. Actual paper order submissions remain disabled.
There is NO deployed persistent worker or writer connection.**

## Verified deployment

- Neon project: `misty-shadow-43891652` (`horsesdontstop`), branch:
  `production`, PostgreSQL database `neondb`.
- Neon migration ID: `a68d87f0-7a04-4b7d-a651-2c60344dde42`.
  The exact schema from `bot/paper_store.py` was prepared and checked on
  temporary branch `br-crimson-scene-b5wsv4wp`, then applied to
  production **only after explicit owner approval**.
- Neon confirmed **"Migration applied successfully to parent branch"**
  and deleted the temporary migration branch.
- Verified directly on production using Neon read-only tools:
  `paper_control`, `paper_intents`, `paper_audit_events`,
  `paper_equity_snapshots` all exist in `public`.
- Verified `paper_control.kill_switch_active = TRUE` and
  `paper_control.submissions_enabled = FALSE`.
- Verified `paper_intents = 0`, `paper_audit_events = 0`,
  `paper_equity_snapshots = 0`. These tables are intentionally empty.
- The migration proves schema deployment. A later read-only GitHub
  workflow proved Python can connect, but **not** live journal write
  behavior, durable unattended worker hosting, or broker order execution.
- GitHub Actions read-only Neon + Alpaca paper preflight:
  https://github.com/Venloud/horsesdontstop/actions/runs/38114132972 .
  **28 tests passed**, Alpaca paper equity $500, 0 positions,
  0 open orders, 8 safety blockers, **ZERO order submissions**.
- The owner configured the protected `PAPER_JOURNAL_DSN` GitHub repository
  secret for restricted role `hds_paper_monitor`; tested successful
  authentication and enforced SELECT-only checks. Secret contents were
  not put in code or logs.

## How the journal should work

- `bot/paper_store.py` provides a DB-unique `client_order_id` and a
  transactional intent reservation. A repeated reservation returns
  False: **never blindly resubmit a broker order**.
- Status transitions model `reserved`, `submitted`, `accepted`,
  `filled`, `unknown`, `rejected`, `canceled`.
- Equity/cash snapshots, an event ledger and peak-equity lookup are
  available for future drawdown and profit-compounding checks.
- The current Python PostgreSQL adapter enforces TLS and short connection
  timeouts. Mock tests have passed, but it has not been integrated with
  a live persistent worker or a real broker order adapter.

## Still required before paper trading

1. **Completed for monitoring:** Dedicated SELECT-only database role
   `hds_paper_monitor` and securely configured GitHub Actions
   `PAPER_JOURNAL_DSN`. Live CI connectivity verified. Maintain the
   restricted permissions and never print or commit the DSN.
2. **Still required for writing:** A separately scoped **writer** credential
   and real PostgreSQL transaction tests on an isolated test database
   or branch, including concurrent duplicate reservations, rollback
   and outage handling. Do not create test order intents in production.
3. Build and test paper-only broker order reconciliation and a guarded
   adapter for attached stop-loss protection. Ambiguous broker responses
   must fail closed and be reconciled by client order ID before retrying.
4. Deploy a reliable always-on monitored worker/heartbeat with alarms
   and market-hours calendar checks; GitHub cron alone is not sufficient
   to manage an open position.
5. Preserve database kill switch ON and order submissions OFF until
   the owner explicitly approves a supervised first paper order.
6. Once paper is running, evaluate 8–12 weeks of forward performance
   before considering any real-money activity.

**Compounding:** profits and losses adjust the next risk budget immediately,
including below $1,000. Monthly contributions remain optional, manual and
capped at $100, with $0 auto-deposits.

Never paste Neon connection strings, role passwords or Alpaca secrets into
chat, logs or GitHub files.
