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

    def assert_monitor_only(self):
        """Refuse DBA, writer or role-escalation credentials in a read-only job.

        Neon-managed roles may inherit neon_superuser by default even when
        they were created with an innocent-sounding name. The watcher MUST
        never run with such credentials. This is an additional safeguard,
        not a substitute for private GitHub secrets and least privilege.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    "bool_and(has_table_privilege(current_user, t.tbl, 'SELECT')), "
                    "bool_or(has_table_privilege(current_user,t.tbl,'INSERT') "
                    "OR has_table_privilege(current_user,t.tbl,'UPDATE') "
                    "OR has_table_privilege(current_user,t.tbl,'DELETE') "
                    "OR has_table_privilege(current_user,t.tbl,'TRUNCATE')), "
                    "pg_has_role(current_user,'neon_superuser','MEMBER'), "
                    "has_schema_privilege(current_user,'public','CREATE'), "
                    "has_database_privilege(current_user,current_database(),'CREATE'), "
                    "(SELECT rolsuper OR rolcreaterole OR rolcreatedb "
                    "OR rolreplication OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user) "
                    "FROM (VALUES ('public.paper_control'),"
                    "('public.paper_intents'),('public.paper_audit_events'),"
                    "('public.paper_equity_snapshots')) AS t(tbl)"
                )
                row = cur.fetchone()
        if row != (True, False, False, False, False, False):
            raise TradeBlocked(
                "Read-only monitor connection must have only SELECT grants "
                "and no Neon administrator or role-creation privileges"
            )

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
        """Legacy direct-INSERT shortcut is intentionally retired.

        After PR38, every executable intent reservation must flow through
        PaperExecutorJournal.reserve_if_enabled(), whose narrowly owned
        SQL function atomically locks/checks the actual control row.
        A plain INSERT can bypass emergency controls or forge a filled
        record and must NEVER become an available fallback.
        """
        raise TradeBlocked(
            "Direct PAPER intent INSERT forbidden: use atomic guarded reservation")

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

    def read_approved_stop_intents(self, limit: int = 501):
        """Read only the durable approved stop terms for broker comparison.

        This is a diagnostic SELECT, never a broker submission approval.
        Exclude reserved/unknown/canceled/rejected parent intents. A
        saturated result cannot be considered complete by the caller.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise TradeBlocked("Invalid stop-intent inspection limit")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT client_order_id, status, symbol, whole_shares, "
                    "protective_stop, broker_order_id "
                    "FROM paper_intents "
                    "WHERE status IN ('accepted','filled') "
                    "ORDER BY created_at ASC LIMIT %s", (limit,))
                return cur.fetchall()

    def read_equity_references(self, day_start_utc, as_of_utc):
        """Read only. Require an equity sample very near the NYSE opening.

        The day's FIRST arbitrary late sample is not its opening equity and
        would defeat a daily-loss cutoff. Only samples within five minutes
        of the known New York regular open qualify. If missing, remain
        BLOCKED for the rest of the day. A future writer needs an audited
        market-calendar-aware daily baseline, not this check alone.
        """
        from datetime import timedelta
        if day_start_utc.tzinfo is None or as_of_utc.tzinfo is None:
            raise TradeBlocked("Invalid timezone-aware equity window")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT equity FROM paper_equity_snapshots "
                    "WHERE observed_at >= %s AND observed_at < %s "
                    "AND observed_at <= %s "
                    "ORDER BY observed_at ASC LIMIT 1",
                    (day_start_utc, day_start_utc + timedelta(minutes=5),
                     as_of_utc))
                start = cur.fetchone()
                cur.execute(
                    "SELECT MAX(equity) FROM paper_equity_snapshots "
                    "WHERE observed_at <= %s", (as_of_utc,))
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
        """Never let the legacy journal directly attest broker outcomes.

        A future independent broker reconciler must first verify PAPER
        order/position/stop evidence, then call the separately privileged
        atomic paper_record_reconciled_intent_status SQL interface.
        This legacy entrypoint fails closed BEFORE any SQL or broker work.
        """
        raise TradeBlocked(
            "Direct PAPER status and audit writes disabled: use trusted "
            "independent broker reconciliation")

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
