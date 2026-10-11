# Phase 0 preregistration: Project $10K strategy evaluation

**Prepared:** 2026-10-11 UTC  
**Owner-approved Phase 0 choices:** seven strategy candidates **including overnight drift**; an index-fund benchmark with **the same actual contribution amounts on the same dates** as the strategy portfolio; **15% maximum historical drawdown** at 1% risk; **300 sufficiently independent trades where applicable, with a long-history exception for low-frequency strategies**.  
**Status:** **PR FOR REVIEW**. No new scored Phase 3 comparisons may be run until this entire specification (including implementation parameters) is reviewed and merged into `main`. Amendments must be dated and counted as extra trials; they never rewrite an earlier run. Owner's approval of this draft's *choices* does not authorize actual paper orders, real-money orders, deposits, or changing Neon switches.

This is a prospective research evaluation protocol. Historical strategies and windows already examined in 2026 are **exploratory**, not independent confirmatory evidence. Phase 1 trading infrastructure can proceed separately with submissions disabled. This document is not financial advice or a promised path to $10,000.

## 1. Immutable operational separation and capital rules

- **Initial modeled/account equity:** $500. $10,000 is aspirational **total account equity**, not guaranteed profit. Simulated profit/loss changes later position size immediately; there is **no $1,000 compounding threshold**.
- **Maximum per-entry planned stop-distance risk for any real broker order (even PAPER): 1% of current equity**, with a smaller allocation allowed after a documented decision. Never automatically raise it to 2% to force affordability. Historical 2% scenarios remain archived as exploratory research only.
- Simulated execution uses **whole-share long-only** buying, actual affordable cash, **one portfolio-wide open position at a time** for comparability, no margin, no automatic deposits or withdrawals.
- Execution blockers include **3% daily loss** against a verified daily baseline, **15% peak-equity drawdown kill threshold**, broker/account reconciliation, live market/quote controls, GTC child stop verification and a genuinely persistent watchdog. Missing state remains a blocker. Historical max daily-close drawdown must also be **≤15%** (Section 6), although real losses may exceed it during gaps.
- Current production database `paper_control.kill_switch_active=TRUE`, `submissions_enabled=FALSE`; code release `PAPER_ORDER_SUBMISSIONS_RELEASED=False`. This Phase 0 PR is **docs/data/tests only** and may not alter those settings or contact Alpaca to send orders.
- Future real-money idea: owner has mentioned possibly beginning with $500 *later*. This is **not live-money authorization**. Amount affordable to lose entirely and future deposit policy still require separate confirmation. Existing optional $100/month maximum has **not** been expressly changed and cannot be converted to an automatic contribution assumption.

## 2. Exactly seven candidates, one passive benchmark

Use the candidate IDs below as stable keys in every trial. Only the **four existing** signal functions are coded in `bot/strategies.py`; the others must be independently implemented and unit tested in Phase 2 **before any new Phase 3 result is examined**. Extra strategies are not quietly added to this tournament.

| ID | Candidate | Hypothesis | Status |
|---|---|---|---|
| `momentum_volume` | Price momentum with an unusual completed-day IEX volume surge | Current coded baseline, already heavily examined |
| `trend_following` | A rising short-term moving average above a longer one | Current coded control |
| `high_52_week` | Positive price trend near a previous 52-week high | Currently coded |
| `sector_leadership` | Outperforming stocks in outperforming sectors | Currently coded; current mapping is incomplete |
| `short_term_reversal` | Buy a short-lived, oversold pullback in a confirmed longer uptrend | New; high-priority distinct hypothesis |
| `etf_dual_momentum` | Periodically hold the qualifying ETF with strongest absolute/relative trend | New; low-turnover, may need the long-history exception |
| `overnight_drift` | Investigate systematic close-to-next-open drift with strictly prior-known signals | New; **research only until overnight gap/stop and end-of-day order mechanics are proven** |

**Always report `spy_total_return` passive benchmark** for the identical date span and initial capital, including dividends when reliable total-return data exists. Matching owner deposits are applied **on the exact same date, amount and trading-day convention** to both the trading strategy and passive benchmark. No actual deposits means **$0** contributions in *both* comparators. If a contribution is made on a non-trading day, apply to both at the next available close/open using the same stated convention. A separate illustrative $100/month path may be disclosed only as a **non-scored sensitivity**, never substituted for unmade contributions. Benchmark must include comparable fees, cash drag, and distributions where feasible; if no total-return feed exists, mark the benchmark **incomplete**, not a pass. Realized gains are not deposits. Report both **time-weighted** performance and **money-weighted** performance so added cash cannot be misread as trading profit.

