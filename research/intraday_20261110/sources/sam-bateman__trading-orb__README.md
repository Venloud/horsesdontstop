# Intraday Opening Range Breakout Strategy

I built this over several months as a serious attempt to develop a systematic intraday strategy that actually holds up under scrutiny. The core idea is simple: trade breakouts from the first 20 minutes of the session. What made this interesting was the process — I ran around 8,000 parameter combinations, discovered my initial results were overfit to a tiny 41-day window, rebuilt everything on 12 months of data, and eventually validated the whole thing on 10 years of 5-minute bars (3.47M rows). Every year from 2016 to 2026 was profitable. I wasn't expecting that.

## What the strategy does

The opening range is defined as the high and low from 9:30 to 9:50 AM ET. If price breaks above that high with enough volume behind it, I go long. If it breaks below the low, I go short. Entries are only valid between 10:00 and 11:30 AM — after that, I leave it alone. Everything gets flattened by 3:50 PM, no overnight holds.

The target is 0.75x the opening range width, the stop is 0.50x, so the reward/risk is 1.5:1. After price hits 1x the range in my favor, the stop trails to breakeven. I hold up to 3 positions at a time. Volume filter requires 1.2x relative volume on the breakout bar — this turned out to be the most important part.

## Results (10-year backtest, 2016–2026)

4,292 trades across 20 stocks. Win rate of 50.3%, which sounds mediocre until you look at the rest of the numbers. Net PnL of +$30,989 on $100,000 starting capital, at $400 risk per trade — that's a CAGR of 2.71%, profit factor of 1.31, Sharpe of 2.47, max drawdown of $1,271 (1.27% of capital), Calmar of 2.13. Beta to SPY is essentially zero (-0.004). 19 out of 20 tickers were profitable. 75% of months were profitable. 100% of Monte Carlo trials (10,000) finished positive. The strategy survives 3x the slippage assumptions I used in the backtest.

The CAGR is small because the strategy uses very little of available capital — $400 of risk per trade against $100k cash means it's effectively trading on ~1-2% gross utilization most of the time. The honest way to read it: the strategy sat in cash 98% of the time and earned a Sharpe of 2.47 on the active piece. Returns scale with how much capital you're willing to commit; risk metrics don't.

The drawdown number is the thing I keep coming back to. $1,271 max drawdown over a decade is almost too good, which is why I spent a lot of time trying to break it. Net-PnL-to-max-drawdown ratio is ~24× — different number, same point: the strategy almost never gives back its gains.

## How I built it

**Universe selection.** Started by screening 150+ stocks for intraday tradability — volume, volatility, bid-ask spread. Narrowed to 20 names. Before writing a single line of strategy code, I analyzed intraday volume profiles and autocorrelation structure to figure out whether ORB would be trend-following or mean-reverting on these stocks.

**Data pipeline.** Built a clean pipeline in `intraday_data.py` to handle timezone weirdness, detect gaps, validate the data, and derive the columns I needed: VWAP, opening range, relative volume, previous-day levels.

**Backtester.** Event-driven, not vectorized. $0.01/share slippage, $0.005/share commission, fills on the next bar after signal, position sizing based on fixed dollar risk ($400), daily loss limits. I was deliberate about making the assumptions conservative rather than optimistic.

**Optimization — and where it almost went wrong.** I ran 5 rounds of optimization on Yahoo Finance data. V1 found that morning-only entries outperformed (Sharpe 4.02). V2 discovered short-only dominated on that sample (Sharpe 7.19). V3 pushed further with ultra-fine stop tuning (Sharpe 8.27). The numbers looked great. Then I realized I was optimizing on 41 trading days, which is nowhere near enough data to trust anything.

When I pulled 12 months of Alpaca data and tested out-of-sample, the strategy degraded significantly. That was the right outcome — it meant my validation process was working. I added the volume filter, re-optimized using walk-forward validation (train on months 1–6, test on months 7–12), and the test period actually outperformed training. That gave me enough confidence to pull the full 10 years.

**10-year validation.** I ran all 11 robustness checks with zero parameter changes: half-split, removing the top 5 trades, 3x slippage, timing sensitivity, the works. Everything held up.

## The actual edge

The volume filter. Without it, ORB breakouts are basically a coin flip. Requiring 1.2x above-average volume on the breakout bar filters out the weak and fake moves and selects for breakouts with real institutional participation. I found this empirically — the strategy without the filter is mediocre, the strategy with it is consistent. That's the kind of single insight that makes the whole thing feel worth the time.

## Project layout

```
src/
  fetch_alpaca_data.py      # Fetch intraday data from Alpaca (12 months)
  fetch_max_data.py         # Fetch max history (10 years)
  intraday_data.py          # Clean, validate, enrich intraday data
  phase1_screener.py        # Stock universe screening

  orb_strategy.py           # Signal generation + feature engineering

  backtester_v2.py          # Event-driven backtester with realistic execution
  run_12m_backtest.py       # 12-month backtest
  run_max_backtest.py       # 10-year backtest
  run_12m_optimization.py   # Walk-forward optimization

  run_strategy_sims.py      # V1: 6 strategy variants
  run_deep_sims.py          # V1: 3,000+ parameter sweep
  run_deep_sims_v2.py       # V2: Short-only discovery
  run_deep_sims_v3.py       # V3: Fine-tuning
  run_deep_sims_v4.py       # V4: Direction-neutral
  run_deep_sims_v5.py       # V5: Chart-driven refinements

  paper_trade_orb.py        # Alpaca paper trading bot

  generate_report.py        # V1 report
  generate_report_v4.py     # V4 report
  generate_final_report.py  # Final 12-month report
  phase5_validation.py      # Full statistical validation
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

You'll need Alpaca API keys. Set them as environment variables or drop them into `src/paper_trade_orb.py` directly.

## Running it

Fetch 10 years of data:
```bash
python src/fetch_max_data.py
```

Run the full backtest:
```bash
python src/run_max_backtest.py
```

Paper trade live:
```bash
python src/paper_trade_orb.py
```

Watch what it's doing:
```bash
tail -f paper_trade_orb.log
cat paper_trade_logs/trade_log.json
```

## Disclaimer

Not financial advice. This is a research project. Trading real money involves real risk of loss, and backtested results don't guarantee anything about live performance — slippage, latency, partial fills, and regime changes all matter. Paper trade it for a long time before putting real capital behind it.
