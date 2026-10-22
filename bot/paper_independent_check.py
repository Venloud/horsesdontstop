"""Independent, read-only PAPER worker-loss checker (STAGED, NOT DEPLOYED).

Run from a SEPARATE host/process with monitor-only credentials. Never
read the worker's own claim of uptime as an acceptable expected worker ID.
Do not infer healthy supervision from a flat account or closed exchange.
No order/cancel/deposit operations, control toggles or journal writes.
"""
from __future__ import annotations

import argparse
import os
import sys

from .monitor_policy import (
    MonitorIncident, assess_read_only_report, make_incident, unverified_incident,
)
from .monitor_notify import NotificationError, send_critical_ntfy
from .paper_worker_lease import PaperWorkerHeartbeat, WORKER_ID


def evaluate_independent_supervisor(report, inspection) -> MonitorIncident:
    """Fail closed on missing, stale, or uncertain independent evidence.

    No runtime can distinguish a deliberate Neon-idle heartbeat from a
    crashed worker using only the heartbeat table. Treat BOTH as loss of
    supervision, including after market close while broker is flat.
    """
    try:
        observation = assess_read_only_report(report, max_age_seconds=120)
        if observation.level == "critical":
            return observation
        if observation.level != "idle":
            return make_incident(
                "critical", "supervisor_observation_unverified",
                ("independent_broker_or_journal_warning",))
        if (inspection is None or inspection.healthy is not True
                or inspection.poll_succeeded is not True
                or type(inspection.sequence) is not int
                or inspection.sequence < 2
                or type(inspection.age_seconds) not in (int, float)
                or not 0 <= inspection.age_seconds <= 120
                or inspection.watchdog_heartbeat_seconds is None
                or inspection.blockers != ()):
            return make_incident(
                "critical", "supervisor_heartbeat_missing",
                ("independent_worker_lease_unhealthy",))
        return make_incident("idle", "idle", ())
    except Exception:
        # Never print a caught driver exception or leak DSN/key material.
        return unverified_incident()


def independent_check(*, fetch_broker_report, inspect_lease,
                      expected_worker_id: str) -> MonitorIncident:
    """Broker & journal observation and independent lease attestation.

    The expected ID must be configured OUTSIDE the heartbeat writer, not
    taken dynamically from the heartbeat row being inspected.
    """
    if (not isinstance(expected_worker_id, str)
            or WORKER_ID.fullmatch(expected_worker_id) is None):
        return unverified_incident()
    try:
        report = fetch_broker_report()
        # Require an independent, restricted monitor role. inspect_lease
        # itself calls assert_heartbeat_monitor_only before reading.
        lease = inspect_lease(expected_worker_id=expected_worker_id)
        return evaluate_independent_supervisor(report, lease)
    except Exception:
        return unverified_incident()


def main(argv=None, *, broker_report=None, monitor_factory=PaperWorkerHeartbeat,
         send_alert=send_critical_ntfy) -> int:
    parser = argparse.ArgumentParser(
        description="STAGED independent read-only PAPER worker-loss check")
    parser.add_argument(
        "--send-critical-alert", action="store_true",
        help="Opt in to ntfy critical notifications using a private topic")
    args = parser.parse_args(argv)
    expected = os.getenv("PAPER_EXPECTED_WORKER_ID")
    dsn = os.getenv("PAPER_JOURNAL_DSN")
    if (not expected or WORKER_ID.fullmatch(expected) is None
            or not dsn):
        print("BLOCKED: separate monitor role / expected worker ID missing.",
              file=sys.stderr)
        return 2
    try:
        from .paper_watchdog import run_once
        # The journal and heartbeat DSNs should both use the restricted
        # SELECT-only monitor role. Never use a writer or owner DSN.
        monitor = monitor_factory(dsn)
        monitor.assert_heartbeat_monitor_only()
        observed = independent_check(
            fetch_broker_report=broker_report or (
                lambda: run_once(output="")),
            inspect_lease=monitor.inspect,
            expected_worker_id=expected)
    except Exception:
        observed = unverified_incident()

    if observed.level != "idle":
        print("PAPER INDEPENDENT CHECK: UNVERIFIED OR LOST SUPERVISION; "
              "NO trade permission.", file=sys.stderr)
        if args.send_critical_alert:
            topic = os.getenv("PAPER_MONITOR_NTFY_TOPIC")
            if not topic:
                print("ALERT BLOCKED: no private topic configured.",
                      file=sys.stderr)
                return 2
            try:
                delivered = send_alert(observed, topic=topic)
                if delivered is not True:
                    raise NotificationError("No delivery")
            except Exception:
                print("ALERT DELIVERY UNVERIFIED: manual review required.",
                      file=sys.stderr)
                return 2
        return 3
    print("PAPER INDEPENDENT CHECK: broker/Neon and lease observations "
          "current; READ-ONLY ONLY, ZERO orders, NO execution authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
