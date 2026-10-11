# Backtest audit checklist

The October 2026 first run is exploratory, not evidence of a tradable edge.

- Five stock backtests are independent; do not sum their profits into a portfolio.
- With $500 and whole shares, 1% risk sizing yielded zero trades. Fractional
  shares must not be assumed available for every order type, especially stops.
- Existing simulator applies adverse slippage to entry and exits and handles
  open-below-stop gaps, but OHLC bars cannot determine actual intraday event order.
- New validation.py contains independently testable conservative execution and
  closed-trade drawdown metrics; it is **not yet integrated** with the existing
  simulator or the historical backtest CLI.
- Closed-trade drawdown understates possible intratrade drawdown.
- Next: integrate execution audit, add mark-to-market equity curves, stock split
  and corporate action adjustments, walk-forward holdouts, transaction cost
  assumptions, cash accounting across symbols, realistic fills and liquidity.
- A 1-year backtest on a five-symbol preselected watchlist can overfit badly.
- No paper or live orders are enabled by this work.
