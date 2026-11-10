# HorsesDontStop Intraday Strategy Research

**Date:** November 10, 2026  
**Scope:** Read-only strategy and execution review; offline historical research only. No Neon writes, broker order calls, stop changes, strategy deployment, or release-control changes were made.

## Executive result

**Recommendation: do not replace or expand the production strategy based on this experiment.** None of the tested candidates demonstrates robust positive risk-adjusted performance after the tested cost stress. The best selected out-of-sample variant finished only $1.98 above its $500 start, while its training period and stress case lost money. This is not evidence of a tradeable edge.

The production `momentum_volume` signal was **not directly backtested** here. The intraday research engine is a separate hypothesis simulator. Its `momentum_daily_eod` candidate is only a daily momentum/volume proxy. Do not interpret any row below as production strategy validation or as permission to trade.

## Existing system audit

The current `bot/strategies.py::signal_score("momentum_volume", ...)` accepts only completed daily bars. It needs at least 22 bars, checks positive 21-session close momentum and latest volume at least 1.15 times the prior 20-session mean, and scores momentum times capped relative volume. The executor later ranks candidates, checks the live regular-session clock and account state, independently reconciles prior orders, fetches fresh Alpaca IEX quotes, and applies quote, affordability and risk gates before preparing a proposal.

The inspected live quote policy rejects spreads above 75 bps or quotes older than 45 seconds, uses a capped buy limit, sizes whole shares using the 1% equity stop-risk budget, cash, buying power, displayed ask size and fee reserve, and creates an OTO/GTC request for inspection. The source expressly states that a stop is not a fill guarantee and partial-fill gaps remain a risk. The execution gateway and account safety controls are outside this research experiment; no changes were made to them. The current report assumes all owner controls remain as configured, including kill switch ON and submissions OFF.

### Production fit

The executor is built around one current approved signal and a fresh quote/risk check. ORB, intraday VWAP reclaim and intraday momentum are not wired production strategies. Do not add them to the executor based on these results. For the $500 whole-share account, eligible notional is often the binding constraint; a statistical signal is not executable if one share breaches available cash, stop-risk or displayed-liquidity limits.

## Open-source review

