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


## Semantic CSV validation, staged in draft PR #10

In addition to source paths, headers and SHA-256, the offline gate now
invokes `bot/phase2_semantic_validation.py` on every verified input.
The code reads the *actual pinned local CSV contents*, rejecting:

- Missing SPY price history, duplicate or out-of-order ticker sessions,
  nonfinite/negative prices, inconsistent OHLC, negative or fractional
  share volume and malformed CSV fields.
- Unsorted or repeated total-return benchmark dates, zero/nonfinite
  total-return indices and lack of a common date with SPY price bars.
- Missing stable security/source/snapshot identifiers, duplicate
  security IDs **or tickers within one snapshot**, mixed source IDs
  or knowledge timestamps for the same snapshot, competing snapshot
  versions for one day, unparseable knowledge timestamps, invalid
  eligibility states and incomplete membership snapshots.
- Duplicate/unsorted owner cash-transfer dates, nonfinite transfer values
  and fractions of a U.S. cent. **A header-only contribution CSV is
  valid for zero actual contributions**; do not invent monthly deposits
  merely to populate the file.

Every failure is returned as a safe blocker like
`asset_semantic_invalid:bars`, never ignored because a hash matched.
The synthetic fixture tests include broken data with *recalculated*
matching SHA-256 to prove it cannot pass by relying on checksums alone.

**Limits:** These checks do not certify genuine vendor publication time,
license to export data, complete delistings, adjustment quality, exact
real-world cash deposits, exchange-session completeness, or a pristine
future holdout. Review of those sources remains mandatory and the
production Phase 2 manifest is still **DRAFT/blocked**. No scoring or
brokerage actions are authorized by passing a semantic parser.

## Phase 2 historical stock eligibility: no hindsight list

A new **research-only historical universe selector** now lives in
[`bot/phase2_point_in_time_universe.py`](../bot/phase2_point_in_time_universe.py)
with synthetic [unit tests](../tests/test_phase2_point_in_time_universe.py).

**Never calculate historical eligibility from a current ticker list,
or from retrospective `first_eligible_day` / `last_eligible_day`
ranges alone.** Such lists can know in advance which stocks survived,
changed ticker, or delisted, and may bias the algorithm's selection.
The Phase 2 manifest now requires a complete as-known security
membership snapshot instead of those retrospective range columns.

### Required membership CSV columns

| Column | Meaning |
|---|---|
| `snapshot_day` | A **completed, previous NYSE trading session**, explicitly supplied by the market-calendar caller |
| `known_at_utc` | When this exact membership snapshot was *actually available* from the historical data source; must be time-zone-aware and before the decision |
| `snapshot_id` | Immutable ID for that complete day's membership snapshot |
| `security_id` | Vendor's **stable permanent security ID** through ticker changes; never join bars by ticker alone |
| `symbol` | Historical ticker **display label** as of the snapshot |
| `eligible` | Strict lowercase `true` or `false`, no missing/unknown statuses |
| `source_id` | Trace to a real reviewed provider/version; a synthetic `source_id` does not constitute verified data |
| `complete` | Strict `true`, representing a **whole, source-verified session snapshot**, not a conveniently selected set of survivors |

One row per stable security in a day's snapshot, exactly one snapshot
version per session, consistent `known_at_utc` and `source_id`.
Disallow duplicate security IDs, ticker collisions within one snapshot,
unknown eligibility, or multi-version mixing. The selector requires
the previous session specified by its caller, rejects same-day or
future snapshots and prevents silent use of a snapshot older than
four calendar days. This staleness check is not a substitute for
a verified NYSE session calendar (holidays/unscheduled closings).

When calculating a signal for the next session open, call
`select_historical_universe(records, snapshot_day=<verified previous
trading session>, decision_at_utc=<the actual next session decision
instant>)`. If the expected snapshot is absent, **do not trade,
do not substitute an ETF or current survivor list**. A ticker rename
may change the symbol displayed without changing `security_id`.

**Crucial evidence boundary:** A CSV file can *claim* a 2012 historical
knowledge timestamp even if somebody fabricated that value today.
The validator cannot prove historical vendor publication time,
membership completeness, stock splits, proper listing dates or
survivorship freedom just by examining its columns or SHA-256.
Independent source verification, provenance/licensing, corporate
action review and a correctly frozen `phase2_manifest.json` are
mandatory before any scored strategy comparison. The observed
QuantConnect SPY/FRC/AAPL tests are promising **samples**,
not a frozen complete historical market universe. We did not
download or redistribute QuantConnect's underlying licensed data.

### Synthetic-only example (NOT a genuine provider record)

```csv
snapshot_day,known_at_utc,snapshot_id,security_id,symbol,eligible,source_id,complete
2023-05-02,2023-05-02T20:10:00Z,example-may2,QC-EXAMPLE-FRC,FRC,true,synthetic-only,true
2023-05-03,2023-05-03T20:10:00Z,example-may3,QC-EXAMPLE-FRC,FRC,false,synthetic-only,true
```

These invented records demonstrate handling of a security that is
removed from the eligible universe *after* an observed delisting;
they cannot establish the timing when FRC was actually tradable
or a reliable membership feed. The real broker tradability,
halts, and overnight stop safety must be checked separately.

**Status:** Research-only implementation and offline synthetic tests
exist on draft PR #10. The production data manifest is still
`status=draft`, missing genuine vendor membership provenance and
complete source artifacts, and any scored Phase 3 trial remains
**BLOCKED**. Zero brokerage orders, no Neon write, no trading switch
changes.

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
- [ ] Freeze *complete prior-session point-in-time membership snapshots*, tied to a real historic-source knowledge timestamp and stable security IDs; `first_eligible_day/last_eligible_day` ranges are not sufficient on their own. Then freeze the data-universe manifest (target >=100 stocks if
  covered, ETFs, dates, point-in-time eligibility, dividends
  and actions). Fetch and hash genuine research artifacts.
  Large/broker-private datasets need managed/private storage,
  not public GitHub uploads.
- [ ] Independently source-verify the actual bars, benchmark total-return
  histories, symbol membership, corporate actions, duplicated bars
  and missing sessions. The new parser checks basic **OHLCV, IDs, timestamps, dates,
  and cash precision**. Still independently audit missing sessions,
  real vendor history, dividends, split adjustments and PIT truth.
- [ ] Decide/record exact discovery windows and **untouched**
  prospective holdout start/end, embargo, seed values,
  bootstrap block lengths, and actual matching deposit convention
  **BEFORE inspecting any scored candidate result**.
- [x] Implement pure offline eligibility signals for all three
  research-only candidates: `short_term_reversal`,
  `etf_dual_momentum`, `overnight_drift`. These are **not**
  executable trades or complete simulation implementations.
- [ ] Implement **separate strategy-specific cash, entry, exit, stop,
  dividend and transaction-cost simulation models**; overnight drift
  must prove executable pre-close signals and auction timing before
  any scored result, not after-close clairvoyance.
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

## Continuation: matched benchmark sessions and verified ETF calendar
The SPY daily-price session set must now exactly match its passive
total-return benchmark dates. Partial overlap is not sufficient.
Monthly ETF dual momentum also requires an ordered, unique sequence
of externally verified market sessions: next_session must immediately
follow completed_session. An attestation flag without actual session
dates is insufficient. These remain synthetic inputs, not verified
NYSE calendars. The real dataset manifest remains DRAFT and blocked.
