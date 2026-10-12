"""UNDEPLOYED PAPER supervisor heartbeat lease, no order privileges.

Staged for a future independently hosted, continually running observer.
The heartbeat role must be separate from both restricted broker executor
and read-only paper monitor. Its DB connections write ONLY the new
paper_worker_heartbeat table. No production migration or credentials are
installed by this module. Zero Alpaca order calls.

A heartbeat proves that one attested process recently wrote a server-
clock pulse. It DOES NOT establish continuous cloud uptime, actual
GTC child stop coverage, authenticated owner consent, or safe trading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .paper_safety import TradeBlocked, money
from .paper_store import validate_postgres_url

HEARTBEAT_ROLE = "hds_paper_heartbeat"
MAX_HEARTBEAT_AGE_SECONDS = 120
WORKER_ID = re.compile(r"^[a-zA-Z0-9_.:-]{8,64}$")


@dataclass(frozen=True)
class LeaseInspection:
    observed_at_utc: str | None
    age_seconds: float | None
    worker_id: str | None
    sequence: int | None
    poll_succeeded: bool
    healthy: bool
    blockers: tuple[str, ...]

    @property
    def watchdog_heartbeat_seconds(self) -> int | None:
        # Never transform a missing, bad, future or expired heartbeat
        # into a healthy PaperSafetyState heartbeat.
        if not self.healthy or self.age_seconds is None:
            return None
        return int(self.age_seconds)


class PaperWorkerHeartbeat:
    def __init__(self, dsn: str):
        self._dsn = validate_postgres_url(dsn)

    def _connect(self):
        import psycopg
        return psycopg.connect(self._dsn, sslmode="require", connect_timeout=5)

    def assert_heartbeat_writer_only(self):
        """Deny admin, order-executor, control-writer and monitor roles."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_user, "
                    "pg_has_role(current_user,'neon_superuser','MEMBER'), "
                    "(SELECT rolsuper OR rolcreaterole OR rolcreatedb OR "
                    "rolreplication OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user), "
                    "has_table_privilege(current_user,'public.paper_control','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','INSERT'), "
                    "has_table_privilege(current_user,'public.paper_worker_heartbeat','INSERT'), "
                    "has_table_privilege(current_user,'public.paper_worker_heartbeat','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_worker_heartbeat','DELETE')"
                )
                row = cur.fetchone()
        if row != (HEARTBEAT_ROLE, False, False, False, False,
                   True, True, False):
            raise TradeBlocked(
                "PAPER heartbeat role has invalid or excessive DB permissions"
            )

    def pulse(self, *, worker_id: str, sequence: int,
              poll_succeeded: bool) -> bool:
        """Insert or advance ONE DB-server-clock heartbeat lease.

        No consecutive confirmations for an unexpected new worker:
        takeover is allowed only after the previous lease is stale,
        and the takeover's FIRST pulse is intentionally unhealthy.
        A second increasing-sequence pulse by the same worker is needed.
        An unchanged sequence, duplicate, competing instance or stale
        writer cannot silently refresh the lease.
        """
        if not isinstance(worker_id, str) or not WORKER_ID.fullmatch(worker_id):
            raise TradeBlocked("Invalid PAPER supervisor worker identifier")
        if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
            raise TradeBlocked("Invalid positive heartbeat sequence")
        if type(poll_succeeded) is not bool:
            raise TradeBlocked("Heartbeat poll result must be explicit boolean")
        self.assert_heartbeat_writer_only()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute(
                    "INSERT INTO public.paper_worker_heartbeat "
                    "(singleton, worker_id, sequence, observed_at, poll_succeeded) "
                    "VALUES (TRUE, %s, %s, clock_timestamp(), FALSE) "
                    "ON CONFLICT (singleton) DO UPDATE SET "
                    "worker_id=EXCLUDED.worker_id, "
                    "sequence=EXCLUDED.sequence, "
                    "observed_at=clock_timestamp(), "
                    "poll_succeeded=CASE "
                    "WHEN public.paper_worker_heartbeat.worker_id="
                    "EXCLUDED.worker_id THEN %s ELSE FALSE END "
                    "WHERE "
                    "(public.paper_worker_heartbeat.worker_id=EXCLUDED.worker_id "
                    "AND EXCLUDED.sequence>public.paper_worker_heartbeat.sequence) "
                    "OR "
                    "(public.paper_worker_heartbeat.worker_id<>EXCLUDED.worker_id "
                    "AND public.paper_worker_heartbeat.observed_at<"
                    "clock_timestamp()-interval '120 seconds') "
                    "RETURNING poll_succeeded",
                    (worker_id, sequence, poll_succeeded),
                )
                row = cur.fetchone()
                if row is None:
                    raise TradeBlocked(
                        "Duplicate or competing PAPER supervisor lease; "
                        "no health accepted"
                    )
                return row[0] is True

    def assert_heartbeat_monitor_only(self):
        """Independent observer must be SELECT-only, never the heartbeat writer."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_user, "
                    "pg_has_role(current_user,'neon_superuser','MEMBER'), "
                    "(SELECT rolsuper OR rolcreaterole OR rolcreatedb "
                    "OR rolreplication OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user), "
                    "has_table_privilege(current_user,'public.paper_worker_heartbeat','SELECT'), "
                    "(has_table_privilege(current_user,'public.paper_worker_heartbeat','INSERT') "
                    "OR has_table_privilege(current_user,'public.paper_worker_heartbeat','UPDATE') "
                    "OR has_table_privilege(current_user,'public.paper_worker_heartbeat','DELETE')), "
                    "has_table_privilege(current_user,'public.paper_control','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','INSERT')"
                )
                row = cur.fetchone()
        if row != ("hds_paper_monitor", False, False,
                   True, False, False, False):
            raise TradeBlocked(
                "PAPER heartbeat inspector must use narrow SELECT-only monitor"
            )

    def inspect(self, *, expected_worker_id: str) -> LeaseInspection:
        """Read DB-time health; missing table/permission fails closed.

        The monitor inspecting this result should use a SELECT-only DSN
        with explicit grants on paper_worker_heartbeat. A hostname or
        process timestamp alone is not evidence of an actual broker poll.
        """
        if not isinstance(expected_worker_id, str) or not WORKER_ID.fullmatch(
                expected_worker_id):
            raise TradeBlocked("Expected worker identity not configured")
        # A heartbeat writer may not certify its own liveness. Use a
        # separate read-only monitor DSN for the independent inspection.
        self.assert_heartbeat_monitor_only()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT clock_timestamp(), worker_id, sequence, "
                    "observed_at, poll_succeeded "
                    "FROM public.paper_worker_heartbeat WHERE singleton=TRUE"
                )
                row = cur.fetchone()
        if not row or len(row) != 5:
            return LeaseInspection(None, None, None, None, False, False,
                                   ("persistent_supervisor_heartbeat_missing",))
        db_now, observed_worker, sequence, observed_at, ok = row
        if (not isinstance(db_now, datetime)
                or not isinstance(observed_at, datetime)
                or db_now.tzinfo is None or observed_at.tzinfo is None):
            raise TradeBlocked("Unverifiable DB-clock PAPER worker heartbeat")
        age = (db_now - observed_at).total_seconds()
        problems = []
        if not 0 <= age <= MAX_HEARTBEAT_AGE_SECONDS:
            problems.append("persistent_supervisor_heartbeat_stale_or_future")
        if observed_worker != expected_worker_id:
            problems.append("persistent_supervisor_identity_mismatch")
        if type(sequence) is not int or sequence < 1:
            problems.append("persistent_supervisor_sequence_invalid")
        if ok is not True:
            problems.append("persistent_supervisor_broker_poll_not_proven")
        return LeaseInspection(
            observed_at_utc=observed_at.astimezone(timezone.utc).isoformat(),
            age_seconds=max(0.0, age) if age >= 0 else None,
            worker_id=observed_worker if isinstance(observed_worker, str) else None,
            sequence=sequence if type(sequence) is int else None,
            poll_succeeded=ok is True,
            healthy=not problems,
            blockers=tuple(problems),
        )
