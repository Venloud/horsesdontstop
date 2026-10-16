# PAPER worker hosting decision: free vs truly persistent

**2026-10-11, research and readiness assessment only.** No account,
billing, deployment or production trading control changed.

**2026-10-16 update: docs only; facts re-verified against official
pages.** No account, billing, deployment or provisioning change.

## Owner's constraints

The user prefers a $0 unattended cloud worker. A **home Windows PC
that stays powered on and online on trading days is acceptable only
as a last resort**. A billing card for a "free" Google VM is acceptable
**only if there is no possibility of charges**; a regular Google Cloud
budget notification is not an enforceable zero-spend cap. Do not
attach a card, upgrade, select paid compute or otherwise incur
charges without explicit separate approval.

Oracle's sign-up page says most users need a credit card and "Your
credit card will not be charged unless you upgrade your account"; this
does NOT pre-approve sign-up, which still needs separate explicit owner
approval.

## Hosting comparison

| Option | Can provide healthy supervision while PAPER position is open? | Costs / limitation | Current disposition |
|---|---|---|---|
| GitHub Actions scheduled workflow | **No**. Scheduled runs are best-effort, independent containers, can start late, stop after each run, and lack a continuously monitored 120-second heartbeat. Schedule interval is 5 min minimum; high load can delay runs and drop queued jobs; schedules in public repos auto-disable after 60 days without activity; each GitHub-hosted job has a 6 h maximum | Existing free allowance useful for research/read-only audit and once-per-open baseline (if on time), **not** 24/7 stop-loss supervision | **Fails** the continuous supervisor gate |
| QuantConnect **FREE cloud** | **No live deployment nodes** in free tier, even though research notebooks and historical backtests work | Vendor explicitly lists Free = **0 live nodes**. Quant Researcher supports two node quota but paid live node subscription is separate | Free tier **not** a no-cost persistent worker for this project |
| Google Cloud e2-micro Always Free | Potentially can host an independent process if resources and uptime prove adequate, but ordinary billing budgets only warn; **not guaranteed bill-free** | Card/billing account may generate charges outside eligible limits, for egress, storage or accidental deployments. Owner condition of guaranteed $0 is **not met** by ordinary alerts | **Do not initiate card/billing** |
| Oracle Cloud Always Free | Potentially can host an independent process if capacity and uptime prove adequate | Up to 2× VM.Standard.E2.1.Micro (1 GB each), or A1.Flex allowance of 1,500 OCPU-h + 9,000 GB-h/month (≈2 OCPU/12 GB); home region only; "out of host capacity" is possible. Most users need a card; Oracle says it is not charged unless upgraded. Oracle may reclaim instances it deems idle when, during a 7-day period, p95 CPU <20%, network <20%, and memory <20% (A1 only) all hold. At Free Trial end, if the tenancy has more A1 instances provisioned than the Always Free allowance (2 OCPU / 12 GB total), Oracle disables **all** existing A1 instances, then deletes them after 30 days unless upgraded. A separate host does not fix the Neon budget | **Candidate; owner decision pending; never upgrade** |
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
- Neon plans and Free compute allowance:
  https://neon.com/docs/introduction/plans
- Neon scale-to-zero behavior:
  https://neon.com/docs/introduction/scale-to-zero
- GitHub Actions scheduled workflow events:
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- GitHub Actions limits:
  https://docs.github.com/en/actions/reference/limits
- Oracle Cloud Free Tier sign-up and terms:
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- Oracle Always Free resources:
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## Neon Free compute budget

Neon Free includes 100 CU-h/project/month (≈400 h at 0.25 CU).
Scale-to-zero is always on for Free with a fixed 5-min idle timeout;
a query reactivates the compute. When CU-h are exhausted, compute is
"suspended until the next billing period or until you upgrade"; a
suspended compute is not billed; Neon states "None of these limits
delete your data."

While running continuously, the 30–90 s supervisor
(`bot/paper_supervisor_loop.py`) and 60 s watchdog would never let Neon
idle for 5 min. The watchdog image (`Dockerfile.paper-watchdog`) is not
deployed.

Estimate (assumes fixed 0.25 CU and a 30-day month):
- 24/7: 0.25 × 24 × 30 = 180 CU-h, exceeding the allowance by 80;
  compute would be suspended around day 16–17.
- Market hours: ≈0.25 × 21 × 6.5 ≈ 34 CU-h.

Other consumers, including GitHub workflows and other branches, share
the project budget. Consequence: **24/7 polling on Neon Free is not
viable.** Options and non-options:
- (a) Gate all recurring Neon reads and heartbeat writes to exposure
  windows, plus a flat-by-close rule.
- (b) Moving only the heartbeat off Neon does NOT fix the budget,
  because each supervisor tick still reads the Neon journal.
- (c) Relocating or caching the journal reads off Neon is a separate
  design proposal, NOT a ready option. It needs explicit review of the
  authoritative source of truth, freshness guarantees, and fail-closed
  behaviour (stale or missing data must block, never report healthy)
  before it can be considered.
- (d) Use paid Neon (needs explicit approval).

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
- [ ] Verify the actual Neon compute size/autoscale and monthly CU-h
      usage across all consumers, and that the planned polling fits
      within 100 CU-h with margin.
- [ ] If Oracle is chosen, observe utilization against the reclamation
      criteria and test recovery from reclamation/reboot.

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
3. Evaluate Oracle Always Free as a hosted worker candidate, subject
   to separate owner approval of card sign-up and never upgrading;
   otherwise prepare a clearly owner-reviewed Windows Task
   Scheduler/read-only heartbeat smoke test. The Windows PC remains
   the last resort. No auto-execution.
4. Only after all stop/worker/risk gates have real evidence
   ask owner to approve **one exact small PAPER broker trade**,
   with separate authorization for the broker submission switch.
5. Separately design and review an exposure-window gate on all
   recurring Neon reads and heartbeat writes as a code change in its
   own PR.
