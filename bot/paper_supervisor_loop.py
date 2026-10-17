"""STAGED read-only persistent PAPER supervisor loop, NOT DEPLOYED.

This future host runner can poll the existing read-only Alpaca/Neon
watchdog and write ONLY an attested restricted heartbeat. It can never
place/cancel/replace orders or change the kill switch. Production has
no approved heartbeat writer role/secret yet: this CLI fails closed.
A running loop alone is NOT independent supervision, alert delivery,
actual GTC stop evidence, or authorization to trade.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .monitor_policy import assess_read_only_report
from .paper_safety import PAPER_ENDPOINT, TradeBlocked
from .paper_worker_lease import PaperWorkerHeartbeat
from .paper_neon_idle import make_paper_closed_flat_probe, verified_flat_journal_baseline


@dataclass(frozen=True)
class SupervisorLoopReport:
    ticks: int
    healthy_polls: int
    blocked_polls: int
    broker_order_submissions: int = 0
    neon_idle_ticks: int = 0


def valid_observation(report, *, now=None) -> bool:
    """Count only a current, independently rechecked idle safety report.

    A syntactically consistent reconciliation alone is NOT an attestation:
    the broker may report stale state, an unexpected unlocked Neon
    control, missing required report fields, or a critical/warning
    monitor incident. Those states must never renew a healthy lease.
    """
    if not isinstance(report, dict):
        return False
    # A broker write count of False compares equal to zero in Python;
    # require the exact integer zero and explicit readonly indicators.
    if (type(report.get("broker_write_operations")) is not int
            or report["broker_write_operations"] != 0
            or report.get("can_submit_orders") is not False
            or report.get("code_submissions_enabled") is not False
            or report.get("broker_paper_url") != PAPER_ENDPOINT):
        return False
    incident = report.get("monitor_incident")
    if (not isinstance(incident, dict)
            or incident.get("level") != "idle"
            or incident.get("code") != "idle"):
        return False
    check = report.get("reconciliation")
    if (not isinstance(check, dict)
            or check.get("consistent") is not True
            or check.get("uncovered_symbols") != []
            or check.get("blockers") != []):
        return False
    try:
        now = now or datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            return False
        observed = datetime.fromisoformat(
            report["observed_at_utc"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return False
        age = (now.astimezone(timezone.utc)
               - observed.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= 120:
            return False
        independent = assess_read_only_report(
            report, now=now, max_age_seconds=120)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        return False
    return (independent.level == "idle" and independent.code == "idle"
            and incident.get("fingerprint") == independent.fingerprint)

def one_supervisor_tick(
    *, fetch_read_only_report, heartbeat_writer: PaperWorkerHeartbeat,
    worker_id: str, sequence: int,
) -> bool:
    """Never send broker orders. Mark broken observations as failed pulses."""
    try:
        observation = fetch_read_only_report()
        ok = valid_observation(observation)
    except Exception:
        # All exceptions may embed account numbers, API keys or DSNs.
        # Never expose exception text, URLs or broker response bodies.
        ok = False
    try:
        return heartbeat_writer.pulse(
            worker_id=worker_id, sequence=sequence, poll_succeeded=ok
        ) is True
    except Exception:
        return False


def run_supervisor_loop(
    *, fetch_read_only_report, heartbeat_writer, interval_seconds=30,
    max_ticks=0, sleeper=time.sleep, worker_id=None,
    closed_flat_broker_probe=None,
) -> SupervisorLoopReport:
    """Synthetic tests can supply fake report, pulse and sleeper.

    A real host must use a fresh process instance ID on EVERY restart;
    only the database lease reader can independently verify liveness.
    """
    if type(interval_seconds) is not int or not 30 <= interval_seconds <= 90:
        raise TradeBlocked("Read-only supervisor interval must be 30–90 seconds")
    if type(max_ticks) is not int or max_ticks < 0 or max_ticks > 500:
        raise TradeBlocked("Invalid bounded worker test count")
    if worker_id is None:
        worker_id = "paper-" + uuid4().hex
    ticks = healthy = blocked = neon_idle = 0
    # After a restart, no cached status can replace a fresh broker AND
    # durable-journal read. Any failed poll invalidates this baseline.
    previously_verified_flat = False

    def full_read():
        nonlocal previously_verified_flat
        report = fetch_read_only_report()
        previously_verified_flat = verified_flat_journal_baseline(report)
        return report

    while max_ticks == 0 or ticks < max_ticks:
        ticks += 1
        # Opt-in only. A fresh read-only broker clock, positions and open
        # orders check is required on EVERY idle tick. No lease pulse and
        # no Neon call are made here, so idle is NEVER healthy supervision.
        idle = False
        if closed_flat_broker_probe is not None and previously_verified_flat:
            try:
                idle = closed_flat_broker_probe() is True
            except Exception:
                idle = False
        if idle:
            neon_idle += 1
            print("PAPER READ-ONLY SUPERVISOR: broker verified closed/flat; "
                  "NEON IDLE, NO heartbeat, NO trading permission.")
        else:
            succeeded = one_supervisor_tick(
                fetch_read_only_report=full_read,
                heartbeat_writer=heartbeat_writer,
                worker_id=worker_id, sequence=ticks
            )
            if succeeded:
                healthy += 1
                print("PAPER READ-ONLY SUPERVISOR: recent broker/Neon poll; "
                      "ZERO broker orders.")
            else:
                previously_verified_flat = False
                blocked += 1
                print("PAPER READ-ONLY SUPERVISOR: unhealthy/unknown; "
                      "NO trading permission.", file=sys.stderr)
        if max_ticks == 0 or ticks < max_ticks:
            sleeper(interval_seconds)
    return SupervisorLoopReport(
        ticks, healthy, blocked, neon_idle_ticks=neon_idle)


def main() -> int:
    from dotenv import load_dotenv
    from .paper_watchdog import run_once

    parser = argparse.ArgumentParser(
        description="STAGED read-only PAPER watchdog; no broker orders"
    )
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--max-ticks", type=int, default=0,
                        help="0 is continuous; 2-5 for synthetic smoke on a host")
    parser.add_argument(
        "--idle-neon-when-broker-closed-flat", action="store_true",
        help="Opt-in: skip Neon/heartbeat only after verified flat journal "
             "baseline and a FRESH closed/flat broker check every tick")
    args = parser.parse_args()
    load_dotenv()

    if not all(os.environ.get(x) for x in (
            "ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
            "PAPER_JOURNAL_DSN", "PAPER_HEARTBEAT_DSN")):
        print("BLOCKED: separate read-only and restricted heartbeat "
              "credentials missing. No broker orders.", file=sys.stderr)
        return 2
    try:
        writer = PaperWorkerHeartbeat(os.environ["PAPER_HEARTBEAT_DSN"])
        writer.assert_heartbeat_writer_only()
        closed_flat_probe = None
        if args.idle_neon_when_broker_closed_flat:
            closed_flat_probe = make_paper_closed_flat_probe(
                os.environ["ALPACA_PAPER_API_KEY"],
                os.environ["ALPACA_PAPER_SECRET_KEY"],
            )
        outcome = run_supervisor_loop(
            fetch_read_only_report=lambda: run_once(output=""),
            heartbeat_writer=writer,
            interval_seconds=args.interval_seconds,
            max_ticks=args.max_ticks,
            closed_flat_broker_probe=closed_flat_probe,
        )
    except Exception:
        print("BLOCKED: cannot operate a verified PAPER read-only worker; "
              "no broker orders.", file=sys.stderr)
        return 2
    return 0 if outcome.blocked_polls == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
