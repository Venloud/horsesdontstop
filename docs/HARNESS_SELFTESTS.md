# Harness self-tests (PREREGISTRATION section 7), 2026-10-13

Offline only: synthetic data or the archived legacy artifact. No Alpaca, no
Neon, no scored strategy run. Code: `bot/research_harness.py`. Tests:
`tests/test_research_harness_selftests.py`. These run against the real
`run_portfolio` simulator (unchanged since legacy commit `aea2e5b`, plus an
optional `score_fn` hook whose default path is tested identical).

| Section 7 item | Status | How |
|---|---|---|
| Future-leak signal caught | Pass | Perturb every bar from a cut day on, keeping only that day's open. A planted signal reading the entry-day close or volume changes decisions and is flagged. The 4 coded strategies, the placebo and the planted marker show no violation on the checked days. |
| Random entries near negative costs | Pass | Flat synthetic bars make the gross expectation exactly 1 (optional stopping), so net R per trade is `((1-s)/(1+s)-1)/0.03`: -0.199R at 30 bps, -0.398R at 60 bps, 0 at 0 bps. Pooled means land within 4 SE of these. |
| Planted known edge recovered | Pass | A +2% move after a marker bar has true value 0.470R at 30 bps. Pooled 95% CI contains it and excludes the cost null; 50 independent CIs cover it at least 43 times. |
| Replay run 38108710763 to rounding | **Partial, full replay blocked** | See below. |

## Legacy replay

- Archived `research/legacy/robustness-audit-38108710763.zip`, SHA-256
  `87d610ee...51cb`, identical to the digest GitHub reports for artifact
  11690790832 (it expires from Actions on 2026-10-25).
- Verified offline: all 80 `TRIAL_LEDGER.csv` rows equal the artifact, and
  all 2,192 archived trades re-derive each row's cash chain, trade count,
  P&L, final equity, win rate and profit factor.
- **Not verified:** drawdown, SPY return and the trades themselves need the
  original daily bars, which were never saved and have no recorded hash.
  `legacy_replay(data, report)` re-runs the exact 64 + 16 design and compares
  every row and trade; it returns `blocked_bars_snapshot_missing` until bars
  exist. Getting them needs an Alpaca market-data fetch (IEX, split-adjusted,
  2023-10-12 to 2026-10-10, the 15 symbols plus SPY and sector ETFs), which
  this PR does not do. A split after 2026-10-11 would change the adjusted bars
  and make an exact match impossible; then the honest result is "mismatch".

Limits: synthetic flat bars test accounting and leakage logic, not intraday
stop realism, dividends or real fills. Passing says nothing about any edge.
