# Next milestone: Neon-connected read-only paper watchdog

**Branch:** `experiment/paper-neon-readonly-worker`  
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
available inside GitHub Actions**.

### One owner-only secret step required

1. Open the connected Neon Console project:
   https://console.neon.tech/ and select **production** / **neondb**.
2. Get a PostgreSQL connection string for a **dedicated least-privilege
   monitoring role**, preferred over the powerful database owner role.
   This initial watchdog needs SELECT on the four `public.paper_*`
   tables, not INSERT/UPDATE/DELETE permissions. Ask for help creating
   the monitor role if one is not available.
3. In the GitHub repo https://github.com/Venloud/horsesdontstop
   navigate to **Settings > Secrets and variables > Actions >
   New repository secret**. Name it exactly `PAPER_JOURNAL_DSN`;
   paste the full Neon PostgreSQL URI there, using `sslmode=require`.
   Do not paste it in chat, code, workflow YAML or a public issue.
4. After this branch's code is merged into `main`, use
   **Actions > Neon + Alpaca Paper Preflight (Read Only) > Run workflow**.
   It queries broker/Neon without changing any account or database
   state. The assistant can retrieve its GitHub Action logs directly;
   do not download an artifact manually unless you want to.

**Security note:** A Neon MCP connection enables ChatGPT's management
tools; it is NOT a secret-to-GitHub synchronization mechanism.

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
