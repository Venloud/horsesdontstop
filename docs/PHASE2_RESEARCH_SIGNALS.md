# Phase 2 preregistered signal implementations, offline only

**2026-10-11, DRAFT PR #10.** Added pure research-only candidate signals in
[`bot/phase2_research_signals.py`](../bot/phase2_research_signals.py) and
synthetic-only regression tests in
[`tests/test_phase2_research_signals.py`](../tests/test_phase2_research_signals.py).

These are the **three previously missing** owner-preregistered hypotheses:
`short_term_reversal`, `etf_dual_momentum`, and `overnight_drift`.
The existing four strategy names and existing exploratory CLI are **unchanged**.
The functions intentionally produce **no scored backtests, broker requests,
journal writes, order requests or purchase recommendations**.

## Strict, frozen parameters

1. **Short-term reversal, four variants:** 200 completed-session SMA with
   rising previous SMA, latest completed close above current SMA and
   *below* preceding close, Wilder RSI(2) with seed average of the first two
   price deltas <=5 or <=10; fixed maximum 1 or 3 holding sessions. Returns
   a non-order `ReversalSignal` only when 201 previous completed bars exist.
   All execution, stops, holding-policy simulation and fees remain separate.
2. **ETF dual momentum, three variants:** exactly the 11 US sector ETFs
   `XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY`, plus `SPY`.
   At a calendar-verified transition from the *last completed session of
   a month* to the next eligible session of the following month, rank
   trailing **total-return index** changes over exactly 63, 126 or
   252 fully completed sessions. Hold cash if all returns are <=0.
   Missing/misaligned histories, absent verified total-return source,
   point-in-time membership or official exchange calendar blocks selection.
   Return values are research eligibility only.
3. **Overnight drift, two variants:** no filter, or positive SPY return
   across the previous 20 **fully completed** sessions. No entry-session
   OHLCV is used. The output is *only a research hypothesis indicator*,
   never a claim that the close-to-next-open order can be executed.
   Verified auction cutoff, pre-auction signals, historical auction
   records, next-session market calendar and real stop behavior are all
   missing, so overnight real/PAPER entries remain blocked.

This implementation does not change the preregistered **21 variants
total** (4 existing momentum/volume, 3 existing trend-following, 3 existing
52-week-high, 2 existing sector-leadership, 4 reversal, 3 ETF, 2 overnight).

## Known gates before scored Phase 3

- `research/phase2_manifest.json` remains **draft**, with no real verified
  bars, PIT universe/delistings, SPY total-return benchmark, audited owner
  deposits or frozen untouched holdout. Structural gates must still fail.
- Pure code tests do **not** establish point-in-time data availability,
  correct vendor licensing, real exchange calendars or statistical edge.
- Freeze and independently review the precise interpretation of RSI(2),
  ETF month-end calendar eligibility, aligned corporate-action-adjusted
  total-return series, and the overnight pre-auction cutoff **before**
  scored testing. A spec change after looking at scored returns counts as
  another disclosed trial.
- The existing previously inspected 2024-2026 window is contaminated and
  may not be reused as an untouched holdout.
- No PAPER order submission, no live trading and no paid service has been
  enabled. Current code/Neon trading kill switches remain OFF to submissions.

This module makes Phase 2 *implementation-ready for offline harness tests*,
NOT scientifically validated or eligible for live/PAPER trade promotion.
