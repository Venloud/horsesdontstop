# Phase 2: data provenance and scoring lock, first implementation

**Prepared:** 2026-10-11. **Not a scored backtest. No brokerage orders.**  
Implementation on PR branch: `bot/phase2_dataset_gate.py`, `research/phase2_manifest.json`, `tests/test_phase2_dataset_gate.py`.

This milestone is deliberately narrow: it creates a reproducible
**fail-closed research-input gate**. It does NOT add new trading algorithms,
claim adequate market data coverage, prove an edge, authorize cloud spending
or submit a paper or real-money broker order.

## What the offline gate does now

- Preserves owner-approved Phase 0 **seven candidates**, **21 total
  parameter variants**, **1% maximum order risk**, **15% historical
  drawdown**, **30bps adverse slippage on each side**, **60bps/side stress**,
  **300 sufficiently independent trade events or 10-year low-frequency
  allowance**.
- Verifies real **SHA-256 checksums** of four locally available CSV
  artifacts: historical bars, point-in-time universe membership,
  total-return SPY benchmark and an **actual contribution history
  shared between active and passive accounts**.
- Rejects absolute paths, parent-path traversal, symlinked assets,
  missing files or hashes, missing CSV header columns, absent
  point-in-time/delisting documentation, altered approved research
  thresholds, missing future-safe holdout chronology and missing
  bootstrap/placebo random seeds/replicate counts.
- Requires the prospective scored holdout to start **after
  2026-10-09**, the last previously inspected historic run's date,
  and requires an explicit embargo gap, no prior result inspection,
  and chronological separation from discovery data.
- Requires exact dataset source/feed/fetch time, eligibility
  metadata, corporate-action and dividend adjustment choices,
  licensing review and human-reviewed dataset integrity.
- Requires a separate synthetic harness self-test signoff. This
  flag is NOT proof by itself that a vendor's historical membership
  or dividends are actually point-in-time. Reviewers must inspect
  the underlying sources rather than simply flip flags.

Current manifest `research/phase2_manifest.json` is **intentionally
`status=draft`** with missing real dataset hashes/paths and folds.
Running this now must exit **2** with `PHASE 2 BLOCKED`, which is
the **correct and expected safe result**, not a broken workflow.

## What to do next, in order

- [x] Freeze Phase 0 owner selections in
  [`PREREGISTRATION.md`](PREREGISTRATION.md), merged in
  [PR #9](https://github.com/Venloud/horsesdontstop/pull/9).
- [x] Archive 80 preinspected historical scenario results,
  **not** 80 pristine independent experiments.
- [x] Add an **offline manifest validation gate** and unit tests.
- [ ] Find lawful/feasible source coverage for full 2022 regime,
  **10 years** for low-frequency candidates, delistings and
  historically correct sector membership. Alpaca IEX daily data
  with only ~3 years in prior exploratory runs is **not enough**.
  Do not spend on a new feed without user approval.
- [ ] Freeze a data-universe manifest (target >=100 stocks if
  covered, ETFs, dates, point-in-time eligibility, dividends
  and actions). Fetch and hash genuine research artifacts.
  Large/broker-private datasets need managed/private storage,
  not public GitHub uploads.
- [ ] Independently validate the actual bars, benchmark total-return
  histories, symbol membership, corporate actions, duplicated bars
  and missing sessions. The current gate checks **file hash and
  CSV envelope only**; a later *semantic* parser must check values.
- [ ] Decide/record exact discovery windows and **untouched**
  prospective holdout start/end, embargo, seed values,
  bootstrap block lengths, and actual matching deposit convention
  **BEFORE inspecting any scored candidate result**.
- [ ] Implement all three research-only candidates:
  `short_term_reversal`, `etf_dual_momentum`,
  `overnight_drift`, each with separate strategy-specific
  cash/entry/exit modeling. Overnight drift requires
  executable pre-close signals, not after-close clairvoyance.
- [ ] Build the synthetic leak/planted-edge/random-entry
  statistical validation harness and test its calibration.
- [ ] Once all prerequisites pass and owner reviews the frozen
  data manifest, allow *separately versioned* scored Phase 3
  comparisons. It is perfectly legitimate for **no strategy** to
  meet all promotion gates.

## How to check, without executing anything

```bash
python -m pytest -q tests/test_phase2_dataset_gate.py
python -m pytest -q
python -m bot.phase2_dataset_gate  # EXPECTED exit 2: incomplete source manifest
```

The gate's `valid` result would mean the **required evidence
fields exist with matching local file hashes**, not that backtest
results are good or even statistically validated. This milestone
never invokes `alpaca.trading.client.TradingClient`, never calls
`submit_order`, never changes Neon control, and has no cron.
Do **not** make `status=frozen` merely to silence the diagnostic.

## About the user's Alpaca app-directory upload

The attached October 11 Alpaca marketplace catalog lists tools
such as TradingView (charting), QuantConnect (algorithmic research
and backtesting), TradersPost and SignalStack (alert-to-order
automation), and the official Alpaca CLI. These are **third-party
alternatives or supplements**, not broker orders in horsesdontstop,
and not proof of zero hosting cost or persistent risk supervision.

An external platform such as QuantConnect can be assessed as a
**separate independent research benchmark**, after confirming
licensing, historical data coverage and current pricing. Do not
send the existing restricted database credentials or authorize
broker order execution to a third-party app without asking the
owner. TradingView/TradersPost are not needed for Phase 2's
offline dataset hygiene checks.
