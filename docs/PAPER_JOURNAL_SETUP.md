# Durable PAPER journal, not connected yet

The repository now contains `bot/paper_store.py`: a TLS-only PostgreSQL
adapter prepared for the future paper-order worker. **It is not connected,
and no paper orders are enabled.**

## Why this is necessary

GitHub Actions jobs are disposable. Neither downloading an artifact nor
storing SQLite in `.state/` gives unattended runs a reliable shared state.
A correctly configured hosted database will provide a durable record
across runs and worker restarts.

## What is implemented

- DB-unique `client_order_id`, reserved atomically in a transaction.
- A repeated reservation returns False; it must **never** automatically
  submit another broker order.
- States: `reserved`, `submitted`, `accepted`, `filled`, `rejected`,
  `canceled`, and `unknown`, with restricted transitions.
- Equity/cash snapshots, an append-only event table, and a peak-equity
  query. These can support compounding and drawdown checks when attached
  to real paper account snapshots.
- Database kill switch defaults to ON and order submission defaults to OFF.
- Connections enforce TLS and short connection timeouts.
- Unit tests use an in-memory fake, **not a production database**.

## Still required

1. The account owner selects and provisions hosted PostgreSQL with secure
   TLS, e.g. a supported managed PostgreSQL service. Check its current
   limits and inactive-project policies before choosing.
2. Store the database DSN in a **secret**, not a GitHub commit, chat
   message, or uploaded log. Future code will read a
   `PAPER_JOURNAL_DSN` environment secret.
3. Apply the schema to the provisioned instance and test read/write
   connectivity from the intended always-on worker.
4. Verify transactions and duplicate reservations using a real database,
   including simulating crashes, network partitions and concurrent runs.
5. Build broker reconciliation and protected paper order execution:
   reserve before submission; if a submission times out, mark outcome
   unknown and reconcile via broker `client_order_id` before any retry.
6. Deploy a monitored persistent worker with a heartbeat and alerts, and
   require its readiness before disabling the kill switch.
7. Owner approval is required before turning on paper order submissions.

**Do not paste credentials or database connection strings in chat.**

No monthly contributions are simulated. Trading profits/losses will
change the next equity-risk budget immediately, even below $1,000.
