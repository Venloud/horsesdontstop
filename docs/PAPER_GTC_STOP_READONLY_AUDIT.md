# PAPER broker stop coverage: read-only overnight safety hardening

**Prepared 2026-10-11.** This is a staged read-only checker, **not a
proof that Alpaca's DAY parent OTO creates an overnight GTC stop**.
No brokerage orders or database modifications have been authorized.

## Why an extra safety gate is needed

The initial `paper_reconcile.reconcile_observation()` categorized any
open SELL STOP or STOP_LIMIT of adequate nominal quantity as an
apparent protective order. That was too permissive:

- A `DAY` child stop may expire at the close, leaving an overnight
  position unprotected.
- A STOP_LIMIT might never fill when the price gaps beyond its limit.
- A `held`, `pending_cancel`, `rejected`, `canceled` or fully
  filled child is not evidence of active remaining coverage.
- Already executed shares must be subtracted from child stop order
  quantity before comparing against current position quantity.
- A manual unrelated SELL stop must not automatically count as the
  linked child of an approved OTO parent.

Alpaca documents **only an order-level `time_in_force` field** for
the advanced order request and a STOP-loss child request with its own
price fields. Its official code examples use a **DAY LIMIT parent
OTO**; the documentation does **not establish the child's
independent GTC TIF for that case**. Do not treat this as proof
of the child's overnight persistence.

Official references:

- https://docs.alpaca.markets/us/docs/orders-at-alpaca
- https://docs.alpaca.markets/us/docs/working-with-orders

## What this PR's read-only inspector requires

For any nonzero PAPER position, an observed protective candidate must:

1. Be an active SELL **STOP**, not STOP_LIMIT.
2. Report **`time_in_force=gtc`** in the actual nested broker
   child-leg response; missing/unknown/DAY is *not verified*.
3. Have an observed linked parent ID belonging to a BUY LIMIT OTO
   order in the fetched broker order history. The parent must share
   the position's symbol, be **fully filled with its reported filled shares
   equal to the approved quantity**, and include
   the **same child ID in its actually nested broker legs**. Merely
   claiming a matching `parent_order_id` is insufficient.
4. Cover **remaining unfilled child stop quantity** and have an observed
   parent order size and completed fill equal to the durable approved
   whole-share quantity. There must be **exactly one active SELL STOP**
   against those shares; a second active stop is an unresolved duplicate.
5. Have a strictly positive reported stop trigger.

If any of these checks fails, the inspector sets
`position_missing_verified_gtc_oto_stop` and reports the position
as `uncovered`; it **never** tries to repair the position with
a trade. This hardening is implemented in
`bot/paper_reconcile.py` and exercised by synthetic-only tests
in `tests/test_paper_reconcile.py`.

**Remaining unknown:** Observing a child with `gtc` still does
not guarantee fills during gaps or halted/extended-hours sessions,
even though the checker now compares the reported stop trigger
to the original durable Neon intent. We must still independently prove
the real PAPER overnight stop lifecycle, exchange order timestamps,
and fully authenticated original owner approval before submission.

## Durable Neon stop-price and parent identity, new read-only audit

The read-only watchdog now requires the actual restricted Neon monitor to
`SELECT` every potentially accepted/filled `paper_intents` row relevant to
an observed position. The query requests 501 rows and **blocks at 501**,
rather than trusting a potentially truncated record.

For each long position, `reconcile_observation` refuses to describe an
active broker stop as covered unless its observed BUY LIMIT OTO parent
has a matching original Neon `client_order_id`, exact observed
`broker_order_id`, approved symbol, **approved whole-share size**,
and a child trigger **equal to the original immutable
`protective_stop`**. Remaining active GTC stop quantity must cover
the open shares, without exceeding the approved position.

An absent original record, malformed row, changed stop price or
wrong broker parent ID **fails closed**. The query is `SELECT` only,
and fake broker/database regression tests do not require credentials,
orders or migrations. As before, an open PAPER position still blocks
new entries even when a stop *appears* linked.

**Limitations:** This is an account-level read-only audit, not a live
per-client-ID fetch that proves original child order TIF across overnight
sessions. Broker-stop execution through a gap/market halt cannot be
guaranteed, the monitor is not a deployed 24/7 worker, and accepted
broker orders whose Neon status did not advance may conservatively
remain unverified. None of these limitations permits opening trades.

## Gaps still blocking activation

- Actual Alpaca PAPER DAY-limit OTO **entry/partial-fill/child
  TIF/overnight lifecycle** proof. This PR only tests fake objects.
- A **24/7 supervised worker** with independent durable heartbeat,
  read-only reconciliation, alert delivery and independently
  verified recovery. GitHub cron is not a persistent watchdog.
- Authenticated exact one-time owner approval, verified daily
  opening-equity and durable marked-to-market peak, restricted
  Neon executor concurrency/permissions and reliable trade/holiday
  calendar.
- Production order release remains hard-disabled
  (`PAPER_ORDER_SUBMISSIONS_RELEASED=False`); production Neon
  kill switch **TRUE**, submissions **FALSE**.

**If a parent is too old for the 30-day closed-order search,
the parent linkage may be missing and this audit will fail closed.
That may produce false alarms requiring deeper read-only
`get_order_by_client_id` and nested-order reconciliation, not
permission to ignore the alert.**

No scheduled action, API key changes, new paid hosting, broker
writes or Neon mutations are part of this PR.
