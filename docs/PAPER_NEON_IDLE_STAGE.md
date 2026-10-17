# PAPER supervisor, optional broker-confirmed Neon idle (STAGED)

**2026-10-16. No deployment, schema migration, order authorization or
production change.** This is the implementation follow-up to the
2026-10-16 VANDAM ROUTE hosting research. Project implementation belongs
here, in HorsesDontStop. The private Vandam repo is for cross-agent
memory and documented findings only.

## Why

The READ-ONLY `bot.paper_supervisor_loop` normally does a broker +
Neon journal poll and writes a restricted database heartbeat every
30–90 seconds. Neon Free currently advertises 100 CU-hours/project
per month, while continuous polling at an *assumed* 0.25 CU
for 30 days consumes 0.25 × 24 × 30 = **180 CU-hours**,
before accounting for any other GitHub/Neon consumers. Monitoring only
21 regular 6.5-hour trading sessions is approximately 34.1 CU-hours
at the same assumed size, but actual consumption and scaling
are not yet measured. These are planning estimates, not a quota
guarantee. Official reference: https://neon.com/docs/introduction/plans .

## How, without pretending the worker is deployed

After **explicit opt-in** `--idle-neon-when-broker-closed-flat`:

1. At process startup, ALWAYS perform a full authenticated broker
   + restricted Neon read. The observer must attest a consistent
   durable journal, exactly zero broker positions, zero broker open
   orders, zero unresolved durable intents, and disabled trading
   switches. A successful restricted heartbeat pulse is also required
   before the in-process flat baseline is trusted.
2. Every subsequent tick calls the ALPACA **PAPER-only** broker
   to read its clock, all positions and all currently open orders.
   Only `clock.is_open is False` and explicit EMPTY position/order
   lists allow a tick to skip Neon entirely.
3. The skipped tick **does not touch Neon and does not write a
   heartbeat**. Its count is `neon_idle_ticks`, separate from
   `healthy_polls`. An intentionally stale heartbeat must
   NEVER be accepted as a healthy, independently supervised worker.
4. On open market, any position/open order, unknown broker response,
   API outage, worker restart, malformed journal evidence or
   heartbeat error, do a full broker + Neon read again. If that
   poll fails, report unhealthy, never assume flat.
5. Even with this flag, this worker **only observes**. It cannot
   auto-liquidate an open position, and it does not implement or prove
   a flat-by-close policy. Overnight broker exposure requires a
   continuously monitored system, not the idle path.

The default invocation has **no behavior change**. Both workflows and
the `PAPER_ORDER_SUBMISSIONS_RELEASED=False` source release stay
unchanged. There is still no production heartbeat role/secret,
a deployed always-on host, confirmed GTC protective stop lifecycle,
independent alert delivery, verified NYSE calendar, or today's
durable opening-equity baseline.

## Explicit deployment and risk constraints

- Read-only PAPER credentials ONLY. Broker order submission and
  live-money operations remain prohibited.
- Do not deploy the opt-in flag until a separate independent
  monitoring/alerting design distinguishes deliberate
  closed-flat idle from an unexpected dead supervisor.
- The market-clock state is a broker observation, NOT a complete
  NYSE holiday/early-close schedule. No trading decisions use it.
- A journal could be changed by a separate process while Neon
  is idle; that makes the cached flat baseline weaker than
  a current durable journal check. Before enabling this in
  any order-capable worker, prove exclusive writer authority and
  safe invalidation/monitoring. For now all order release gates
  remain blocked.
- No card, Oracle VM, Neon usage upgrade, SQL deployment or
  GitHub secret changes have been authorized.
- **Actual monthly CU-hour consumption and other consumers are
  unknown**. This feature alone cannot guarantee staying on Free.

## Verify in CI

`tests/test_paper_neon_idle.py` exercises real broker-request
shapes against fakes, must-full-poll startup and restarts, long idle
without heartbeat, mid-session position/order changes, outages and
unchanged default polling. Synthetic tests DO NOT verify a live host.
After separate owner approval of a safe host and read-only role:
test the restricted role on a TEMP database branch, measure actual
Neon compute usage, and test independent failed-worker alerts before
any PAPER broker order request.