| Project | What is useful | Published results and limitations | License / adoption |
|---|---|---|---|
| [sam-bateman/trading-orb](https://github.com/sam-bateman/trading-orb) | Event-driven ORB example; next-bar entries, per-share cost assumptions, daily loss controls, and acknowledgement of an early overfit period. | README self-reports 4,292 trades over 2016–2026, PF 1.31 and Sharpe 2.47 on $100,000. These are author claims, not independently reproduced here, and its multi-stock/short-capable assumptions do not fit this $500 long-only account. **The README and pinned source disagree materially:** README says 20-minute range, 10:00–11:30 entries, 0.75x range target/0.50x stop and 15:50 flatten; `src/orb_strategy.py` sets 30 minutes, 10:00–14:30, 1.5x target/0.75x stop and 15:45 close. | MIT. Studied at pinned commit `23f2400a696296cddb00166dd429c0d048bee1bb`; no code copied. Parameter discrepancy means do not treat the README metrics as a reproducible specification. |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Strong reference for event-driven simulation and pluggable fill, fee and slippage models; source includes equity fill models and volume-share slippage models. | It is a trading/backtesting engine, not a particular profitable strategy or proof that an HDS strategy works. Its realism depends on data and selected models. | Apache-2.0. Use as a design reference; migrating the project to the full engine would add operational complexity without evidence it improves this small-account use case. |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Fast vectorized research and portfolio analytics; useful for broad exploratory parameter sweeps. | It is a research framework, not a documented edge. Vectorized fills need careful temporal and execution assumptions for stops, partial fills and order priority. | The captured license is Apache-2.0 with Commons Clause, including a restriction on selling the software. Do not vendor or depend on it without license review. No code copied. |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | Accessible event-style backtest API and detailed result statistics. | README’s GOOG SMA sample is illustrative, long-horizon and not an independently validated intraday strategy; its sample includes commission but is not a realistic HDS spread/stop model. | AGPL-3.0 in the captured source. Do not copy code or adopt as a dependency without license review. |
| [mericck/QQQ-Opening-Range-Breakout](https://github.com/mericck/QQQ-Opening-Range-Breakout) | The README describes a chronological development/OOS split, walk-forward analysis and transaction-cost sensitivity, and explicitly distinguishes gross from net performance. | README reports 906 primary OOS trades and PF 1.146 **before costs**, then reports fixed-cost scenarios. Results and dataset were not independently reproduced; the strategy trades QQQ/TQQQ and does not establish whole-share feasibility with $500. The captured source manifest includes its README only, so its implementation and license were not verified. | Treat as a research-design reference only; no code copied. Verify license and reproduce before reuse. |

LEAN and vectorbt demonstrate useful backtest engineering patterns, not profitability. Backtesting.py's sample return and any repository author-reported performance should not be treated as evidence of future returns. This review copied no third-party code.

## Backtest design and data

The offline-only simulator is in `research/intraday_20261110/engine.py`; it imports no broker or database client. It evaluates five long-only hypotheses with a shared $500 cash balance per run, whole shares, at most one position and one entry per session, next-bar-open entries, 1% stop-risk sizing, 15:50 ET forced exits, stop-first same-bar ambiguity, gap-aware stop fills, and trailing-stop updates applied from the next bar. The tests cover malformed timestamps/OHLCV, duplicates, incomplete sessions, warm-up, next-bar entries, stops, trailing stops, EOD exit and affordability.

The input is 236,922 Alpaca historical IEX 5-minute bars for AAPL, AMD, F, KO, SPY and WMT from 2024-01-01 through 2025-12-31 (adjustment `all`). The SHA-256 of `iex_5m_2024_2025.json` is `126dca9b4ccd26e26fc775ec72885c6b2006cce5f0594b9f574e5d514fb75334`. The run accepted 2,867 complete symbol-days and excluded 139 symbol-days with missing/short regular sessions.

Chronological split: 2024-03-01 through 2024-12-31 training (211 portfolio sessions after warm-up); 2025-01-01 through 2025-12-31 test (249 sessions). For each strategy, one of four stop/target/trailing combinations was selected by the **training daily Sharpe only**, at baseline cost settings. That selected configuration was evaluated once on the 2025 test split; no test-period retuning was used.

Baseline modeled cost on each side is half of a 5 bps spread plus 5 bps slippage, plus $0.005/share commission. Stress is half of a 20 bps spread plus 30 bps slippage per side, with the same commission. These are **scenario assumptions, not measured historical quotes, Alpaca fills, or verified current fees**. Because the dataset is IEX-only OHLCV, it does not supply consolidated NBBO or queue depth; the model cannot establish actual spread, market impact or stop execution. The simulator caps participation using 1% of the previous IEX bar's volume, which is a conservative heuristic, not observed executable depth.

## Out-of-sample comparison

Rows are ranked by selected 2025 test daily Sharpe; all metrics are net of the simulator's modeled costs. “Stress final” is final simulated equity under the 20 bps spread / 30 bps per-side slippage case. Parameters are the training-selected stop, target and trailing-stop settings.

| Rank / candidate | Selected stop / target / trail | Train final | Test final | Trades | Win rate | Profit factor | Expectancy / trade | Max drawdown | Test daily Sharpe | Stress final |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1. Daily momentum/volume proxy, EOD | 3% / 2R / off | $493.69 | $501.98 | 80 | 46.2% | 1.035 | $0.025 | 4.04% | 0.129 | $438.78 |
| 2. Plain ORB | 3% / 2R / off | $463.85 | $473.84 | 187 | 44.9% | 0.751 | -$0.140 | 6.87% | -1.353 | $424.78 |
| 3. Intraday momentum | 1% / 2R / trail at 1R | $428.89 | $436.10 | 77 | 36.4% | 0.595 | -$0.830 | 15.10% | -1.936 | $425.11 |
| 4. ORB + VWAP + volume | 1% / 1.5R / off | $435.28 | $441.13 | 59 | 35.6% | 0.540 | -$0.998 | 15.09% | -2.093 | $426.15 |
| 5. VWAP reclaim | 3% / 2R / off | $465.33 | $460.22 | 140 | 41.4% | 0.545 | -$0.284 | 10.32% | -2.433 | $426.73 |

The top row is not a robust winner: its training equity fell $6.31 and training Sharpe was -0.594; its first two 2025 quarters lost $7.81 combined and its stress final equity is $438.78 (a 12.2% loss). Its nominal test expectancy is approximately 2.5 cents per trade and profit factor is only 1.035. Other strategies lost in the 2025 test split. No strategy passes a reasonable positive expectancy, profit-factor, Sharpe and cost-stress bar together.

## Interpretation and limitations

1. **Not the production strategy.** `momentum_daily_eod` uses an intraday simulator's prior-day momentum/volume flag and EOD exit; it is not `signal_score("momentum_volume")` plus production live quote sizing and broker lifecycle.
2. **Tiny, selected universe.** Six hand-picked current tickers create survivorship/selection bias and do not support a general universe claim. Results are symbol-dependent; the top row's 2025 P&L is concentrated in F and loses on KO, WMT and AMD.
3. **IEX is not consolidated US market data.** Alpaca documents free live IEX as a single exchange feed; IEX bars/quotes are not consolidated SIP/NBBO and can omit intervals with no qualifying IEX trade. This matters especially for relative volume, spread and fill realism. See [Alpaca's market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
4. **Completeness filter.** The simulator drops any symbol-day not containing every expected 5-minute regular-session bar. It improves data integrity but uses completed-session knowledge to decide whether that day is included; it is a selection limitation and must be redesigned for a prospective-grade evaluation.
5. **Execution is simplified.** There are no quote-level bid/ask histories, order-book queues, latency, partial fills, halts, broker rejects, buying-power reservation effects, child-stop behavior, or actual fee schedules. OHLC bars cannot determine the ordering of intrabar stop/target touches; stop-first is conservative but not an execution guarantee.
6. **Short sample and multiple testing.** One training year and one OOS year are insufficient to establish regime robustness. Four stop/target/trail settings per strategy were tried; selecting the best train Sharpe creates some selection bias. This was not a full rolling walk-forward or bootstrap study.
7. **Risk metrics are model-dependent.** Daily Sharpe uses daily portfolio returns and is unstable on one test year; drawdown includes simulated intraday marks but is still based on five-minute bars and can understate gaps.

## Recommendation and next research work

- Keep the existing strategy and all execution/security controls unchanged. Do not deploy ORB, VWAP or new exit rules from this result set.
- Before recommending a signal change, add an exact adapter for the production `momentum_volume` decision path and use point-in-time eligible symbols. Preserve chronological train/validation/test periods with the final period sealed until rules are frozen.
- Use historical quote data (including bid/ask and sizes) where account entitlements permit; report IEX-only and consolidated SIP results separately. No paid feed is assumed. Calibrate spread/slippage from actual observed PAPER fills only after sufficient samples exist; otherwise keep broad stress bands.
- Test execution rules separately from signal alpha: marketable limits, stop gaps, intrabar ambiguity, GTC OTO stop lifecycle, partial fills, cancellation/rejection, and restart/reconciliation. Any stop is risk control, not a guaranteed maximum loss.
- Extend the test window across multiple regimes and use walk-forward folds; report per-symbol and per-quarter outcomes, confidence intervals, trade count, net expectancy, profit factor, drawdown, daily Sharpe and a cash benchmark. Fix all assumptions before opening a final untouched holdout.
- Keep order submission disabled. A strategy result is only a research artifact; it does not alter the WMT position, its broker stop, Neon permissions, or owner approval boundary.

## Reproduction

From the repository root in PowerShell, using the project virtual environment:

```powershell
.\.venv\Scripts\python.exe research\intraday_20261110\engine.py
Push-Location research\intraday_20261110
..\..\.venv\Scripts\python.exe -m unittest -v test_engine
Pop-Location
.\.venv\Scripts\python.exe research\intraday_20261110\compare.py
```

The historical data fetcher is a GET-only Alpaca market-data client and is not needed to reproduce the checked-in snapshot. Its run would require existing PAPER data credentials in the environment; do not print or commit them. This review executed the 120-scenario simulation and 9 focused unit tests successfully. A full product suite was not rerun because production source was not changed.

## Source notes

- [Alpaca IEX vs SIP and bar behavior](https://docs.alpaca.markets/us/docs/market-data-faq)
- [trading-orb README at reviewed commit](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/README.md), [source strategy](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/src/orb_strategy.py), [MIT license](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/LICENSE)
- [LEAN engine](https://github.com/QuantConnect/Lean), [equity fill model](https://github.com/QuantConnect/Lean/blob/3ca6e9b03f5aba806d32c6e446baf696233864e1/Common/Orders/Fills/EquityFillModel.cs), [slippage models](https://github.com/QuantConnect/Lean/tree/master/Common/Orders/Slippage), [Apache-2.0 license](https://github.com/QuantConnect/Lean/blob/3ca6e9b03f5aba806d32c6e446baf696233864e1/LICENSE)
- [vectorbt README](https://github.com/polakowo/vectorbt), [captured Commons Clause license](https://github.com/polakowo/vectorbt/blob/07f5e85e83a5286c396f4ecfd55dfaa05f25442d/LICENSE.md)
- [backtesting.py README](https://github.com/kernc/backtesting.py), [AGPL-3.0 license](https://github.com/kernc/backtesting.py/blob/ca2e2611621e472542ba90f7243a1fa06a7d7108/LICENSE.md)
- [QQQ ORB report](https://github.com/mericck/QQQ-Opening-Range-Breakout/blob/27fab3095be85c1a2f5ce15c959362d4c7abf601/README.md)
