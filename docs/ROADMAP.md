# Project $10K research milestones

## Verified
- GitHub Actions manual Alpaca paper connection succeeded on 2026-10-10: active paper account, equity 500, market closed, zero orders.
- This does not verify the market-data scanner or autonomous execution.

## Phase 1, no orders
- Read-only weekday scheduled daily-bar watchlist research; manually trigger first and inspect logs.
- IEX feed is not consolidated market volume, so relative volume is only relative to IEX data.
- GitHub-hosted jobs are ephemeral, and cron can be delayed or skipped. Each run writes a downloadable artifact, not a persistent database across runs.

## Phase 2, backtest
- Historical walk-forward validation, data freshness checks, commissions/spreads/slippage estimates, no future leakage, realistic $500 buying-power constraints.
- Compare 1% vs 2% risk experiments, daily 3% cutoff, adaptive exits, benchmark and drawdown.
- No automatic orders until tested.

## Phase 3, paper trading
- Broker-side protective stops, kill switch, duplicate-order prevention, broker position reconciliation, calendar/holiday and DST handling, event journal, alerts, and persistent worker.
- Scheduled GitHub Actions alone is not reliable for unattended open-position risk management.
- Paper trading for 8-12 weeks before considering any real-money activity. Real-money activity requires explicit approval.

## Guardrails
- $500 initial simulated equity, $10,000 total-equity goal, monthly contribution ceiling $100 inclusive of loss replacement.
- Do not log secrets or commit .env. No live keys, live brokerage orders, deposits, or automatic withdrawals.
