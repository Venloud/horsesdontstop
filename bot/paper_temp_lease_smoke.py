"""TEMP-ONLY actual psycopg heartbeat/independent reader smoke test.

No Alpaca client, no order calls, no production writes. This changes ONLY
the temporary heartbeat singleton and ALWAYS uses poll_succeeded=False:
database connectivity alone cannot prove a genuine broker supervision poll.

Requires explicit --acknowledge-temp-only-writes and two separate restricted
DSNs in environment. Never put credentials in command-line arguments/logs.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from .paper_safety import TradeBlocked
from .paper_worker_lease import PaperWorkerHeartbeat

# Fixed, pre-existing TEST branch endpoint. NOT production.
TEMP_HOST = "ep-holy-moon-b5trovxf.c-7.us-east-2.aws.neon.tech"
TEMP_HOSTS = frozenset((
    TEMP_HOST,
    "ep-holy-moon-b5trovxf-pooler.c-7.us-east-2.aws.neon.tech",
))
EXPECTED_DB = "neondb"


def validate_temp_role_dsn(dsn: str | None, expected_role: str) -> str:
    """Reject untrusted/production host or elevated/nonmatching DB role.

    Does not authenticate the database by itself: the Python role's SQL
    permission attestation runs separately before any heartbeat write.
    """
    if not isinstance(dsn, str) or not dsn or len(dsn) > 2048:
        raise TradeBlocked("Missing or invalid temp role DSN")
    try:
        url = urlsplit(dsn)
        role = unquote(url.username or "")
        port = url.port
        host = url.hostname
        database = unquote(url.path)
        query = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
    except (ValueError, TypeError):
        raise TradeBlocked("Malformed temp database connection") from None
    # Libpq URI query arguments can OVERRIDE an otherwise safe hostname,
    # database or username. Never accept host, hostaddr, service, user,
    # dbname, password, options or unrecognized params in this smoke runner.
    seen = set()
    for key, value in query:
        if key in seen or (
            (key == "sslmode" and value not in ("require", "verify-full"))
            or (key == "channel_binding" and value != "require")
            or (key == "connect_timeout" and value not in ("3", "5", "10"))
            or key not in ("sslmode", "channel_binding", "connect_timeout")
        ):
            raise TradeBlocked("Forbidden temp database connection option")
        seen.add(key)
    if (url.scheme not in ("postgres", "postgresql")
            or host not in TEMP_HOSTS
            or (port is not None and port != 5432)
            or database != "/neondb"
            or role != expected_role
            or not url.password
            or url.fragment):
        raise TradeBlocked("Temp connection does not match approved role/branch")
    return dsn


def run_temp_lease_smoke(writer: PaperWorkerHeartbeat,
                         monitor: PaperWorkerHeartbeat, *,
                         worker_id: str) -> dict:
    """Real psycopg writer and separate monitor; always an UNHEALTHY lease.

    No synthetic healthy lease: true requires real, complete broker+Neon
    observation in a separately validated supervisor host.
    """
    writer.assert_heartbeat_writer_only()
    monitor.assert_heartbeat_monitor_only()

    first = writer.pulse(
        worker_id=worker_id, sequence=1, poll_succeeded=False)
    if first is not False:
        raise TradeBlocked("Unexpected successful first temp heartbeat")

    first_seen = monitor.inspect(expected_worker_id=worker_id)
    if (first_seen.healthy is not False
            or first_seen.poll_succeeded is not False
            or first_seen.worker_id != worker_id
            or first_seen.sequence != 1
            or first_seen.blockers != (
                "persistent_supervisor_broker_poll_not_proven",)
            or type(first_seen.age_seconds) not in (int, float)
            or not 0 <= first_seen.age_seconds <= 120):
        raise TradeBlocked("Independent monitor rejected first temp pulse")

    second = writer.pulse(
        worker_id=worker_id, sequence=2, poll_succeeded=False)
    if second is not False:
        raise TradeBlocked("Unexpected successful second temp heartbeat")

    second_seen = monitor.inspect(expected_worker_id=worker_id)
    if (second_seen.healthy is not False
            or second_seen.poll_succeeded is not False
            or second_seen.worker_id != worker_id
            or second_seen.sequence != 2
            or second_seen.blockers != (
                "persistent_supervisor_broker_poll_not_proven",)
            or type(second_seen.age_seconds) not in (int, float)
            or not 0 <= second_seen.age_seconds <= 120):
        raise TradeBlocked("Independent monitor rejected second temp pulse")

    try:
        writer.pulse(worker_id=worker_id, sequence=2, poll_succeeded=False)
    except TradeBlocked:
        duplicate_denied = True
    else:
        duplicate_denied = False

    try:
        writer.pulse(
            worker_id="paper-test-competitor-"+uuid4().hex[:16],
            sequence=1, poll_succeeded=False)
    except TradeBlocked:
        competitor_denied = True
    else:
        competitor_denied = False

    if not duplicate_denied or not competitor_denied:
        raise TradeBlocked("Temp heartbeat lease exclusivity failed")

    final = monitor.inspect(expected_worker_id=worker_id)
    if (final.healthy is not False
            or final.poll_succeeded is not False
            or final.sequence != 2
            or final.blockers != (
                "persistent_supervisor_broker_poll_not_proven",)
            or type(final.age_seconds) not in (int, float)
            or not 0 <= final.age_seconds <= 120):
        raise TradeBlocked("Temp heartbeat did not remain unhealthy")

    return {
        "writer_role_verified": True,
        "independent_monitor_verified": True,
        "unhealthy_first_pulse": True,
        "unhealthy_second_pulse": True,
        "duplicate_denied": True,
        "competing_writer_denied": True,
        "final_health": "unhealthy",
        "broker_orders": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run restricted Python psycopg smoke on TEST Neon branch")
    parser.add_argument(
        "--acknowledge-temp-only-writes",
        action="store_true",
        help="I explicitly intend a synthetic UNHEALTHY heartbeat DB write "
             "on the pre-existing TEST branch, not production")
    args = parser.parse_args(argv)
    if not args.acknowledge_temp_only_writes:
        print("BLOCKED: explicit TEST-only acknowledgement missing.",
              file=sys.stderr)
        return 2
    try:
        writer_dsn = validate_temp_role_dsn(
            os.getenv("PAPER_TEMP_HEARTBEAT_DSN"),
            "hds_paper_heartbeat")
        monitor_dsn = validate_temp_role_dsn(
            os.getenv("PAPER_TEMP_MONITOR_DSN"),
            "hds_paper_monitor")
        writer = PaperWorkerHeartbeat(writer_dsn)
        monitor = PaperWorkerHeartbeat(monitor_dsn)
        result = run_temp_lease_smoke(
            writer, monitor, worker_id="paper-temp-"+uuid4().hex)
    except Exception:
        # Driver error messages may include passwords, DSNs or host details.
        print("BLOCKED: restricted TEMP Python lease integration failed. "
              "No broker orders. Never infer supervisor health.",
              file=sys.stderr)
        return 2
    print(
        "TEMP PYTHON LEASE SMOKE PASSED: separate restricted writer/monitor; "
        "duplicate and competitor rejected; lease UNHEALTHY; "
        "ZERO broker orders."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
