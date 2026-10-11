# Project $10K: Research Strategy Comparison

This is a **read-only research workflow**. It never creates Alpaca orders,
moves money, places stop orders, or modifies the existing $500 paper account.

## Strategies (fixed before reviewing holdout outcomes)

1. `momentum_volume`: positive 21-session momentum and most-recent completed
   session IEX volume at least 1.15x preceding 20-day average.
2. `trend_following`: last close above rising 20-day SMA, itself above
   50-day SMA.
3. `high_52_week`: stock closes within 2% of the previous 252-session high
   and has positive trailing 63-session price change.
4. `sector_leadership`: stock 63-session price gain exceeds a mapped sector
   ETF's gain; the sector ETF beats SPY and is itself positive. Initial mapping
   is tech stocks AAPL/MSFT/NVDA/AMD => XLK; TSLA => XLY. Mapping is explicit
   and incomplete, not real-time sector constituent discovery.

**Post-earnings announcement drift is postponed**, not approximated from price
alone. Point-in-time earnings announcements, consensus expectations, revisions
and event timestamps must be licensed and validated before it can be tested.

## How it runs

Use **Actions > Strategy Research (No Trades) > Run workflow**. Also scheduled
weekly on Sunday at 15:00 UTC. The workflow:
- Runs offline research-engine unit tests.
- Uses paper API credentials to request 730 calendar days of split-adjusted
  Alpaca IEX daily OHLCV data for five explicitly chosen stocks, SPY, XLK, XLY.
- Compares four signals at 1% and 2% risk per position. Each experiment has
  its own independent $500 capital, while stocks share one cash budget within
  each experiment. Only one long, whole-share stock position may be open.
- Uses first 70% of dates as the exploratory 'train' period and last 30% as
  a strictly later, separately funded holdout period. There is no fitted
  model or parameter optimization in this first comparison.
- Signals read only sessions before the entry day. Entry fills at following
  session's open with 10-basis-point adverse slippage.
- Applies a 3% initial stop and 5% trailing stop updated from the prior day's
  peak; an overnight gap through the stop fills at the worse open. Daily-bar
  ambiguities are resolved conservatively. Open positions are closed at the
  window end with adverse slippage.
- Reports trade count, net P&L, percentage return, daily-close equity
  drawdown, win rate, profit factor and SPY price-return reference.

## Critical limitations and next work

- Five hand-selected surviving stocks are **not a representative or unbiased**
  universe. Diversified universe selection must use historical membership.
- IEX-only volume and daily bars are not consolidated tape or a simulation
  of order-book microstructure; historical entitlements and missing bars may
  vary. Signals will often produce **zero trades** with $500 whole-share
  capital and 1% per-position risk. This is honest; do not fake a trade.
- Split-adjusted data is a research simplification, not historical share
  accounting. ETF price-only benchmark excludes dividends. No taxes,
  regulatory fees, borrow costs, or real broker stop-order guarantees.
- Closed-ended 70/30 holdout is NOT a completed walk-forward validation.
  Reusing its results to tune choices contaminates the holdout. Future
  research needs repeated out-of-sample windows with fixed hypotheses.
- Daily-close marked drawdown omits worst intraday losses, and stop gaps may
  exceed the intended risk budget.
- No reliable persistent database, autonomous open-position management, or
  licensed earnings-surprise data exists yet.
- The first historical simulator is retained as an independent baseline.
  Neither workflow submits orders.

The workflow is suitable for research, **not unattended trade execution**.
