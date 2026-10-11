# Next milestone: Neon-connected read-only paper watchdog

**Merged into:** `main`, PR #1, commit `6375d04b749316989e907930d7994f8cbbdef869`.  
**Trading:** OFF. NO broker write API calls in this worker.

## What was built

- `bot/paper_reconcile.py`: compare the broker's positions and orders
  with Neon order intents. If orders are truncated, an intent is
  unresolved, the broker has an unmanaged order, a holding has no active
  adequately sized stop, or account status is uncertain, block readiness.
- `bot/paper_store.py`: additionally read unresolved order intents
  and bounded daily-open/historical-peak equity references using SELECTs.
  A daily starting value is only accepted if captured within the first
  five minutes of the 9:30 AM America/New_York regular session, so a
  midday loss cannot silently become the new daily starting balance.
- `bot/paper_watchdog.py`: read production journal and Alpaca **PAPER**
  account, clock, positions and open/recently closed nested orders.
  Secrets are environment-only, exceptions are redacted, and the
  output is a sanitized safety report. Code-level order submission
  is **permanently disabled in this module**, regardless of DB settings.
- `tests/test_paper_reconcile.py`,
  `tests/test_paper_watchdog.py`,
  `tests/test_paper_store_readonly.py`: no-network mocks for broker
  outage, Neon outage, unknown/duplicate IDs, too many orders, missing
  stops, insufficient partial-stop coverage, and market DST.
- `.github/workflows/neon-paper-preflight.yml`: explicitly **manual**
  GitHub Actions read-only integration run. Fails visibly if the
  `PAPER_JOURNAL_DSN` GitHub Actions repository secret is missing.
  Tests run first. It cannot place any orders.
- `Dockerfile.paper-watchdog`, `.dockerignore`: small, unprivileged,
  credential-free **image definition** for a future background process.
  An image definition is NOT a deployed always-on host.

## Verified Neon database, but secret not yet configured

Production branch: `production` (Neon branch ID
`br-restless-sunset-b5s18rj3`) in project
`misty-shadow-43891652`, database `neondb`.

On October 11, the owner approved the schema-only migration and Neon
confirmed `paper_control`, `paper_intents`,
`paper_audit_events`, and `paper_equity_snapshots`
exist on production. Kill switch is ON; submissions are OFF.
The connection used by this ChatGPT Neon tool is **not automatically
available inside GitHub Actions**. The owner subsequently configured the
GitHub Actions secret, and a real read-only connection test passed.

### Verified GitHub-to-Neon preflight — completed 2026-10-11

**Success:** The owner privately configured the restricted monitor role's
password and GitHub Actions repository secret `PAPER_JOURNAL_DSN`.
An explicitly read-only GitHub Actions integration run completed
successfully:

https://github.com/Venloud/horsesdontstop/actions/runs/38114132972

- 28 preflight-focused unit tests passed (1 nonfatal warning).
- Connected to **production** `neondb` and Alpaca **PAPER**, using secret
  values only from the CI environment.
- Log: `READ ONLY: paper equity $500.00, positions 0, open orders 0,
  blocked checks 8; ZERO order submissions.`
- The worker's `assert_monitor_only()` gate ran first; it refuses a
  Neon administrator role or a user with table write permissions.
- A sanitized preflight artifact was uploaded. There were **no** journal
  table inserts/updates, broker writes, or actual trades.
- Neon production control was separately rechecked:
  `kill_switch_active=TRUE`, `submissions_enabled=FALSE`.
- The temporary workflow push trigger used to run the integration test
  was removed in commit `47424a37d522df6c7876408362e4156aeffe68b1`.
  Subsequent integration checks remain manual via workflow_dispatch.

**No further Neon password or GitHub secret setup is needed for this
read-only test.** Never disclose or paste the DSN. This proves CI can
read the remote journal, **not** that a worker is deployed continuously
or that paper execution is ready.

## Remaining for real automatic paper trading

1. Dedicated durable `PAPER_JOURNAL_DSN` secret wired to an always-on,
   supervised cloud worker with health monitoring, not GitHub Actions
   alone. Verify real Neon TLS connection and read-only queries.
2. Separate service privileges for writer versus observer; transaction
   locks and atomic idempotent broker-order reservation in an isolated
   testing branch first. Do not enable database submissions during tests.
3. Implement proper Alpaca PAPER-only order adapter with guaranteed
   preflight of current *executable* bid/ask, correct session calendar,
   and full order reconciliation/stop-leg verification. No guarantee a
   stop triggers a particular fill.
4. Handle partial fills, network timeouts, stale price data, duplicate
   scheduler runs, orphaned positions, and alarm/kill switch failures
   without ever blindly resubmitting an order.
5. Owner must separately approve first **supervised paper trade**,
   and only later the switch to unattended paper operation.
   Never place real-money orders or automatic deposits.

Remember: profits and losses compound immediately from first
closed trade, including below $1,000. 1% risk is the baseline;
2% is an experiment and must not be silently enabled. Backtests
are not forecast profits.

## October 11 Neon role security finding (resolved)

Neon's default `create_postgres_role` produced an unsafe test role
inheriting `neon_superuser` and write permissions, which was **deleted
before use**. A different role `hds_paper_monitor` was created in
PostgreSQL without inherited administration privileges, and with **SELECT
only** on the four `paper_*` journal tables.

Role introspection verified no INSERT/UPDATE/DELETE/TRUNCATE privileges
and no Neon admin membership or CREATE rights. The owner privately set
a password for this restricted role. The successful GitHub integration
test above confirms authentication and read access now work.

The Python watcher calls `assert_monitor_only()` before broker queries
and refuses an owner/admin/write-enabled DSN even if someone adds one
by mistake. Keep this role read-only. Any future journal writer must
use a separate, narrowly scoped service role and complete a separate
review.