**Parked:** `post_earnings_drift` pending point-in-time earnings/surprise availability. **Excluded:** intraday breakout/gap scalping, options, shorting, leverage, cryptocurrencies and pooled multi-strategy optimization. Adding any would require a preregistered new tournament rather than an undisclosed extra trial.

## 3. Predeclared parameter grid for implementation review

The options below define an **initial proposed grid, max 30 variants per candidate**. They are included for transparent owner/engineer review; no scored test is authorized until the PR is merged with implementable definitions. All indicators use bars **fully closed before** the proposed entry. An existing implementation default is marked. Any variation, robustness retry, regime filter, data-universe tweak or change in exit logic counts as an additional trial in the ledger.

| Candidate | Predeclared variants (cross product only where specified) | Max |
|---|---|---:|
| `momentum_volume` | Momentum lookback **21 (existing), 42** sessions × relative volume threshold **1.15 (existing), 1.50** vs prior 20 sessions; keep signal positive | 4 |
| `trend_following` | SMA short/long **20/50 (existing), 20/100, 50/200**; short SMA rising, last close above short | 3 |
| `high_52_week` | Last close within **1%, 2% (existing), 5%** of prior 252-session high; preceding 63-session return >0 | 3 |
| `sector_leadership` | Relative strength over **63 (existing), 126** sessions; stock return > mapped sector ETF > SPY return and sector absolute return >0. Sector classification **must be point-in-time** or marked biased | 2 |
| `short_term_reversal` | Prior completed close above rising **200-day SMA**, 2-session RSI at/below **5 or 10**; hold **1 or 3** sessions max, exit earlier on standard stop; a signal requires a down close and positive 200-day trend | 4 |
| `etf_dual_momentum` | Eligible sector ETFs + SPY, **monthly** rebalance at next session open; rank trailing total return over **63, 126, 252** completed sessions; if best trailing return ≤0 remain in cash. Equal calendar/month-end convention across variants | 3 |
| `overnight_drift` | Buy next eligible closing auction and exit at next regular-session opening auction; either **no regime filter** or **completed 20-session SPY return >0**. Do not invent fills at the close using information only available after the auction | 2 |

**23 parameter variants**, before universe/cost/window variations. Count *every* selection and rerun as its own attempted test, not just the final winner. Current historical 80 scenarios also remain in the trial register because they informed design. A nominal 23 variants tested across many universes, windows and cost levels constitutes *more* than 23 attempts. The preregistration does **not** imply those new strategies are implemented, that the options are profitable, or that each variation has enough trades.

**Fixed baseline exits for comparable daily-bar stock strategies:** existing modeled initial 3%-below-reference stop and 5% trail, one position, adverse entry/exit slippage, no forced +20% take profit, gaps filled at the worse executable opening price. Exceptions for strategy-defining exit timing (fixed reversal max hold, monthly ETF rebalance, and the overnight close-to-open hypothesis) are recorded as different exit policies, compared with appropriate matched placebo exits and disclosed rather than pooled naively with daily swing statistics. The existing DAY LIMIT OTO order *preview* and unverified GTC child do not supply a proven real execution model.

## 4. Dataset, split discipline and universe

1. **Contaminated history:** the preexisting 2024-10-24 through 2026-10-09 continuous evaluation period and overlapping four ~105-session windows were already inspected. Earlier 70/30 strategy holdout was also inspected. **Neither may be represented as pristine unseen confirmation**. Their 80 exact run records from https://github.com/Venloud/horsesdontstop/actions/runs/38108710763 are archived in `research/TRIAL_LEDGER.csv` as legacy exploratory trials.
2. **Discovery:** use strictly older history *if verifiably available*, with frozen datasets, split-adjustment method, point-in-time corporate actions and consistent symbol inclusion. Do not silently regard today's surviving stock list as a historical constituent set.
3. **Confirmation:** reserve a chronologically later, **never-used** contiguous block after parameter choices and all debugging tests. Data from 2026-10-12 onward must be recorded prospectively and remain untouched for scored decisions until its declared horizon is reached. A code build/run that inspects results forfeits its "untouched" label for subsequent tuning.
4. **Universe:** target **≥100 liquid U.S. stocks plus sector ETFs** if entitlement permits. Freeze tickers and inclusion/liquidity criteria with a hashed, versioned file **before** Phase 3. Account for delistings and point-in-time membership; if not available, label survivorship risk, and report an ETF-only sensitivity.
5. **ETF-only analysis:** required as a robustness lens for any stock strategy that can meaningfully be mapped to ETFs; `etf_dual_momentum` is natively ETF-only. Exceptions must be justified *before* the run, not invented when results fail.
6. Record each data source and feed (IEX not SIP/NBBO), exact coverage/missing days, split/dividend adjustments, fetch timestamp/version and SHA-256 of cached data. Historical index+ETF total-return inputs must be consistent or benchmark comparisons are inconclusive.
7. If the available feed lacks **2022** or the **10-year history** necessary for the chosen exception, report **insufficient coverage** instead of waiving the condition. A free feed's actual historical reach is not assumed.

