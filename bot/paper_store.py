"""PostgreSQL-backed, fail-closed PAPER order-intent state store.

NOT wired into order submission: no trades can be sent from this module.
This is a durable intent journal for a FUTURE persistent paper worker.
Requires an owner-provisioned PostgreSQL URL, supplied as a secret, and
TLS enforced. GitHub Actions artifacts and local SQLite are NOT durable.
"""
from dataclasses import dataclass
import re

from .paper_safety import PaperOrderPlan, TradeBlocked


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_control (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    kill_switch_active BOOLEAN NOT NULL DEFAULT TRUE,
    submissions_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO paper_control (singleton) VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS paper_intents (
    client_order_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trading_day DATE NOT NULL,
    whole_shares INTEGER NOT NULL CHECK (whole_shares > 0),
    expected_entry NUMERIC(18, 6) NOT NULL CHECK (expected_entry > 0),
    protective_stop NUMERIC(18, 6) NOT NULL CHECK (protective_stop > 0),
    status TEXT NOT NULL DEFAULT 'reserved' CHECK
        (status IN ('reserved', 'submitted', 'accepted', 'filled',
                    'rejected', 'canceled', 'unknown')),
    broker_order_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (protective_stop < expected_entry)
);
CREATE INDEX IF NOT EXISTS ix_paper_intents_status ON paper_intents(status);

CREATE TABLE IF NOT EXISTS paper_audit_events (
    audit_id BIGSERIAL PRIMARY KEY,
    client_order_id TEXT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_paper_audit_events_time
    ON paper_audit_events(created_at);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity NUMERIC(18, 6) NOT NULL CHECK (equity > 0),
    cash NUMERIC(18, 6) NOT NULL CHECK (cash >= 0)
);
"""


ALLOWED_TRANSITIONS = {
    "reserved": frozenset(("submitted", "unknown", "rejected")),
    "submitted": frozenset(("accepted", "filled", "unknown", "rejected", "canceled")),
    "accepted": frozenset(("filled", "unknown", "canceled", "rejected")),
    "unknown": frozenset(("submitted", "accepted", "filled", "rejected", "canceled")),
    "filled": frozenset(),
    "rejected": frozenset(),
    "canceled": frozenset(),
}


def transition_allowed(old: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(old, ())


def validate_postgres_url(dsn: str) -> str:
    # Never print or log this return value: credentials may be embedded.
    if not isinstance(dsn, str) or not re.match(r"^postgres(?:ql)?://", dsn):
        raise TradeBlocked("Durable PAPER journal requires PostgreSQL URL")
    if not dsn.strip() or len(dsn) > 2048:
        raise TradeBlocked("Invalid PostgreSQL URL")
    return dsn


@dataclass(frozen=True)
class JournalControl:
    kill_switch_active: bool
    submissions_enabled: bool


class PostgresPaperJournal:
    """Use one short TLS connection/transaction per operation.

    Reservation has DB uniqueness on client_order_id. If a process crashes
    after reservation, a subsequent call gets False and MUST NOT resubmit
    automatically. A broker-by-client-order-id reconciler must handle
    unknown outcomes and attached protective stop legs.
    """
    def __init__(self, dsn: str):
        self._dsn = validate_postgres_url(dsn)

    def _connect(self):
        import psycopg
        # sslmode forced; no credentials ever logged.
        return psycopg.connect(self._dsn, sslmode="require", connect_timeout=5)

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA, prepare=False)

    def read_control(self) -> JournalControl:
        # Unavailable journal is equivalent to a closed kill switch.
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT kill_switch_active, submissions_enabled "
                    "FROM paper_control WHERE singleton = TRUE")
                row = cur.fetchone()
                if row is None:
                    raise TradeBlocked("Missing durable kill-switch control row")
                return JournalControl(bool(row[0]), bool(row[1]))

    def reserve_intent(self, plan: PaperOrderPlan) -> bool:
        """Atomic one-time reservation. False means NEVER auto-resubmit."""
        if plan.whole_shares < 1 or plan.protective_stop >= plan.estimated_entry:
            raise TradeBlocked("Unsafe protective order")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO paper_intents "
                    "(client_order_id, strategy, symbol, trading_day, "
                    " whole_shares, expected_entry, protective_stop) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (client_order_id) DO NOTHING "
                    "RETURNING client_order_id",
                    (plan.client_order_id, plan.strategy, plan.symbol,
                     plan.trading_day, plan.whole_shares,
                     plan.estimated_entry, plan.protective_stop),
                )
                return cur.fetchone() is not None

    def read_unresolved_intents(self, limit: int = 501):
        """Read durable order intents. A saturated result is NOT complete.

        Never assume a previous 'reserved' or 'unknown' order failed simply
        because it isn't in a recent Alpaca order-list response.
        """
        if not 1 <= limit <= 1000:
            raise TradeBlocked("Invalid reconciliation intent limit")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT client_order_id, status, symbol, whole_shares "
                    "FROM paper_intents "
                    "WHERE status IN ('reserved','submitted','accepted','unknown') "
                    "ORDER BY created_at ASC LIMIT %s", (limit,))
                return cur.fetchall()

    def read_equity_references(self, day_start_utc):
        """Read only. Daily starting equity and historical peak from snapshots.

        Caller supplies the validated New York regular-session opening UTC
        datetime. Missing values MUST remain a trading blocker.
        """
        if day_start_utc.tzinfo is None:
            raise TradeBlocked("Trading day start must be timezone-aware")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT equity FROM paper_equity_snapshots "
                    "WHERE observed_at >= %s ORDER BY observed_at ASC LIMIT 1",
                    (day_start_utc,))
                start = cur.fetchone()
                cur.execute("SELECT MAX(equity) FROM paper_equity_snapshots")
                peak = cur.fetchone()
        return (start[0] if start else None,
                peak[0] if peak and peak[0] is not None else None)

    def get_intent(self, client_order_id: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, broker_order_id, symbol, whole_shares "
                    "FROM paper_intents WHERE client_order_id = %s",
                    (client_order_id,))
                return cur.fetchone()

    def record_observed_state(self, client_order_id: str,
                              observed_status: str, broker_order_id=None):
        """Use ONLY with trusted, freshly reconciled broker state.

        Reject backward/unsafe transitions and duplicate status writes.
        The journal NEVER authorizes trading by itself.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM paper_intents "
                    "WHERE client_order_id = %s FOR UPDATE",
                    (client_order_id,))
                row = cur.fetchone()
                if row is None or not transition_allowed(row[0], observed_status):
                    raise TradeBlocked("Unknown or invalid order state transition")
                cur.execute(
                    "UPDATE paper_intents SET status = %s, "
                    "broker_order_id = COALESCE(%s, broker_order_id), "
                    "updated_at = now() WHERE client_order_id = %s",
                    (observed_status, broker_order_id, client_order_id))
                cur.execute(
                    "INSERT INTO paper_audit_events "
                    "(client_order_id, kind, summary) VALUES (%s,%s,%s)",
                    (client_order_id, "status_observed", observed_status))

    def record_equity(self, equity: float, cash: float):
        from .paper_safety import money
        eq, available = money(equity), money(cash)
        if eq <= 0 or available < 0:
            raise TradeBlocked("Invalid equity snapshot")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO paper_equity_snapshots "
                    "(equity, cash) VALUES (%s,%s)", (eq, available))

    def max_recorded_equity(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(equity) FROM paper_equity_snapshots")
                row = cur.fetchone()
                if not row or row[0] is None:
                    raise TradeBlocked("No verified durable equity peak")
                return row[0]
