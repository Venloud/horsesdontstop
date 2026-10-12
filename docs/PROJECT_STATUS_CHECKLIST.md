# Project $10K / horsesdontstop: current build checklist

**Last verified: 2026-10-11 (UTC).** **Source of truth for current state.**
This file is a checkable engineering handoff, not permission to submit
stock orders. For complete chronology, refer to private
[VanDam handoff](https://github.com/Venloud/claudevandam/blob/main/HORSESDONTSTOP_PROJECT_HANDOFF.md)
and [full build log](https://github.com/Venloud/claudevandam/blob/main/HORSESDONTSTOP_FULL_BUILD_LOG.md).

## Critical current status

- **Broker:** Alpaca **PAPER** account, last independently observed
  $500 virtual equity/cash, zero orders and zero positions. These are
  last-read values, **not** a claim of a continually refreshed balance.
- **Neon production:** project `misty-shadow-43891652`, branch
  `production` / `br-restless-sunset-b5s18rj3`, database `neondb`.
  Verified **`kill_switch_active = TRUE`**,
  **`submissions_enabled = FALSE`**, **0 intent rows and 0 snapshots**.
  Neither production switch has been changed.
- **Market data:** IEX real last bid/ask query path works, but IEX is
  **not consolidated SIP/NBBO**. Scheduled market scanner does **not**
  submit orders.
- **Execution:** `bot/paper_supervised_execution.py` exists on
  **`main`** after [PR #7](https://github.com/Venloud/horsesdontstop/pull/7), and contains a `submit_order` call that is **hard-disabled**
  by `PAPER_ORDER_SUBMISSIONS_RELEASED = False`. No production
  workflow invokes it. This is a *staged adapter*, **NOT** a running bot.
- **Monitoring:** GitHub Actions best-effort cron is running as a
  scanner/observer; NOT a durable always-on supervisor. No healthy
  independent worker heartbeat.
- **First supervised paper order:** **NOT authorized by a specific
  quote-backed symbol/shares/limit/stop approval**, and not ready
  because the live stop-leg lifecycle and supervisor are unproven.
- **Real-money orders, deposits and withdrawals:** **NONE**, prohibited
  without a separate explicit authorization.

## Goals and immutable owner decisions

- [x] $500 **virtual** Alpaca account; aspirational $10,000 **total
      equity**, not $10,000 profit.
- [x] Immediate compounding of realized gains/losses from the **first
      closed trade**, including balances **under $1,000**.
- [x] Initial **1% of current equity** planned stop-distance risk per
      trade (about $5 at $500); 2% is **historical research only**.
- [x] Whole-share/cash constraints and no unapproved margin trades.
- [x] Owner may manually contribute at most $100 per calendar month
      including loss replacement; **never auto-deposit**.
- [x] Paper stocks using real market data; PC-off automation preferred,
      free/low-cost services, no paid tier without asking.
- [x] Proposed hard risk gates: **3% daily loss** and **15% equity
      peak drawdown**, fail closed if baseline is missing.
- [x] No forced exit simply because profit reaches +20%; no assumed
      guaranteed stop fill.
- [x] Any first actual paper trade requires reviewing its **specific**
      ticker, size, capped entry, stop and live market state.

## Phase 0 research preregistration (owner-approved, merged 2026-10-11)

- [x] Owner approved **seven** candidates including `overnight_drift` and the **exact 21 parameter variants**: [`docs/PREREGISTRATION.md`](PREREGISTRATION.md), merged via [PR #9](https://github.com/Venloud/horsesdontstop/pull/9).
- [x] Frozen **1% maximum risk for order-placing**, **15% historical max drawdown**, **30bps adverse slippage per side**, **60bps-per-side stress**, **300 sufficiently independent events** where possible, otherwise **10-year low-frequency exception**. No scored Phase 3 runs yet.
- [x] Frozen **same actual deposits on the same dates** for active strategy vs index benchmark; do not invent $100/mo hypothetical funding. Alternative "tie with lower drawdown" is not an allowed pass until a dated owner-approved quantitative rule exists.
- [x] Recovered and archived **80 actual previously inspected exploratory trials** from [original GitHub run 38108710763](https://github.com/Venloud/horsesdontstop/actions/runs/38108710763): [`research/TRIAL_LEDGER.csv`](../research/TRIAL_LEDGER.csv). These are NOT independent confirmation data.
- [x] Phase 0 integrity tests plus existing suite [225 passed](https://github.com/Venloud/horsesdontstop/actions/runs/38125009160) before PR #9 merge. No change to broker order permissions.
- [x] **Phase 2 foundation (branch, pending PR merge):** added [offline source-data integrity gate](PHASE2_DATA_INTEGRITY.md), manifest template and synthetic tests. All scored research is correctly BLOCKED until source evidence is complete.
- [x] **Historical-data source discovery (branch only):** researched free Alpaca Basic/IEX, QuantConnect FREE cloud versus paid local downloads, and premium Sharadar, with primary vendor documentation in [PHASE2_HISTORICAL_DATA_SOURCES.md](PHASE2_HISTORICAL_DATA_SOURCES.md).
- [x] **Actual Alpaca data coverage smoke check:** [GET-only GitHub run 38126272354](https://github.com/Venloud/horsesdontstop/actions/runs/38126272354), 2016 Oct raw IEX **0** bars for SPY/AAPL/XLK, 2022 June **22** each (plus 22 for historical FRC), 2026 September **22** each. No OHLCV published, no broker orders or database writes; coverage workflow switched to **manual-only**.
- [ ] **Critical source-quality blocker:** find a legally usable point-in-time, delisting-aware **≥10-year** dataset for each low-frequency candidate. Alpaca's free IEX October 2016 sample was empty and is insufficient by itself. QuantConnect's long historical free **cloud** environment looks promising for an *independent* comparison, but those datasets cannot be copied into GitHub without appropriate local licensing.
- [ ] **Phase 2 prerequisite before scored Phase 3:** precommit actual dataset/version/hash and point-in-time membership, strategy-specific universe, exact discovery/holdout/embargo dates, seeds/bootstrap blocks, matched-deposit conventions and fees; inventory earlier five-stock strategy attempts in historical ledger. Missing manifest means `preregistration_incomplete`, **not** permission to run scored tests.
- [ ] Implement and unit-test the **three new research-only candidate strategies** and statistical comparison harness without connecting order submission code or changing production Neon controls.

## Completed — research and cloud foundations

- [x] Connected GitHub repo and read-only Alpaca PAPER credentials.
- [x] Read-only momentum/relative-volume scanner, historical backtest,
      strategy comparisons and compounding robustness audit.
- [x] Simulated 491 trading sessions, 2024-10-24–2026-10-09.
      Strongest **selected** 2%-risk momentum/volume case ended
      **$886.99** from $500 at 10-bps modeled slippage; $799.58
      at 30-bps. This is **NOT actual profit** and suffers from
      selected symbols, survival and execution assumptions.
      See [robustness audit](https://github.com/Venloud/horsesdontstop/actions/runs/38108710763).
- [x] Verified read-only Alpaca PAPER connection ($500, zero broker
      orders) and live IEX scanner / separate
      [quote preview CI](https://github.com/Venloud/horsesdontstop/actions/runs/38115475872).
- [x] Four production Neon tables:
      `paper_control`, `paper_intents`,
      `paper_audit_events`, `paper_equity_snapshots`.
- [x] Neon migration first proven on a temporary branch, then
      **explicitly owner-approved** to production with kill ON.
- [x] `hds_paper_monitor` **SELECT-only** role and GitHub secret
      `PAPER_JOURNAL_DSN`: real
      [integration test passed](https://github.com/Venloud/horsesdontstop/actions/runs/38114132972).
- [x] `hds_paper_executor` restricted journal writer role, **no**
      `paper_control` UPDATE, no Neon admin, no DELETE, GitHub secret
      `PAPER_EXECUTOR_DSN`: corrected wrong-role secret, verified
      [real read-only integration passed](https://github.com/Venloud/horsesdontstop/actions/runs/38118901207).
      **Do not re-request those credentials.**
- [x] Read-only broker/journal reconciliation, missing-stop detection,
      alert classification and optional opt-in private NTFY notifications
      (owner has **not** supplied notification topic).
- [x] Restricted journal reservation behind Neon kill switch,
      deterministic `client_order_id`, account-wide advisory lock
      and one order intent per New York day. Broker ambiguous outcomes
      never authorize blind resubmission.
- [x] Live IEX quote guards: max 45 seconds old, spread <=75 bps,
      valid quote sizes, price-capped DAY LIMIT + stop-loss OTO
      **request preview**, no extended hours.
- [x] Unit tests for live risk sizing, broker/stale quotes, database
      control checks, rejected/duplicate intent IDs, and timeouts.

## Completed **in main**, not yet verified as launched

Merged from `experiment/paper-broker-lifecycle-v1` via [PR #7](https://github.com/Venloud/horsesdontstop/pull/7), merge commit `9b4450f9a3a5f0929d2b1c4e857ed012960e84ce`. Source is now on `main`, but **NOT enabled for order submission**.

- [x] Implemented equity-at-open writer:
      `bot/paper_execution_journal.py::record_opening_equity` uses
      executor-role permission attestation, Neon server time, exclusive
      transaction advisory lock, and first existing snapshot check.
      Only within **09:30:00–09:34:59 New York time**.
      In **DRAFT safety PR #11**, sampling is independent of the
      existing switch values so it can still record risk baselines
      after a future authorized PAPER launch, but it **never
      modifies a switch or sends an order**. The current
      production switches remain kill TRUE/submissions FALSE;
      no post-launch real session has been tested.
- [x] Implemented scheduled equity sampler:
      `bot/paper_equity_open.py` reads Alpaca **PAPER** account/clock,
      `.github/workflows/paper-opening-equity.yml` triggers at
      **13:30 and 14:30 UTC** weekdays; inappropriate DST window is
      harmless because script confirms 09:30 NY + market clock.
      A late/skipped GitHub job **cannot fabricate a daily baseline**.
- [x] Retired legacy
      `paper_safety.make_protective_alpaca_order` GTC **market**
      buy; it now raises `TradeBlocked`. The future adapter builds
      only a quote-backed **DAY LIMIT OTO**.
- [x] Added `bot/paper_broker_recovery.py`: lookup by
      `client_order_id`, nested stop-leg/order-ID comparison,
      handling of missing/partial/closed stop coverage, blocks
      unverified **GTC stop-child time in force**.
- [x] Added `bot/paper_supervised_execution.py` with an explicitly
      **UNRELEASED** `PAPER_ORDER_SUBMISSIONS_RELEASED=False`,
      mock-tested single submit, exact short-lived approved terms,
      re-read account/positions, fresh IEX quote and one durable
      reservation before submit, best-effort broker **GET** by
      client ID on ambiguous POST/ack/journal outcomes. No live
      order submitted. **Owner approval dataclass is not an
      authenticated approval channel**.
- [x] CI on branch:
      [220 Python tests passed](https://github.com/Venloud/horsesdontstop/actions/runs/38119978555).
      Includes mocked stop/order/approval scenarios. Passing mock
      tests are NOT a broker lifecycle test.
- [x] Created temporary Neon test branch
      **`test-paper-entry-20261011`** /
      `br-billowing-tree-b5vuiscp`, fork of production. This is
      **NOT production**.
- [x] Real temp Postgres **switch gate** tested: under
      `kill_switch_active=TRUE`, `submissions_enabled=FALSE`,
      guarded INSERT returned **0** and intents remained **0**.
- [x] Real temp Postgres unique `client_order_id` test:
      first **test-only direct** insert succeeded, identical second
      `ON CONFLICT DO NOTHING` inserted **0**; count remained 1.
      This direct test bypassed the *code-level* kill gate **on
      temporary DB only**, never used broker or production.
- [x] Real temp Postgres synthetic opening-snapshot query test:
      fake Monday 2026-10-12 **13:31 UTC** $500 opening sample and
      **14:15 UTC** $485 later sample; five-minute window reader
      returned **$500 baseline**, max historical equity **$500**.
      Temp-branch controls remained locked throughout.

## Read-only overnight-stop monitoring hardening (separate draft PR)

- [x] Added strict **synthetic-tested** read-only OTO child stop coverage:
      active SELL **STOP** (not STOP_LIMIT), observed **GTC** TIF,
      enough *remaining* quantity, positive trigger, and linked
      observed BUY LIMIT OTO parent. Missing or unverifiable cases
      now set `position_missing_verified_gtc_oto_stop`.
      See [PAPER_GTC_STOP_READONLY_AUDIT.md](PAPER_GTC_STOP_READONLY_AUDIT.md).
      This work is in a **separate safety PR, not a real Alpaca fill**.
- [x] Staged **non-deployed** read-only persistent supervisor loop,
      restricted DB-server-clock lease code, worker/monitor privilege
      attestation and source-level CI release tripwires. See
      [PAPER_WORKER_HEARTBEAT_STAGE.md](PAPER_WORKER_HEARTBEAT_STAGE.md).
- [x] Tested staged lease SQL against the **existing isolated Neon
      `test-paper-entry-20261011` branch only**: first pulse
      unhealthy, second sequential pulse healthy, duplicate and
      competing worker rejected. Both test-branch submission
      switches remained OFF. This was **management SQL**, NOT
      a verified restricted-role production/real-host test.
- [ ] Apply and independently privilege-test the heartbeat migration
      ONLY after owner approval, select safe always-on host and
      provision a new restricted heartbeat writer credential; neither
      the production table nor `PAPER_HEARTBEAT_DSN` exists yet.
- [ ] Prove a real persistent worker/independent alert path and
      populate the execution gate's genuine watchdog age from
      independently observed DB heartbeat. Currently
      `paper_watchdog` correctly reports **None**.

- [ ] Independently observe **actual PAPER nested child stop TIF**
      and behavior across a market close before allowing overnight
      exposure. Official Alpaca examples show DAY LIMIT OTO
      parent but do NOT prove the child remains GTC.
- [ ] Persist and reconcile the originally approved stop **price**,
      order IDs, partial-fill quantities and linked parent across
      outages; a single read-only account-wide stop listing is
      insufficient for full overnight protection.

## Opening equity and loss/drawdown safety work (DRAFT only)

- [x] **Staged** opening-equity writer no longer requires
      `kill_switch_active=TRUE/submissions_enabled=FALSE` to
      capture an actual broker PAPER account balance. It verifies
      boolean control states without changing them, uses Neon
      server time inside **09:30–09:35 New York**, a durable
      once-per-session lock and no order API. This will preserve
      daily baseline collection on **future** separately approved
      trading days. Broker trading restrictions do not prevent
      **read-only** equity observation, and an observation finishing
      at a different day's 09:30 open cannot be backfilled.
- [x] Added offline `bot/paper_equity_risk.py` computation for
      3% daily loss / 15% adjusted peak, based only on a fully
      independently attested snapshot/flow/exchange-calendar history.
      `tests/test_paper_equity_risk.py` now exercises genuine
      cash deposits hiding losses, withdrawals, exact cutoffs,
      missing source evidence, duplicates and stale samples.
      **Unit-test success is NOT permission to place trades.**
- [ ] **Still missing in production:** complete independently
      verified recurring marked-to-market equity sampling, durable
      broker-verified external cash-flow ledger, accurate periodic
      peak history and an actual 09:30 baseline row. Do not wire
      offline helper flags as `True` before independent source
      validation. Production's existing raw `MAX(equity)` can
      be distorted by deposits and is **not a trusted adjusted
      risk reference**.
- [ ] Actual exchange calendar including NYSE holidays and
      early closes, integration with live risk gate, persistent
      monitor and final per-order owner approval.

## Still to do — in dependency order

- [x] Review and merge the tested lifecycle and docs to `main`:
      [PR #7](https://github.com/Venloud/horsesdontstop/pull/7),
      merge commit `9b4450f9a3a5f0929d2b1c4e857ed012960e84ce`.
      Merge **did not** enable any PAPER order submissions.
- [ ] Test the **real restricted Python executor-role connection
      on the temporary Neon branch**, not just SQL through the
      management account. The Neon management session **could not
      `SET ROLE hds_paper_executor`** (`permission denied`).
      Thus actual column-level INSERT/transaction semantics and
      concurrent sessions have NOT yet been proven with the Python
      executor. Do not confuse SQL mock and one-session constraint
      tests with true concurrent-worker integration tests.
- [ ] Verify first live **opening equity snapshot** on an actual
      weekday while both production controls remain OFF to trade
      (kill switch ON / submissions OFF). Run after 09:30 NY time;
      confirm exactly one timestamped database row. If GitHub cron
      skips/delays beyond 09:35, leave baseline missing and retry
      only on a future *real* session, never backfill.
- [ ] Add durable **daily/peak equity aggregation** including
      transparent contributions and genuine fills, and verify
      3% day / 15% peak risk limits across restarts, gaps, and DST.
      Baseline without a recurring peak history is not enough.
- [ ] **Verify actual Alpaca PAPER OTO child stop**
      `time_in_force` **and overnight protection** on a broker
      accepted/filled test order: parent DAY does *not* prove
      child GTC. Until proven, block overnight entries or require
      a supervised flatten-at-close procedure. Never claim a
      stop trigger guarantees fill.
- [ ] Build an actual **persistent 24/7 supervised worker**
      independent of best-effort GitHub Actions cron; persist
      heartbeat with verified timestamps, redundancy and alerting,
      detect orphaned stops, partial fills, manual edits, restarts,
      broker outages and stale quotes.
- [ ] Add independent watchdog failure alerts, verify NTFY/email
      delivery and fallback if monitoring fails. No alert secret
      is yet configured.
- [ ] Enforce account-wide 1%-risk sizing *under actual fills*,
      portfolio cash, split/halting/exchange constraints, proper
      NYSE holiday/early-close calendar, liquidity/quote drift;
      paper execution differs from IEX top-of-book.
- [ ] Validate real end-to-end Alpaca PAPER order status
      transitions in a strictly supervised test (submitted ->
      filled/partially filled/rejected/canceled, attached stop ->
      accepted/active/filled, overnight).
- [ ] Finish authenticated owner approval channel; require
      **separate approval for the specific first paper order**
      with fresh ticker, shares, limit, stop and clock. Do not
      re-interpret this checklist or generic "go" as approval to
      flip either Neon switch.
- [ ] Independently verify **paper-only** endpoint and no
      real-money credentials under all failures; add live-API
      sandbox tests before enabling a production code release.
- [ ] First small supervised PAPER fill after all safety gates
      pass; afterward forward-test **8–12 weeks** with an
      untouched benchmark and genuine broker fills/slippage.
- [ ] Only after separate future approval discuss any
      real-money transition. **Not in scope.**

## Safety validation commands and authoritative links

```bash
python -m pytest -q
python -m bot.paper_executor_preflight  # READ ONLY; uses GitHub secret
python -m bot.paper_watchdog          # READ ONLY; rejects writable monitor DSN
python -m bot.live_paper_preview_cli  # LIVE IEX quotes; ZERO orders
# python -m bot.paper_equity_open     # MAY insert equity snapshot at 09:30–09:35 NY
```

Do not run the opening snapshot CLI with made-up clock/account inputs
against production. Do not run `submit_order` manually. No order
execution runner/workflow is deployed in this repository.

Useful links:
- Project: https://github.com/Venloud/horsesdontstop
- Tests: https://github.com/Venloud/horsesdontstop/actions
- Live research: https://github.com/Venloud/horsesdontstop/blob/main/docs/LIVE_PAPER_QUOTES.md
- Executive gate: https://github.com/Venloud/horsesdontstop/blob/main/docs/PAPER_LAUNCH_CHECKLIST.md
- Alpaca docs: https://docs.alpaca.markets/us/docs/orders-at-alpaca

**Stop here if anything is missing:** This checklist is a dependency
tracker, never a checklist-to-execution authorization.
