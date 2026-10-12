# Phase 0 amendment 1 (DRAFT): QuantConnect cloud as the long-history source

**Dated:** 2026-10-12. **Status:** DRAFT for owner review. Amends
`docs/PREREGISTRATION.md` only where stated; everything else there stands
(seven candidates, 21 variants, 1% risk, 15% drawdown, 30 bps per side with
60 bps stress, 300 independent events or 10 years, matched-deposit benchmark).
This amendment counts as a dated amendment, not a rewrite of any earlier run.
It authorizes no scored run, no order, and no billing.

## Why
Alpaca's free IEX feed returned zero daily bars for October 2016, so the
10-year exception cannot be met from it. The owner's QuantConnect free cloud
notebook showed SPY daily bars for 2012 to 2022 and delisting, split and
dividend events for sampled tickers. QuantConnect data cannot be exported
or redistributed on the free tier.

## Changes
1. **Long-history scored runs may execute inside QuantConnect free cloud.**
   The dataset-hash rule is met for this source by recording, instead of a
   data-file SHA-256: the repository commit SHA of the harness code, the
   QuantConnect project and backtest/research run identifiers, the full text
   of the run output saved in `research/quantconnect/`, and the date range
   and symbols used. No raw vendor prices are committed.
2. **Alpaca IEX stays the source for forward paper and live checks.** It is
   not used as a scored long-history source.
3. **Committed before any scored run:** the harness code, the frozen symbol
   universe, seeds, bootstrap block length, fold dates and embargo. Anything
   changed afterwards is a further dated amendment and counts as extra trials.
4. **Coverage batch first.** Run `research/quantconnect/phase2_coverage_batch.py`
   once and commit its text output. It reports first and last bar date per
   symbol, years with short counts, and split, dividend and delisting counts
   for ETFs, large stocks and later-delisted names.

## Known problem this amendment does not fix
`etf_dual_momentum` as coded requires all 12 ETFs (SPY plus 11 sector ETFs)
to share identical sessions. XLRE (listed late 2015) and XLC (listed mid 2018)
shorten the common history, so the 10-year exception may not be reachable for
that candidate with the frozen universe. The coverage batch will show the
first bar date per ETF. If the common history is under 10 years, the owner
chooses between (a) amending the ETF universe, before any scored run, to
ETFs with 10 or more years of history, or (b) leaving that candidate
inconclusive. This choice is made before looking at any returns.

## Open items
- Confirm the QuantConnect free-tier terms allow committing summary counts
  and metrics derived from their data. Not verified here.
- Confirm the free research node quota covers the batch and, later, the
  10,000-sample bootstrap and placebo runs.
