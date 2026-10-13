# Six-core simulator adapters, synthetic-only, 2026-10-13

Preregistration Section 3 remains frozen: six executable research
families have **19 parameter variants** total; the other **two**
`overnight_drift` variants remain **research-only and cannot be adapted
to broker execution** without verified auction mechanics.

`phase2_simulator_adapters.py` maps each frozen parameter tuple to a
next-open eligibility record, using completed prior bars. The existing
`bot/strategies.py` signal functions are reused for the four existing
baseline variants; `bot/phase2_research_signals.py` is reused for
reversal RSI(2) and total-return dual momentum.

`phase2_synthetic_portfolio.py` is a one-shared-cash, one-whole-share
position-at-a-time test runner with **1%** max stop-distance risk, **30
bps** adverse slippage on BOTH entry and exit, 3% initial stops, 5%
trailing stops, reversal 1/3-session maximum holding exit, and next-month
ETF rebalance. It refuses any call not explicitly marked as a **synthetic
fixture**. Daily-close equity limits block later entries, not phantom
same-tick guaranteed exits. Results are NOT scored historical research.

The strategy variants rely on caller-provided data/calendar/point-in-time
attestations. These synthetic booleans **do not prove actual provenance**,
as-of source availability, market holiday handling, commissions, account
equity completeness, time-of-day order feasibility, 2022 coverage, or
real dividends/splits. The frozen ETF universe has both XLC (2018)
and XLRE (2015), so its full-history start may be insufficient for the
required 10-year low-frequency criterion. **Do not silently drop ETFs,
change parameters, shorten the horizon, or interpret this as a pass.**

No data manifest freeze, Phase 3 trial, Alpaca/Neon call, production
migration, release switch edit or change to `PREREGISTRATION.md`.
