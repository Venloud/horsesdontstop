# PAPER worker lease: staging only, trading still OFF

**2026-10-11.** Staged in a DRAFT GitHub pull request. **No
production migration, no new production secret, and no hosted
worker deployment.** Existing Alpaca PAPER client credentials,
Neon kill switch and broker order submission controls are unchanged.

## Components

- [`sql/paper_worker_heartbeat_v1.sql`](../sql/paper_worker_heartbeat_v1.sql):
  one singleton, DB-time-stamped heartbeat row with
  `worker_id`, increasing `sequence`, `observed_at` and a
  `poll_succeeded` boolean. SQL is a staged migration,
  **NOT automatically applied to production**.
- [`bot/paper_worker_lease.py`](../bot/paper_worker_lease.py):
  separate role attestation and one database-server-clock
  `INSERT .. ON CONFLICT .. UPDATE` lease. Requires narrowly scoped
  future role **`hds_paper_heartbeat`**, without any order,
  kill-switch or Neon-admin permissions. A writer's first pulse
  is deliberately unhealthy. Same worker's strictly increased
  sequence can succeed on a later poll; a competing worker
  cannot claim the lease until the prior instance is >120s
  stale. A replacement worker's first pulse stays unhealthy.
- Independent `inspect` must use existing SELECT-only
  **`hds_paper_monitor`** credentials and an explicit SELECT
  grant for the new heartbeat table; a heartbeat writer
  cannot certify its own health. Checks own DB server clock,
  worker identity and age <=120 seconds.
- [`bot/paper_supervisor_loop.py`](../bot/paper_supervisor_loop.py):
  **not installed as a service**. Once a truly persistent
  host and restricted roles exist, polls the existing
  `paper_watchdog.run_once(output="")` at an allowed
  30–90 second interval and then reports only whether the
  read-only broker+Neon observation was verified. Always
  generates a **fresh process-instance ID on restart**;
  does not transfer prior lease to another process.
  No order, cancel, replace, deposit or live-money API.
- [`tests/test_paper_worker_lease.py`](../tests/test_paper_worker_lease.py)
  and [`tests/test_paper_supervisor_loop.py`](../tests/test_paper_supervisor_loop.py)
  test crashes, competing instances, stale/future timestamps,
  role escalation, simulated broker outage and bounded
  offline host polling. No external credentials are used.

## Real PostgreSQL evidence, TEMP BRANCH ONLY

Used the already existing Neon test branch
`test-paper-entry-20261011`, ID
`br-billowing-tree-b5vuiscp` (not production).
Created the isolated `public.paper_worker_heartbeat`
table there with the staged schema, without touching
existing synthetic paper intents or production.

One SQL transaction via the Neon **management connection**
(using synthetic worker IDs, NO Alpaca calls) observed:

| Operation | Temporary PostgreSQL result |
|---|---|
| First heartbeat, `hds-temp-worker-A` sequence 1 | Row inserted, **poll_succeeded FALSE** |
| Same worker sequence 2 | Updated, **poll_succeeded TRUE** |
| Duplicate sequence 2 | **Zero returned rows**, lease did not refresh |
| Competing worker `hds-temp-worker-B` sequence 1 while fresh | **Zero returned rows**, lease not stolen |
| Independent `paper_control` read before and after | Kill switch **TRUE**, submissions **FALSE** |

**Limitations:** This test used a privileged Neon connector and
simulated heartbeats, not the *actual* restricted Python
`hds_paper_heartbeat` credentials, genuine concurrency
across connections, broker polling, a powered-on host, or
an independent production monitor. A database row marked
`poll_succeeded=True` alone cannot establish genuine broker
stop protection or continuous uptime.

## Production role provisioning safety (October 20 investigation)

**Never use Neon Console, Neon CLI `roles create`, or Neon's API
`create_postgres_role` to create the dedicated least-privilege writer.**
As documented by Neon, those creation methods automatically grant the
`neon_superuser` membership, including broad inherited data and role
privileges. An account named `hds_paper_heartbeat` is **not** safe merely
because it has the intended name. Create narrowly scoped accounts with
direct SQL, explicitly validate `pg_has_role(...,'neon_superuser','MEMBER')
=FALSE` and all object-specific access with a real role login before
using its DSN. No unexpected privilege should be tolerated.

A production role accidentally created by the Neon management API on
October 20 had excessive privileges, was immediately detected, and its
password was rotated. Console/API deletion was blocked in this session,
and SQL REVOKE/ALTER failed; its **manual deletion in production is an
outstanding security cleanup requirement**. Do not distribute or use
that role's DSN. It is not an approved restricted heartbeat writer.
Existing production trading kill remains ON and submissions remain OFF.

## Before any real worker deployment

- [ ] Review/merge the source and SQL PR separately. **Do
      not automatically apply schema to production.**
- [ ] Review and explicitly approve the heartbeat migration.
      Test the actual Python writer+monitor privileges on
      the temporary branch, including two independent clients
      and crash/outage/takeover cases.
- [ ] Provision the separate least-privilege credential
      `PAPER_HEARTBEAT_DSN` only after permissions have been
      proven. Never reuse existing `PAPER_EXECUTOR_DSN`
      or store DSNs in files/PR comments.
- [ ] Select/prove a safe persistent host, ideally no new
      provider charges, else the owner-approved **PC last
      resort** while fully awake/connected.
- [ ] Add an **independent external watchdog alert path**
      confirming health freshness and notifying failed
      polls within the required window. The loop is not
      an independent watchdog by itself.
- [ ] Test real Alpaca PAPER nested stop child GTC/qty/
      stop trigger versus durable approved terms, orphaned
      orders, broker API outages and market close behavior.
- [ ] Use validated heartbeat evidence to populate
      `PaperSafetyState.watchdog_heartbeat_seconds` ONLY
      in a separate reviewed execution-readiness adapter;
      the current read-only `paper_watchdog` intentionally
      sets it to `None`. Do not remove that block merely
      because the module exists.
- [ ] Verify valid 09:30 NY opening equity snapshot for
      each trading day, contribution-adjusted peak,
      independent daily loss/drawdown and authenticated
      one-time owner review of the exact first PAPER order.

**Safe local smoke shape only after secrets, role and host
provisioning:**

```bash
python -m bot.paper_supervisor_loop --max-ticks 3 --interval-seconds 30
```

Do not run this expecting it to pass today: the heartbeat
credential/table/grant/host are not present in production.
A future successful smoke test is still not launch approval.
No automatic submission gateway invocation exists in the worker.

**Current production:** `PAPER_ORDER_SUBMISSIONS_RELEASED=False`,
`paper_control.kill_switch_active=TRUE`,
`paper_control.submissions_enabled=FALSE`; **ZERO
broker orders placed by this change**.
