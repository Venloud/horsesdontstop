# Live U.S. stock quotes for Alpaca PAPER account, now implemented

**What the owner wants:** paper trade **real live stocks** with the $500
Alpaca virtual account, not more historical-only backtests. No live
money, no deposits, no paid hosting upgrades without owner approval.

## Delivered in this phase

- `bot/live_quotes.py`: model and validation for **Alpaca IEX latest
  stock quotes** (not SIP consolidated national bid/offer). Quotes must
  be market-session-fresh (<=45 seconds), positive, uncrossed, have
  nonzero bid/ask size, and <=75 basis points spread.
- Initial risk budget **1% of current PAPER equity per trade**; gains
  and losses immediately change subsequent budgets, including below
  $1,000. Whole-share quantity is bounded by cash, stop-distance risk,
  and current displayed IEX ask size. No auto deposits.
- Build a **buy LIMIT with a strict price cap** plus an attached Alpaca
  stop-loss leg via **OTO order-class**, using DAY time-in-force and
  regular hours only. For live entry review the limit defaults to ask
  plus 10 basis points, and stop at 3% below the quote BID. These are
  unsubmitted SDK request objects, **NOT live broker orders**. The
  stop can fill below its trigger in a gap and no stop guarantees the
  planned maximum loss.
- `bot/live_paper_preview_cli.py`: uses read-only momentum signals from
  completed daily IEX bars, then obtains the **latest real stock IEX
  quote**. Alpaca paper account and market clock, restricted Neon
  monitor-role and production kill-switch are checked first. If market
  closed, positions or open orders exist, unresolved journal intents
  exist, account is blocked, or production switch differs from OFF,
  it rejects previews. It *never* submits or cancels a broker order.
- `.github/workflows/live-iex-paper-preview.yml`: best-effort weekday
  scheduled workflow, every 30 minutes between 13:00–20:30 UTC,
  including both US EDT and EST stock sessions. Alpaca's own clock
  validates actual market open (holidays and early closes). Daily
  bars and quotes are IEX-only and may not include nationally traded
  volume. The job uploads sanitized preview data but no credentials.
- `tests/test_live_quotes.py`, `tests/test_live_paper_preview.py`:
  offline tests of budget, freshness, spread, market state, invalid
  sizes, no affordable shares, deterministic NY order IDs, OTO STOP
  request type and complete lack of broker write calls.

## Clear limits / required before first actual PAPER order

1. The current GitHub Actions `PAPER_JOURNAL_DSN` secret uses
   `hds_paper_monitor`, a **SELECT-only** restricted role. Preserve
   it: no broker trade intents can be written with this credential.
   A separately created narrowly scoped PostgreSQL **writer** role
   will be needed to reserve intent IDs and record fills.
2. Build durable reservation/reconciliation against broker's
   `get_order_by_client_id()` and the stop-loss child leg for every
   attempt. Unknown API outcomes MUST halt and be reconciled, not
   blindly retried.
3. Independently verified watchdog/liveness and alerting that can
   observe and manage open trades if GitHub schedules skip or the user
   turns off their PC. A GitHub Actions timer alone is not sufficient.
4. Verify broker constraints and fill lifecycle in Alpaca's PAPER
   environment, including partial fills, halted stocks, OTO child
   activation, stopped/canceled child legs and overnight gaps.
5. Use actual daily opening equity and durable peak for kill-switch
   checks. Never equate an absent value to zero loss.
6. First order only after owner reviews a live quote-backed proposed
   symbol, quantity, limit and stop and explicitly approves a
   **small supervised paper-only trade**. Maintain 1% risk initial
   budget until owner approves anything else.

No cash is real and **no order submission has occurred** in this
phase. If it's a weekend, stock market is closed and no live-entry
candidate will be offered until the next actual regular session.
Even during market hours, if IEX quote is stale or thin, skip rather
than invent a trade. **Stop orders aren't guaranteed prices**.

Official docs: https://docs.alpaca.markets/us/docs/orders-at-alpaca
and https://alpaca.markets/sdks/python/api_reference/data/stock/requests.html .
