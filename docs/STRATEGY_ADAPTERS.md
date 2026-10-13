# Strategy adapters, 2026-10-13

`bot/strategy_adapters.py` turns grid variants into `run_portfolio`
arguments using only the existing signal functions. Tests:
`tests/test_strategy_adapters.py`. Offline, synthetic data, nothing scored.

## Grid coverage (21 frozen variants)

| Status | Count | Variants |
|---|---:|---|
| implemented | 19 | all 12 coded-strategy variants; 4 reversal; 3 ETF momentum |
| research_only | 2 | overnight_drift (no adapter) |

`bot/strategies.py` hard-codes only the defaults (21/1.15, 20/50, 2%, 63).
The 8 other points use parameterized copies of the same formulas
(`_generic_score`, first written in PR #18 and kept here after I compared them
with `signal_score`). A test checks they return identical scores at the
default parameters; the other points rely on that equivalence plus the
look-ahead audit, not on an independent source.

## Exit policies added to `run_portfolio` (defaults keep legacy output)

- `max_hold_sessions`: reversal sells at the open N sessions after entry,
  earlier on the standard 3%/5% stop.
- `rebalance_days`: ETF momentum sells at each month's first open and may
  buy the new leader at that same open.
- `use_stops`: on/off. Sizing still uses the modeled 3% stop as 1R.

## Raised, not decided

1. **XLC (listed ~2018-06-19) and XLRE (~2015-10-08) cap ETF history.**
   The signal blocks all selection until every one of the 12 ETFs has
   lookback+1 bars. First usable rebalance is about 2018-10 / 2019-01 /
   2019-07 for the 63/126/252 lookbacks, so as of 2026-10-09 there are only
   about 8.0 / 7.8 / 7.3 years: **all three ETF variants fail the 10-year
   low-frequency exception** with the frozen universe. Dates are approximate
   (weekday calendar); confirm against the frozen vendor data. Options
   (owner's call, dated amendment): wait, change the universe pre-scoring,
   or accept "insufficient coverage".
2. Reversal "hold 1 or 3 sessions": this adapter exits at the next open
   after N sessions. Confirm.
3. ETF momentum: the preregistration doesn't say whether the 3%/5% stops
   apply between rebalances. The adapter requires an explicit choice.
4. ETF sizing at 1% risk with a 3% modeled stop puts about a third of
   equity in the ETF; the rest sits in cash.
