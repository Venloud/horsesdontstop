# Phase 0 trial ledger: provenance and operating rules

**Registered and merged to `main` on 2026-10-11 in [PR #9](https://github.com/Venloud/horsesdontstop/pull/9). No scored Phase 3 run yet.**

The canonical machine-readable file is [`research/TRIAL_LEDGER.csv`](../research/TRIAL_LEDGER.csv). It contains **80 real historical outcomes** extracted directly from the *public GitHub Actions job log*, not invented placeholders:

- Source workflow: [Robustness Audit (No Trades), run 38108710763](https://github.com/Venloud/horsesdontstop/actions/runs/38108710763), job `114379585494`, executed 2026-10-11 UTC.
- Code generating scenarios: `bot/robustness_cli.py::compare_robustness` plus the `continuous` loop.
- **64** `independent_reset_window` trials: 4 coded strategy IDs × 2 risk percentages (1/2) × 2 adverse slippage bps-per-side (10/30) × 4 consecutive historical 105-session windows, each starting with an **independent $500 cash allocation**. Not the cash history of one investor.
- **16** `continuous_no_resets` trials: same 4 × 2 × 2 grid, full **491 trading-session** post-warmup horizon from 2024-10-24 through 2026-10-09, each with a separate $500 initial allocation and profits/losses reinvested from the first trade.
- All 80 have **actual source-log dates, metrics, net P&L, returns, drawdown and trade counts**. `profit_factor` and `win_rate_pct` come directly from the source; missing values remain blank rather than invented.
- Portfolio universe identifier `BROADER_SYMBOLS_15_SURVIVORS` denotes exactly `AAPL, MSFT, NVDA, AMD, TSLA, JPM, BAC, XOM, CVX, KO, WMT, DIS, F, PFE, INTC` from `bot/robustness_cli.py`. It is handpicked survivors, not a point-in-time all-stock index.
- The historical engine's `slippage_bps` is **applied adversely at entry and exit**, using `entry=open*(1+slip)` and `exit=reference*(1-slip)`; the CSV column is thus `slippage_bps_per_side`.
- Historical `spy_price_return_pct` omits dividend reinvestment and is **NOT** the owner-approved future matching-contribution, total-return benchmark. These legacy runs simulated **$0 external deposits**.
- `max_daily_close_drawdown_pct` is based on end-of-day marked equity and **understates possible worst intraday/overnight risk**. Neither stop gaps nor partial fills are guaranteed in this historical model.

## Contamination / multiple tests

All 80 rows have `validation_status=legacy_exploratory_preinspected` and `prior_results_seen=true`. **They are not 80 independent datasets**. Their historical periods overlap, their strategies are correlated and their results influenced subsequent strategy choice. Use the ledger's full number of attempted variations, not the count of winners, for a defensible multiple-testing correction. The previously inspected earlier five-stock and 70/30 research comparisons must also be inventoried and counted in a later dated legacy append before an honest deflated-Sharpe gate. **Do not assert that 80 is the total number of all historic trial attempts.**

This ledger is a historical baseline for disclosure, not confirmation that any strategy has market edge. Even impressive historical return percentages are **not Alpaca-paper account profits**.

## CSV schema and retention contract

| Column | Meaning |
|---|---|
| `trial_id` | Stable, immutable `LEGACY-001` through `LEGACY-080`, never recycled |
| `source_run_id`, `source_job_id`, `source_workflow_url` | Direct trace to GitHub Actions evidence |
| `design` and `window` | One separately reset window or one full nonreset continuous run |
| `first_day`, `last_day`, `sessions` | Trading-day evaluation span as reported, not calendar-day duration |
| `universe`, `strategy`, `risk_pct`, `slippage_bps_per_side` | Fixed historical scenario config |
| `trade_count`, `return_pct`, `net_pnl_usd`, `final_equity_usd` | Model results, not live account state |
| `max_daily_close_drawdown_pct`, `profit_factor`, `win_rate_pct`, `spy_price_return_pct` | Other original diagnostic outputs |
| `first_1000_equity_day` | First marked-equity date reaching $1,000, if ever, **not** compounding activation |
| `validation_status`, `prior_results_seen` | Warn that the result was inspected before registration |

## How future experiments will be recorded

Before running a scored Phase 3 experiment, append a new uniquely identified prospective row or a corresponding immutable versioned output in a new ledger revision, with:

1. `trial_id`, UTC timestamp, responsible agent, **commit SHA**, dataset manifest and **SHA-256**, fixed universe/version, candidate ID, exact parameters, number of variants already tried, fold identifiers, execution/cost assumptions, random seed, benchmark deposit schedule, and validation type.
2. A committed planned status **before** reading output. Once finished, append results and source run/artifact URL in a later commit, **never replace a losing trial with a winning retry**.
3. Any code/data bug fix after viewing scored results is a new version/trial and can invalidate the claimed untouched holdout. Failed runs and aborted experiments count toward the transparent audit ledger. Never erase rows from historical commits.
4. A trial that uses the **already inspected** 2024-10-24 to 2026-10-09 history may be `prospective_discovery` or `legacy_exploratory`, **never** `scored_holdout` simply because the script was re-executed.
5. Record passive SPY **total-return plus exactly matching actual contributions at identical timestamps** once those inputs are validated. Keep hypothetical $100/month scenarios **separately labelled unscored**.
6. Source data with personal brokerage statements, API keys, passwords, DB URI, or account number is **not** permitted in this public repo. Store only non-sensitive aggregate/scenario data.

New prospective columns/manifest schema must be approved and committed before the first scored run. Do not modify/relabel the source 80 rows to make retrospective backtests seem independent.

## Reproducibility checks

```bash
python -m pytest -q tests/test_phase0_artifacts.py
# Existing read-only research and unit tests remain available:
python -m pytest -q
```

Both commands use local fixtures and CSV/Markdown; no Alpaca broker orders or Neon switches are ever touched. A passing Phase 0 artifact test does **not** mean Phase 3 strategies passed the economic thresholds.
