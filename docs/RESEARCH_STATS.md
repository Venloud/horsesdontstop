# Section 6 statistics, 2026-10-13

Offline code: `bot/research_stats.py`, tests: `tests/test_research_stats.py`.
Nothing here has been run on real data. Seeds, block length and trial IDs
for a scored run must be committed in a dated amendment first.

## What each piece does

- **Date-block bootstrap** (`date_block_bootstrap`): circular blocks of
  consecutive sessions, so same-day trades stay together. Flags
  `prereg_compliant` only at >= 10,000 resamples. Tested: matches normal
  theory on iid data, widens for same-day clusters, covers the true mean at
  >= 86% (nominal 95%) under AR(1) days.
- **Placebo** (`placebo_test`): the strategy and every placebo go through
  the same run function, so exits, stops, costs, cash and universe are
  identical by construction; only the entry signal differs. The random entry
  rate is calibrated so placebo median **exposure** equals the strategy's.
  Each placebo trial is cut or topped up to the strategy's **exact trade
  count**, so the 95th percentile has the same noise. If exposure cannot be
  matched the status is `invalid_exposure_mismatch`, never a pass.
  Holding periods are reported, not gated: a real edge changes them.
  Measured on synthetic data: no-edge false-pass 4/40 at nominal 5%.
- **Deflated Sharpe** (`deflated_sharpe`): Bailey and Lopez de Prado (2014).
  N = ledger rows (80) + new distinct trial IDs (`ledger_trial_count`).
  Positive lag-1 autocorrelation shrinks T to T(1-rho)/(1+rho). Variance of
  trial Sharpes must come from trials at the same return frequency;
  `per_trade_sharpes` can supply it from the 80 archived legacy rows.
  Tested: the best of 40 noise strategies passes <= 10% of the time.
- **Matched-deposit benchmark**: `align_cash_flows` maps each owner deposit
  to one session, and that same dict feeds both
  `run_portfolio(cash_flows=...)` and `matched_deposit_benchmark`. Both buy at
  that session's open. Exact TWR from valuations just before each flow;
  annualized MWR (IRR). With deposits, strategy drawdown uses unit NAV so a
  deposit cannot hide a loss. Price-only SPY is flagged incomplete.

## Needs an owner decision (not decided here)

1. **Whole vs fractional SPY.** One SPY share costs more than $500, so a
   whole-share benchmark holds only cash. Default here is fractional.
2. Dividends reinvest at the ex-date close; no SPY total-return source is
   wired in yet, so any real comparison is currently `incomplete`.
3. Placebo design above (exposure-calibrated rate, count-matched trials)
   is my reading of section 6.3. Confirm or amend before scoring.
4. Block length and seeds are not chosen. They go in the dated amendment.
