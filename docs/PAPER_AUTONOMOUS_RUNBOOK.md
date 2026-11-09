# Autonomous PAPER trading: review notes and release runbook

Status (2026-11-09): branch `fix/paper-reservation-expiry-20261109`.
Migrations 001 and 002 applied. **Migration 003 reviewed, not applied.**
`PAPER_ORDER_SUBMISSIONS_RELEASED` and `PAPER_RECONCILIATION_SERVICE_RELEASED`
are `False`. Kill switch ON. The scheduled executor is `--prepare` only.

## Identities (no owner credential is stored on the trading host)

| Login / role | Used by | Can do |
|---|---|---|
| `hds_paper_win_monitor` | supervisor, diagnostic, reads, `-Status` | SELECT only |
| `hds_paper_executor` | executor, `-Stop` | owner-review reservation, policy-authorize one entry (003), commit dispatch, PAUSE (003) |
| `hds_paper_reconciler_service` → `hds_paper_reconciler` | reconciler service, executor reconcile step | broker-verified status transitions (release-gated), expire unapproved reservations, retire never-sent dispatches (003), PAUSE (003) |
| `hds_paper_operator_login` → `hds_paper_operator` (003) | `set-paper-trading.ps1 -Arm`, human only | ARM; password typed each time, never stored |
| `neondb_owner` | Neon integration / interactive deploy | migrations and release SQL |

`paper_role_boundary` refuses to run a bot login that is a member of the
operator or control-owner roles or can execute any SECURITY DEFINER function
outside its allowlist; `paper_arm_autonomous_trading` is not on it.

## Independent risk enforcement

In Neon (`paper_policy_authorize_entry`, under the exclusive-intent lock):
controls ARMED; policy `autonomous_entries_enabled` (installed FALSE);
Mon-Fri 09:35-15:30 NY; valid GTC OTO terms; no open intent; one entry per
day; risk <= 1% and cost + $0.50 <= 100% of the LOWEST opening-window
equity snapshot; then intent + single-use 120 s approval + audit. The
existing dispatch commit consumes the approval once and marks UNKNOWN pre-POST.

In Python: 3% daily-loss and 15% drawdown cutoffs block entries and engage
the kill switch (Neon has no live equity feed). Code release flags gate every
broker write.

## Lifecycle handling

- Unapproved reservation: expires after 10 min (audited).
- Dispatch committed but never sent: retired after 15 min with broker 404 (audited).
- Entry unfilled > 10 min, or any open entry from 15:45 NY: canceled by single order UUID.
- Partial fill: entry canceled; remaining shares flattened with one market SELL `<cid>-x`
  only if no SELL leg is open; recovery accepts only that exact filled exit.
- Filled position without a verified GTC stop (after 2 min grace) and no open SELL: flattened.
- Position not explained by the journal, or shares held by an unverified SELL: never
  touched; urgent alert.
- Monitor: PAUSED and ARMED are healthy; journaled exposure with exactly one
  verified durable GTC OTO stop is `managed_exposure`; orphan SELLs, unprotected
  shares and loss cutoffs page critical.

## Emergency stop

`ops\windows\set-paper-trading.ps1 -Stop -Reason "..."` (executor login; any
bot role may pause). Authorization, reservation and dispatch refuse at once in
Neon. Maintenance (cancel/flatten) keeps running while paused. Broker GTC stops
are unaffected. Only `-Arm` (operator password) re-enables.

## Release sequence

1. Apply `sql/migrations/003_policy_authorization_and_controls.sql` (owner, Neon integration).
   Record it in `hds_schema_migrations` with the sha256 in the evidence.
2. Owner: create `hds_paper_operator_login` (SQL at the end of
   `sql/release/enable_autonomous_entries.sql`; you choose and keep the password).
3. Release commit: both `*_RELEASED` flags `True`; update the pinned tripwire
   tests; `--apply-status` in `run-paper-reconciler.ps1`.
4. Owner: run `sql/release/enable_autonomous_entries.sql` (policy ON).
5. `set-paper-trading.ps1 -Status` (PAUSED), then `-Arm` during the session.
6. **Supervised proof**: `run-paper-executor.ps1 -Autonomous` once. Verify the
   `policy_authorized` audit row and dispatch receipt; the Alpaca OTO parent and
   GTC stop; reconciler UNKNOWN -> accepted -> filled; supervisor
   `managed_exposure`; executor `POSITION_MANAGED`; then `-Stop` and confirm `PAUSED`.
7. Only after 6: `install-paper-autonomous-schedule.ps1`.

## Known gaps

- Exit is the initial 3% stop only; the preregistered 5% trailing stop is not built.
- No maintenance outside 09:36-15:56 NY (positions rely on broker GTC stops overnight).
- If the `<cid>-x` exit order is canceled or rejected, automatic flatten stops for that
  intent and alerts; manual handling required.
- Notifications are best effort (no durable outbox).
