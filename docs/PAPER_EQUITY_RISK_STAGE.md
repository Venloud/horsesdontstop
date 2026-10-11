# PAPER risk-accounting research: staged, not connected to live trading

**2026-10-11.** Read-only/offline preparation in draft PR #11.
Production control remains emergency kill **ON**, submissions **OFF**,
code-level broker submission release **FALSE**. The staged
calculation is **NOT wired into** `PaperSafetyState` or any broker POST.

## Opening equity correction

The opening snapshot collector is *separate* from permission to trade.
The previous writer only inserted when production trading was disabled,
which would block the 3% daily loss calculation on every later
enabled trading day.

Staged `PaperExecutorJournal.record_opening_equity` now checks the
singleton control row contains genuine booleans but does NOT restrict
observing an equity baseline to a particular boolean combination.
It does NOT UPDATE either switch. The writer still requires restricted
Neon executor permissions, the actual database server time inside
`[09:30:00,09:35:00)` New York, exclusive once-per-opening
transactional advisory lock, positive broker equity, and only one
opening baseline. Late GitHub cron cannot backfill missing openings.

The broker collector also records a baseline if the account's
`trading_blocked` flag is active, because that flag must prohibit
**orders**, not prohibit **observing equity risk**. It verifies the
broker read finished within the exact same 09:30 NY opening interval.
Neither branch case sends orders.

**All combinations of synthetic current/future order-control booleans
are tested solely to confirm observation doesn't flip them**.
Contradictory or emergency states still remain trading blockers
via the separate supervised-execution gate.

## Contribution-adjusted daily and peak risk calculator

`bot/paper_equity_risk.py` is a PURE offline function. It requires:
- Independently attested complete ordered equity observations and a
  broker-reconciled external cash-flow history;
- Each external cash flow has a unique broker event ID, broker
  verification, correct sign and strict timestamp;
- A real, verified exchange calendar and exactly one opening
  equity baseline in the correct five-minute interval;
- Current equity sampled within **120 seconds**; any stale,
  missing, ambiguous, duplicate, future or contradictory data
  MUST block calculation.

The calculator subtracts audited owner **cash deposits** and adds back
owner **withdrawals** to measure trading-account performance rather
than incorrectly counting external deposits as profit or withdrawals
as trading losses.

**Regression example with fictional PAPER numbers only:**

| Action | Raw equity | External deposits | Contribution-adjusted equity |
|---|---:|---:|---:|
| Morning open | $500 | $0 | $500 |
| Account loses $15 | $485 | $0 | $485 |
| Owner deposits $100 | $585 | $100 | $485 |

Despite raw equity showing $585, the result must still trigger the
**3% daily loss** cutoff. A prior contribution-adjusted $600
high-water mark and $510 current equity must trigger the **15% peak
drawdown** cutoff. Tests also cover deposit-only/withdrawal-only
cases, exact numeric thresholds, invalid proofs, and stale equity.

`tests/test_paper_equity_risk.py` uses ONLY synthetic values and never
connects to Alpaca or Neon.

## Before this can ever be used for an order

- [ ] Capture real broker equity continuously on a genuinely
      persistent read-only worker with verified time, frequency,
      stop coverage and external alert delivery.
- [ ] Collect a **durable verified external contribution/withdrawal
      ledger** with owner and broker attribution. It must distinguish
      cash transfer from dividends, fees, fills and corporate actions.
- [ ] Define + test exact transaction timestamps, snapshot ordering,
      completeness, missing-session flags, and correction/reversal
      processing. A supplied boolean flag is **not** evidence in
      its own right.
- [ ] Persist an audited historic contribution-adjusted high-water
      mark across restarts and broker data gaps. A raw
      `SELECT MAX(equity)` is not trustworthy after a deposit.
- [ ] Independently verify NYSE dates/market clock/DST, peak
      and baseline calculations on a separate temporary Neon
      test branch using real restricted-role connections and
      representative (non-production) fake cash transfers.
- [ ] Wire a reviewed fail-closed adapter into the existing
      supervised risk gate ONLY after the above real proofs,
      and never bypass the **1% per order**, **3% daily**,
      **15% peak** caps.
- [ ] Separately prove a verified **active GTC child stop**
      for actual filled Alpaca PAPER OTO order, persistent
      worker / heartbeat and owner-authenticated exact
      first-trade approval.

**Last observed production:** zero equity snapshot rows, zero
intent rows, kill ON, submissions OFF. A successful offline
calculator/CI run does not change this.
