# Phase 0 pre-registration (frozen rules for scored backtests)

**Status:** DRAFT until the owner merges this PR. No scored backtest may run
before this file is merged. After merge, any change needs a new dated version
section, and every result states which version scored it. Research only:
PAPER orders stay hard-disabled and nothing here authorizes live money.
(The $500 real-money start is a plan, not permission to fund or trade.)

## 1. Candidates (six core)
1. `momentum_volume` — baseline
2. `trend_following` — control
3. `high_52_week`
4. `sector_leadership`
5. short-term reversal (new)
6. ETF rotation / dual momentum (new)

Deferred, not run: overnight drift, post-earnings drift. Not built: intraday
breakouts. A deferred candidate enters only via a new version of this file.

## 2. Risk
- Maximum risk per order is 1% of current equity. Lower (e.g. 0.5%) is allowed
  when justified and pre-declared. Never raise above 1% to force a trade.
- 2% is not scored.

## 3. Benchmark and deposits
- Benchmark: an index fund (SPY unless changed in a new version) receiving the
  **same deposit schedule as the strategy account** (deposits included on the
  same dates). Default scored scenario: no deposits; if deposits are
  simulated, both accounts get identical ones.
- Also report a separate "$100/month into the index fund" savings scenario,
  labelled as savings, not as a strategy comparison.

## 4. Pass criteria (all must hold)
| # | Criterion |
|---|-----------|
| 1 | Evidence size: >= 300 sufficiently independent trades. Low-frequency strategies (e.g. ETF rotation) use the exception in section 5. |
| 2 | Expectancy >= +0.15R after 30 bps round-trip cost, bootstrap 95% lower bound > 0 |
| 3 | Beats the 95th percentile of a placebo (random entries, same exits and sizing, >= 1000 draws) |
| 4 | Deflated Sharpe probability >= 95%, using the total trial count in the ledger |
| 5 | Positive in >= 60% of calendar years and not negative in 2022 |
| 6 | Max historical drawdown <= 15% at <= 1% risk (matches the live 15% stop) |
| 7 | Gap/stop-slippage stress: re-run with overnight gaps through stops and stop fills worse than trigger; drawdown stays <= 15% in the base stress and is reported for a severe stress. A historical 15% does not guarantee live <= 15%. |
| 8 | Parameter neighbors keep >= half the edge |
| 9 | Still positive at 2x costs (60 bps) |
| 10 | ETF-only / out-of-subset check stays positive |
| 11 | Beats the benchmark of section 3 on risk-adjusted terms |

At most two strategies advance to Phase 4.

## 5. Low-frequency exception (to finalize before the first scored run)
A strategy that cannot reach 300 trades by design may instead qualify with:
long history (proposed >= 15 years of data covering at least one drawdown
regime), the same bootstrap and placebo tests on period-level returns, and
criteria 5-11 unchanged. The owner fixes the exact history length in this PR.
Using the exception must be declared per strategy here, never after seeing
results.

## 6. Trial ledger
`docs/TRIAL_LEDGER.md` lists every strategy/parameter combination ever scored,
including failures. The count feeds the deflated Sharpe. It is append-only.

## 7. Open items for the owner
- Confirm the history length in section 5.
- Confirm SPY as the benchmark and the cost model (30 bps / 60 bps).