## 5. Reproducible execution, costs and trial accounting

- **Signal formation:** completed bars through day T only, execute no earlier than next eligible event after the information was available. No future volumes/corporate actions/close-of-day values may influence same-day fills. The `overnight_drift` closing-auction variant must prove its signal can be formed *before* the auction cutoff, or it remains **unexecutable research**.
- **Fill model:** conservative available cash, integer shares, limit/spread constraints and worst-side gap handling. If daily OHLC order chronology is ambiguous, assume the stop was hit first. Stock splits/dividends are reflected in the simulated equity and passive benchmark consistently; otherwise mark result unqualified.
- **Primary costs:** adverse **30 basis points per side** (entry and exit, clearly distinguished from round trip) plus applicable regulatory fees and any measured spread/fees not already included. Report **10 and 60 bps per side** as sensitivity analyses, and enforce the **double-cost 60bps gate** on the primary edge. No hidden commissions or impact claims.
- **Paper-vs-live distinction:** neither IEX latest quotes nor Alpaca PAPER fills guarantee realistic NBBO or slippage, partial fills or overnight GTC child persistence. The Phase 1 safety checklist must pass before even a supervised order.
- **Trial counting:** persist a unique immutable trial ID; code commit SHA; dataset hash; universe/version; strategy; full grid parameters; date and span; exit policy; risk level; fees/slippage; random seed; metrics; whether a human saw its result; failed test attempts. Never overwrite or omit failed, abandoned, negative or duplicate-run records.
- **Legacy historical trials:** the 80 initial scenarios are fully enumerated from the Oct 11 source job logs: 4 coded strategies × 2 risk levels × 2 slippage values × 4 independent-reset windows (**64**), plus those 16 continuous full-horizon scenarios. Their different windows share data/hypotheses; they are **80 strategy tests, not 80 independent datasets**.
- Before any new scored Phase 3 run: freeze seed(s), strategy and exit definitions, data manifest and universe, fold dates and minimum sample checks in a dated **addendum to this PREREGISTRATION**. If not ready, scoring remains **blocked**. Unit tests and deterministic synthetic self-tests are allowed but must not inspect scored returns.

## 6. Promotion thresholds (ALL must pass, not an average score)

