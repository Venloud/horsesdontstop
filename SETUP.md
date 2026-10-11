# Project $10K, initial foundation

**Status:** Code scaffold only, not deployed and not trading. All API access is PAPER-only and read-only.

1. Create an Alpaca paper account.
2. Install Python 3.11+, then `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`, fill in **paper-only** credentials.
4. Run `python -m bot.main --check` to read paper account status and market clock.
5. Run `pytest` to verify baseline risk calculations.

Research assumptions: $500 initial simulated equity; $10,000 goal; baseline 1% risk per trade and separate 2% experimental variant; test 3% daily cutoff; no real deposits or orders. Do not confuse position sizing with broker-side protective stops.

**Before autonomous paper orders:** implement stop placement, position and order reconciliation, idempotency, journal, market data entitlement checks, scanning, backtesting, circuit breaker, persistent hosting, and monitored forward testing. Alpaca free equities data may be IEX-only; simulated fills differ from real fills. Monthly contributions capped at $100 including loss replacement. $5,000 withdrawal at goal requires approval.
