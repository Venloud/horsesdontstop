# Paper trading launch gate (October 2026)

> **See [CURRENT DONE/TODO CHECKLIST](PROJECT_STATUS_CHECKLIST.md)** for
> verified current status. This document originated as an earlier
> launch-safety specification and contains historically true but
> now stale implementation wording. The project now has a staged,
> hard-disabled submit adapter on
> `experiment/paper-broker-lifecycle-v1`; it has **never submitted
> a production PAPER order**. Neon production kill remains ON and
> submissions OFF. Do not treat the existence of adapter code or
> passing mocks as permission to trade.

**Status: PREVIEW ONLY, ZERO ALPACA ORDERS.** This repo now contains an
Alpaca PAPER-only order planner, but no `submit_order()`, `cancel_order()`
or other brokerage write operation in its paper-readiness workflow.

## Completed development

- Verified $500 Alpaca paper account connection, read-only.
- Momentum-volume was the stronger *exploratory* candidate after the broader
  historical robustness test; it is not a validated profitable strategy.
- Deterministic `client_order_id` per symbol/strategy/trading day.
- Dynamic account-equity sizing: gains and losses change risk allocation
  **immediately, including below $1,000**. Whole-share cash and max 1–2%
  equity risk budgets; no automatic deposits.
- A stop-loss OTO order *request builder*, with one protective broker-side
  stop and **no forced take-profit ceiling**. It generates an SDK request
  object but DOES NOT send it. Overnight price gaps can exceed risk budgets.
- Failure-closed gate that refuses to authorize execution unless:
  PAPER endpoint, explicit enable, kill switch off, market open, no existing
  open position or orders, fresh real-time quote (<=120 seconds), durable
  journal healthy, broker order/position reconciliation successful,
  persistent watchdog heartbeat healthy (<=120 seconds), positive and
  checked paper cash/equity, verified daily start/peak equity, under 3%
  day-loss cutoff and 15% equity drawdown cap.
- `.github/workflows/paper-readiness.yml` runs *read-only* on push,
  weekdays and manually. It shows non-executable entry plans and the list
  of safety controls still blocking submission.

## Required before starting autonomous paper orders

1. A **durable hosted transaction journal** for intents, submitted
   client_order_ids, fills, stop legs, cash contributions and an append-only
   equity ledger, not artifacts or local SQLite in a GitHub runner.
2. A persistent supervised process/worker that remains alive while positions
   are open, even with the user's PC off. GitHub Actions cron alone is
   delayed/skipped and unsuitable as a watchdog.
3. An actual PAPER-only broker adapter for protected entry orders, with
   fail-closed behavior on unknown submit outcomes, no unprotected market
   buys, and idempotent intent reservation BEFORE submission.
4. Reconcile broker positions and all open orders against journal before
   every action, and monitor stop child-leg status after fills. If status is
   unknown or mismatched, halt new entries and escalate.
5. Establish a proper market calendar (holidays and DST), fresh bid/ask
   quotes and spread limits, stable stop coverage, stale-data detection,
   orphaned-order recovery, explicit notification/alerts and a kill switch.
6. Prove failure handling with mocked outages, timeouts, duplicate run
   invocations, partial fills, stop gaps and power-loss simulations.
7. User reviews the final trading switch. Continue with **paper only** and
   evaluate at least 8-12 weeks before contemplating live money.

## Why the historical numbers are not a launch guarantee

The October 2026 robustness audit's best 2%-risk, 10-bps momentum-volume
simulation increased $500 to $886.99 (+$386.99, +77.40%) across **491
trading sessions, 2024-10-24 to 2026-10-09**. This was a selection-biased
15-stock subset using split-adjusted IEX daily bars, a simplified
fill/slippage model, no dividend treatment, no taxes or real stop orders,
and no untouched forward validation. At 30 bps of simulated slippage,
the ending balance was $799.58. It is **not** actual Alpaca performance.
The initial +$100 monthly contribution ceiling remains optional,
owner-approved and excluded from all simulations.

## Future implementation note

Alpaca documents stop-only One-Triggers-Other (OTO) orders:
https://docs.alpaca.markets/us/docs/orders-at-alpaca

An OTO stop is a protective mechanism, not a guarantee of fill price or
profit. Test and verify specific order type constraints in paper trading
before enabling any autonomous order routing.

**Never store API secrets in code or public research artifacts.**
