# Phase 0 amendment 2 (DRAFT): harness settings and open readings

**Dated:** 2026-10-13. **Status:** DRAFT for owner approval. Amends
`docs/PREREGISTRATION.md` only where stated, alongside amendment 1 (PR #12,
QuantConnect as the long-history source). This is a dated amendment, not a
rewrite of any run. It authorizes no scored run, no order, and no billing.
Owner decisions given 2026-10-13 are recorded below.

## 1. ETF universe (changes section 3 and the frozen signal constant)
Drop XLC (listed about June 2018) and XLRE (listed about October 2015).
Universe becomes SPY plus XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY
(10 ETFs, all listed since 1998). Reason: with the old 12-ETF universe the
signal needs every ETF to have lookback+1 bars, which left about 8.0, 7.8 and
7.3 years for the 63, 126 and 252 lookbacks, so all three ETF variants would
fail the 10-year rule before any return was seen. Decided before any scored
run. The constant `SECTOR_ETF_UNIVERSE` in `bot/phase2_research_signals.py`
changes in a separate code PR; tests that reference the 12-ETF list change
with it. The ETF-universe dates are approximate and are confirmed against the
frozen vendor data (see amendment 1 coverage batch).

## 2. Benchmark
The SPY benchmark uses fractional shares. Strategies keep whole-share sizing.
(One SPY share costs more than $500, so a whole-share benchmark would sit in
cash.) This favors the benchmark slightly, which is the conservative side.

## 3. Strategy readings
- `short_term_reversal`: "hold 1 or 3 sessions" means exit at the open N
  sessions after the entry open, earlier if the standard stop is hit.
- `etf_dual_momentum`: the standard 3% initial stop and 5% trail apply between
  monthly rebalances, because the 1% risk rule is defined by stop distance.
  Consequence: a position is about one third of equity at 1% risk and the
  rest is cash. This is accepted, and is not tuned after seeing results.
- All 19 non-overnight grid variants are runnable in the adapters
  (`bot/strategy_adapters.py`). Overnight drift stays research-only.

## 4. Statistical settings
- Date-block bootstrap: block length 10 sessions, 10,000 replicates.
  Block lengths 5 and 20 are reported as sensitivity checks only and never
  used to pick a pass or a variant.
- Placebo: at least 10,000 draws, matched exposure and exit policy.
- Seeds, fixed here: bootstrap 20261013, placebo 20261014, planted-edge and
  leak self-tests 20261015.
- Deflated Sharpe counts the 80 ledger trials plus every new trial, with
  the lag-1 autocorrelation adjustment.

## 5. Legacy archive
The saved report of run 38108710763 (Alpaca-derived trade fills) is kept in the
private repo (`claudevandam/research-archive/`), not in this public repo. The
public tests skip the archive checks unless `HDS_LEGACY_ZIP` points to a copy.
SHA-256 `87d610ee15d6990296f3915ca2eed5040a81a106496f2bfafd530b03b39951cb`.
A full replay to rounding stays blocked because the original price bars were
never saved.

## 6. Still open (owner)
- Approve this amendment and amendment 1.
- A number for the "materially smaller drawdown" tolerance against the index
  fund. Until then only outperformance counts as a pass.
