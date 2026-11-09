# Autonomous PAPER trading: review notes and release runbook

Status (2026-11-09): code merged on `fix/paper-reservation-expiry-20261109`.
Migrations 001 and 002 applied. **Migration 003 is under review and not applied.**
`PAPER_ORDER_SUBMISSIONS_RELEASED` and `PAPER_RECONCILIATION_SERVICE_RELEASED`
remain `False`. The scheduled executor remains `--prepare` (no broker orders).

## Identities (no owner credential is stored or used by the bot)

| Login / role | Used by | Can do |
|---|---|---|
| `hds_paper_win_monitor` | supervisor, diagnostic, executor reads | SELECT only |
| `hds_paper_executor` | executor | reserve (owner-review path), **policy-authorize entry** (003), commit dispatch, **pause** (003) |
| `hds_paper_reconciler_service` → `hds_paper_reconciler` | reconciler, executor's reconcile step | broker-verified status transitions, expire unapproved reservations, **pause** (003) |
| `hds_paper_operator_login` → `hds_paper_operator` (003) | `set-paper-trading.ps1` (human) | pause, **arm**, read controls/policy |
| `neondb_owner` | `deploy-paper-migrations.ps1` only, typed per run | schema migrations |

No bot role can arm trading, write tables directly, or change the policy.
`paper_role_boundary` refuses to run a bot role that could execute
`paper_arm_autonomous_trading`.

## Independent risk enforcement (Neon, migration 003)

`paper_policy_authorize_entry` checks, under the exclusive-intent lock:
controls ARMED; `autonomous_entries_enabled`; Mon-Fri 09:35-15:30 NY; valid
GTC OTO terms; no open intent; entries today < `max_entries_per_day` (1);
today's opening-equity snapshot exists; risk <= 1% of opening equity (+$0.02
rounding); cost + $0.50 <= `max_position_fraction` (100%) of opening equity.
It then writes the intent, a single-use 120-second approval and an audit
event. The unchanged `paper_commit_bounded_pilot_dispatch` consumes it once
and marks the intent UNKNOWN before the single POST.

Python additionally enforces the 3% daily-loss and 15% drawdown cutoffs and
engages the kill switch itself if either is hit. These two are NOT enforced
in Neon (it has no live equity feed).

## Emergency stop

`ops\windows\set-paper-trading.ps1 -Stop -Reason "..."` (operator login).
Kill ON / submissions OFF is checked inside every authorization, reservation
and dispatch function. Broker-side GTC stops on open positions are unaffected.
Executor, reconciler and operator can all pause; only the operator can arm.

## Release sequence (each step separately approved)

1. Review and apply 003: `deploy-paper-migrations.ps1 -Plan`, then
   `deploy-paper-migrations.ps1 -ProvisionOperator`.
2. `set-paper-trading.ps1 -Status` (expect PAUSED), then `-Arm`.
3. Release commit: set both `*_RELEASED` flags `True`, update the tripwire
   tests that pin them, and pass `--apply-status` in `run-paper-reconciler.ps1`.
4. **Supervised lifecycle proof** (one entry, during the session):
   `run-paper-executor.ps1 -Autonomous`; verify in Neon `policy_authorized`
   audit row and dispatch receipt; verify at Alpaca the OTO parent and held
   GTC stop; verify reconciler moves UNKNOWN -> accepted -> filled; verify the
   supervisor reports `managed_exposure` and the executor reports
   `POSITION_MANAGED`; then `-Stop` and confirm the next run reports `PAUSED`.
5. Only after 4 passes: switch the executor task to `-Autonomous` every 5
   minutes 09:36-15:25 NY.

## Known gaps (improve with real PAPER results)

- Exit is the initial 3% protective stop only. The preregistered 5% trailing
  stop is not implemented, so a winning position is held until stopped out
  and blocks new entries (one position at a time).
- An accepted-but-unfilled GTC entry is not auto-canceled.
- Activity notifications are best effort (no durable outbox).