1. **Sample adequacy:** ≥**300 sufficiently independent, executed post-cost trade events** for normal-frequency candidates. Trade counts across simultaneously moving correlated stocks and overlapping holdings are not independent; cluster by trading day and instrument/sector, report both raw count and effective sample evidence. If effective independence cannot be demonstrated, the result is **inconclusive**. **Low-frequency exception**: ≥**10 calendar years** of reliable, adjusted, varied market history including stress regimes, with event-time statistical tests and year/block uncertainty; no claim of 300 independent trades. No shortening that horizon merely because ETF signals are rare.
2. **Expectancy:** average realized, net-of-cost **≥ +0.15R per trade**, where 1R is entry's ex-ante modeled stop-distance *risk in dollars* (actual realized loss may exceed 1R). The **95% date-block bootstrap lower confidence bound >0R**, resampling trading-day blocks to preserve co-movement. Block length and random seed must be committed before the run; minimum 10,000 bootstrap samples.
3. **Placebo:** strategy's net expectancy exceeds the **95th percentile** of at least **10,000** placebo trials with random eligible entry dates and identical instrument universe, risk, holding/exit policy, stops, cash constraints and transaction costs. Hold exposure/risk distribution comparable. A placebo that does not match exposure invalidates the result.
4. **Multiple-testing penalty:** deflated Sharpe **probability ≥95%**, counting the logged ~80 examined historical tests and every additional candidate/variant, not just the selected winner. Use a return frequency consistent with the strategy; account for autocorrelation and correlated strategies; if input data cannot support a valid calculation, mark **not demonstrated** rather than manufacture certainty.
5. **Across regimes:** net-positive in **≥60% of eligible calendar years**, plus **nonnegative net result in 2022** when 2022 data is available (if absent, historical validation is incomplete). Show year-by-year exposure and performance, including losing years; no regime filter added after examining outcomes.
6. **Capital-constrained drawdown:** max marked-to-market **peak-to-trough drawdown ≤15% at 1% per-entry equity risk**, in the **same cash-constrained whole-share portfolio** used for benchmark comparison. Show *daily* marks and conservative stress for intraday gaps. A historical 15% limit does not guarantee future loss control.
7. **Parameter stability:** both sides of at least one neighboring parameter point retain **≥50% of the primary net expectancy**, when both neighbors exist; report the full preregistered grid and how edges are handled. No cherry-picking the single most profitable combination.
8. **Cost robustness:** net expectancy remains **positive at 60bps per side** with relevant fees; also report 10bps sensitivity and real broker paper spreads when available. More realistic estimates may only worsen the model, never retroactively redefine the primary 30bps assumption to save a failing candidate.
9. **Universe transfer:** equity-stock signals must survive an ETF-only robustness check where meaningful; if a stock-only signal cannot be applied to ETFs, the exemption and alternate survivorship-bias check **must be approved before looking at ETF results**. An adverse ETF result cannot simply be discarded.
10. **Benchmark:** against the passive index with **exactly matching actual contribution cash flows**, calculate net absolute and risk-adjusted outcomes, with disclosed taxes/fees assumptions. To qualify for promotion, a strategy must **beat passive after costs and comparable contributions** on preregistered scored periods, or match passive with **materially smaller drawdown** under an *owner-preapproved quantitative tolerance* written before confirmation. Until a quantitative "materially smaller" tolerance is agreed, **only outperformance counts as a pass**.

**Promote at most two** independent candidates to later paper testing. A narrowly qualifying candidate may be assessed with **≤0.5% order risk** only after a separate specific owner decision; **never >1%**. Any failed candidate is retired, not immediately tuned and run again on the same claimed holdout. A new variant requires a documented separate research project with fresh evaluation data. It is valid for **none** to pass.

## 7. Mandatory harness self-tests before Phase 3

- [ ] A deliberately future-leaking synthetic signal is **caught and flagged**, not treated as proof of performance.
- [ ] Random-entry tests center near **negative costs** with known analytical bounds.
- [ ] A planted synthetic known edge is recovered by the harness within a calibrated confidence interval.
- [ ] Replay the historical https://github.com/Venloud/horsesdontstop/actions/runs/38108710763 report to rounding using the same underlying inputs (do not silently refit).
- [ ] Dataset versions, strategy-grid counts, trial IDs and candidate names validated; double-run is either explicitly a new trial or deduplicated with an audit trail.
- [ ] Benchmark contribution schedule and fees produce identical cash deposits for passive and active scenarios.
- [ ] No scoring/validation operation calls `TradingClient.submit_order` or changes the Neon production control.
- [ ] Frozen parameter-grid definitions, embargo/look-ahead rules and benchmark tolerance are signed off **before** the first scored Phase 3 test.

## 8. Owner approval and subsequent change control

**Confirmed Oct 11, 2026:** seven candidates including overnight drift, matched actual deposits in passive benchmark, ≤15% historical drawdown, and 300 sufficiently independent events with a valid 10-year low-frequency exception. The operational **1% cap for order-placing** is also confirmed.

**Not granted:** permission to execute *any* real or paper order, permission to change `paper_control`, permission to release `PAPER_ORDER_SUBMISSIONS_RELEASED`, permission to attach a billing card, or permission to fund a live account. The submit adapter and existing live IEX scanner stay in their current safe modes.

**Review-required implementation choices in this draft:** specific parameter grids in Section 3, data universe manifest and fold dates, statistical seed and block sizes, and numeric tolerance for the smaller-drawdown benchmark alternative. These can be finalized during **PR review**, without examining future scored returns. The user's already-approved four decisions must not be silently changed. If additional review items are not resolved, a future Phase 3 runner must halt with `preregistration_incomplete`.

**Research run statuses:** `legacy_exploratory`, `synthetic_harness`, `prospective_discovery`, `scored_holdout`, `paper_execution_validation`. Only a preregistered scored holdout plus the rest of these gates is eligible to claim strategy advancement. Real fills in Phase 4 validate execution behavior, not statistically establish a market edge from a handful of trades.

**Next engineering step after this PR is merged:** build the Phase 2 harness and extend the ledger, while independently completing persistent-worker and stop-coverage safety work. Never claim Phase 0 evidence authorizes trading.
