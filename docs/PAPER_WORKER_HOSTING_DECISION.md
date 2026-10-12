# PAPER worker hosting decision: free vs truly persistent

**2026-10-11, research and readiness assessment only.** No account,
billing, deployment or production trading control changed.

## Owner's constraints

The user prefers a $0 unattended cloud worker. A **home Windows PC
that stays powered on and online on trading days is acceptable only
as a last resort**. A billing card for a "free" Google VM is acceptable
**only if there is no possibility of charges**; a regular Google Cloud
budget notification is not an enforceable zero-spend cap. Do not
attach a card, upgrade, select paid compute or otherwise incur
charges without explicit separate approval.

## Hosting comparison

| Option | Can provide healthy supervision while PAPER position is open? | Costs / limitation | Current disposition |
|---|---|---|---|
| GitHub Actions scheduled workflow | **No**. Scheduled runs are best-effort, independent containers, can start late, stop after each run, and lack a continuously monitored 120-second heartbeat | Existing free allowance useful for research/read-only audit and once-per-open baseline (if on time), **not** 24/7 stop-loss supervision | **Fails** the continuous supervisor gate |
| QuantConnect **FREE cloud** | **No live deployment nodes** in free tier, even though research notebooks and historical backtests work | Vendor explicitly lists Free = **0 live nodes**. Quant Researcher supports two node quota but paid live node subscription is separate | Free tier **not** a no-cost persistent worker for this project |
| Google Cloud e2-micro Always Free | Potentially can host an independent process if resources and uptime prove adequate, but ordinary billing budgets only warn; **not guaranteed bill-free** | Card/billing account may generate charges outside eligible limits, for egress, storage or accidental deployments. Owner condition of guaranteed $0 is **not met** by ordinary alerts | **Do not initiate card/billing** |
| Owner's Windows home PC | Possible only if always awake, connected and supervised while a position remains open; outages and automatic restarts still require independent monitoring | No separate provider compute bill, but electricity/internet, Windows sleep/reboots, ISP and power failures present risks | **Last-resort development candidate**, not a live approval |

Vendor documentation:
- QuantConnect live deployment requires a live node:
  https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/deployment
- QuantConnect resources table: Free 1 research, 1 backtest, **0 live**:
  https://www.quantconnect.com/docs/v2/cloud-platform/organizations/resources
- QuantConnect Free vs Quant Researcher:
  https://www.quantconnect.com/docs/v2/cloud-platform/organizations/tier-features
- Google Cloud Always Free eligibility and budgeting documentation
  (review current terms when a setup decision is made):
  https://cloud.google.com/free
  https://cloud.google.com/billing/docs/how-to/budgets

## Minimum truly persistent supervisor acceptance checklist

- [ ] Select and independently verify **an always-on host** that can
      poll the Alpaca **PAPER** endpoint and Neon throughout all
      periods of real PAPER position exposure.
- [ ] Worker uses **read-only monitoring credentials** to observe
      account, positions, nested orders, durable intents and equity.
      Never give it the production database owner DSN or live trading keys.
- [ ] Separate **restricted heartbeat-writer permission**, dedicated
      durable heartbeat/lease table, server-clock timestamp,
      instance identity, and monotonic sequence. A monitor reader
      independently checks age ≤120 seconds; two workers cannot both
      be assumed healthy from a duplicate instance.
- [ ] Independently verify a **failed worker detection** within
      120 seconds, including process kill, network outage, power
      loss, Neon outage, Alpaca errors and restart without private
      messages leaking into logs.
- [ ] Independently tested alerts with delivery acknowledgements
      and escalation, including orphaned DAY/expired stop legs,
      entry partial fills, out-of-sync share quantity, market
      halts and cash/position changes.
- [ ] Stop child at broker is *verified active GTC* and its
      order IDs/price/remaining qty match original durable approved
      intent; otherwise no unattended overnight position allowed.
- [ ] Full NYSE exchange holiday/early-close calendar. Opening
      baseline from 09:30–09:35 America/New_York and durable
      contribution-adjusted drawdown peak are verified across
      several sessions, with missed snapshots remaining blockers.
- [ ] Specific owner-approved first-paper-order terms, matched to
      short-lived authenticated authorization. Real-money credential
      support remains excluded.

**Existing `bot.paper_watchdog --loop` is read-only and can demonstrate
broker/API polling** while a computer runs, but it currently sets
`watchdog_heartbeat_seconds=None` explicitly because an independent
durable supervisor heartbeat has NOT been deployed. Starting
that script or running the GitHub read-only cron cannot unlock
order submission.

## Recommended next implementation without spending

1. Add a **separate heartbeat/lease migration** on a temporary
   Neon test branch and unit/integration test the read/write
   privileges. Leave production roles/table unchanged until review.
2. Create read-only supervisor health semantics and an
   independent alert/checker before treating an age as evidence
   of reliable liveness.
3. Evaluate whether a zero-billing-risk hosted worker exists
   in the user's available resources; otherwise prepare a
   clearly owner-reviewed Windows Task Scheduler/read-only
   heartbeat smoke test. No auto-execution.
4. Only after all stop/worker/risk gates have real evidence
   ask owner to approve **one exact small PAPER broker trade**,
   with separate authorization for the broker submission switch.
