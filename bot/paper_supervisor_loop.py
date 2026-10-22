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
    external_liveness_failures: int = 0
    alert_delivery_failures: int = 0


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
    closed_flat_broker_probe=None, external_liveness_sink=None,
    critical_alert_sink=None,
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
    if closed_flat_broker_probe is not None and external_liveness_sink is None:
        raise TradeBlocked(
            "Closed-flat Neon idle needs a separate external liveness sink")
    ticks = healthy = blocked = neon_idle = liveness_failures = alert_failures = 0

    def alarm(code):
        nonlocal alert_failures
        if critical_alert_sink is None:
            return
        try:
            if critical_alert_sink(code) is not True:
                raise TradeBlocked("Critical alert unacknowledged")
        except Exception:
            alert_failures += 1
            print("PAPER CRITICAL ALERT DELIVERY FAILED; manual review needed.",
                  file=sys.stderr)
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
            external_status = "closed_flat_idle"
            print("PAPER READ-ONLY SUPERVISOR: broker verified closed/flat; "
                  "NEON IDLE, NO heartbeat, NO trading permission.")
        else:
            succeeded = one_supervisor_tick(
                fetch_read_only_report=full_read,
                heartbeat_writer=heartbeat_writer,
                worker_id=worker_id, sequence=ticks
            )
            external_status = "read_only_poll_ok" if succeeded else "poll_failed"
            if succeeded:
                healthy += 1
                print("PAPER READ-ONLY SUPERVISOR: recent broker/Neon poll; "
                      "ZERO broker orders.")
            else:
                previously_verified_flat = False
                blocked += 1
                alarm("supervisor_poll_or_lease_failed")
                print("PAPER READ-ONLY SUPERVISOR: unhealthy/unknown; "
                      "NO trading permission.", file=sys.stderr)
        if external_liveness_sink is not None:
            try:
                if external_liveness_sink(
                    worker_id=worker_id, sequence=ticks,
                    status=external_status) is not True:
                    raise TradeBlocked("External liveness not acknowledged")
            except Exception:
                # Unauthenticated or missing external heartbeat NEVER proves
                # safe off-hours idle. No exception details may reach logs.
                previously_verified_flat = False
                liveness_failures += 1
                alarm("external_liveness_unverified")
                print("PAPER LIVENESS: EXTERNAL SIGNAL UNVERIFIED; "
                      "NO trading permission.", file=sys.stderr)
        if max_ticks == 0 or ticks < max_ticks:
            sleeper(interval_seconds)
    return SupervisorLoopReport(
        ticks, healthy, blocked, neon_idle_ticks=neon_idle,
        external_liveness_failures=liveness_failures,
        alert_delivery_failures=alert_failures)



# Fixed diagnostic output contract. Never interpolate third-party messages.
DIAGNOSTIC_STAGES = (
    "required_env", "alpaca_account", "alpaca_positions_orders",
    "neon_journal", "neon_control", "observation_freshness",
    "reconciliation", "independent_assessment", "heartbeat",
    "external_liveness", "nyse_calendar", "first_heartbeat",
)
DIAGNOSTIC_REASONS = frozenset((
    "MISSING_ENV", "AUTH_REJECTED", "TIMEOUT", "TLS", "DNS",
    "PERMISSION_DENIED", "SCHEMA_MISMATCH", "STALE",
    "BLOCKERS_PRESENT", "KILL_SWITCH_ON", "MARKET_CLOSED",
    "NOT_FIRST_BEAT", "HTTP_2XX", "HTTP_4XX", "HTTP_5XX",
    "UNKNOWN",
))


class DiagnosticFailure(Exception):
    def __init__(self, reason_code):
        self.reason_code = (reason_code if reason_code in DIAGNOSTIC_REASONS
                            else "UNKNOWN")


def _diagnostic_reason(error):
    """Classify exception TYPES only. Never inspect or emit their strings."""
    import socket
    import ssl
    from requests.exceptions import ConnectionError as RequestConnectionError
    from requests.exceptions import Timeout as RequestTimeout
    if isinstance(error, DiagnosticFailure):
        return error.reason_code
    if isinstance(error, (TimeoutError, RequestTimeout)):
        return "TIMEOUT"
    if isinstance(error, (ssl.SSLError,)):
        return "TLS"
    if isinstance(error, socket.gaierror):
        return "DNS"
    if isinstance(error, PermissionError):
        return "PERMISSION_DENIED"
    # Database/HTTP drivers can embed credentials in exception messages.
    # Do not inspect them, even to search for a status code.
    if isinstance(error, RequestConnectionError):
        return "UNKNOWN"
    return "UNKNOWN"


def run_diagnostic_stages(stages, *, printer=print):
    """Run every stage independently. Caller supplies SAME production checks.

    An exception only affects its own stage. Stage callbacks return True,
    EXPECTED_FIRST_BEAT, or a fixed reason from DIAGNOSTIC_REASONS.
    """
    failed = False
    for name in DIAGNOSTIC_STAGES:
        try:
            result = stages[name]()
            if result is True:
                line = "PASS"
            elif name == "first_heartbeat" and result == "EXPECTED_FIRST_BEAT":
                line = "EXPECTED_FIRST_BEAT"
            else:
                code = result if result in DIAGNOSTIC_REASONS else "UNKNOWN"
                line = "FAIL " + code
                failed = True
        except Exception as error:
            line = "FAIL " + _diagnostic_reason(error)
            failed = True
        printer("STAGE " + name + ": " + line)
    return 2 if failed else 0


def diagnose_supervisor():
    """One bounded read-only inspection plus ONE restricted heartbeat write.

    No DB changes other than the existing lease pulse; no broker order calls.
    Every stage calls the same clients and validators as the regular loop.
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest, GetCalendarRequest
    from .paper_store import PostgresPaperJournal
    from .paper_watchdog import run_once
    from .paper_external_liveness import check_liveness_settings, send_liveness
    from .paper_supervised_execution import _verified_calendar_session
    from datetime import timedelta

    required = ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
                "PAPER_JOURNAL_DSN", "PAPER_HEARTBEAT_DSN",
                "PAPER_MONITOR_NTFY_TOPIC", "PAPER_EXTERNAL_LIVENESS_URL",
                "PAPER_EXTERNAL_LIVENESS_TOKEN")
    state = {}
    worker_id = "paper-" + uuid4().hex

    def env():
        if any(not os.environ.get(name) for name in required):
            raise DiagnosticFailure("MISSING_ENV")
        return True

    def client():
        if "trading" not in state:
            state["trading"] = TradingClient(
                os.environ["ALPACA_PAPER_API_KEY"],
                os.environ["ALPACA_PAPER_SECRET_KEY"],
                paper=True, url_override=PAPER_ENDPOINT)
        return state["trading"]

    def account():
        state["account"] = client().get_account()
        return True

    def positions_orders():
        trading = client()
        state["positions"] = trading.get_all_positions()
        state["open_orders"] = trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN,
                                    limit=500, nested=True))
        return True

    def journal():
        j = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
        j.assert_monitor_only()
        state["journal"] = j
        return True

    def control():
        row = state["journal"].read_control()
        state["control"] = row
        if row.kill_switch_active is not True:
            raise DiagnosticFailure("BLOCKERS_PRESENT")
        if row.submissions_enabled is not False:
            raise DiagnosticFailure("BLOCKERS_PRESENT")
        return True

    def observation():
        # The same complete read-only observation as the regular supervisor.
        report = run_once(output="")
        state["report"] = report
        dt = datetime.fromisoformat(report["observed_at_utc"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= 120:
            raise DiagnosticFailure("STALE")
        return True

    def reconciliation():
        rec = state["report"]["reconciliation"]
        if (rec.get("consistent") is not True
                or rec.get("blockers") != []
                or rec.get("uncovered_symbols") != []):
            raise DiagnosticFailure("BLOCKERS_PRESENT")
        return True

    def assessment():
        if not valid_observation(state["report"]):
            raise DiagnosticFailure("BLOCKERS_PRESENT")
        return True

    def heartbeat():
        writer = PaperWorkerHeartbeat(os.environ["PAPER_HEARTBEAT_DSN"])
        writer.assert_heartbeat_writer_only()
        # A fresh worker's first accepted heartbeat intentionally returns
        # False. It is not a healthy lease, and MUST NOT authorize trading.
        state["observation_valid_for_pulse"] = valid_observation(state.get("report"))
        state["heartbeat_accepted"] = writer.pulse(
            worker_id=worker_id, sequence=1,
            poll_succeeded=state["observation_valid_for_pulse"])
        return True

    def liveness():
        import requests
        url, token = check_liveness_settings(
            os.environ.get("PAPER_EXTERNAL_LIVENESS_URL"),
            os.environ.get("PAPER_EXTERNAL_LIVENESS_TOKEN"))
        def post(*args, **kwargs):
            response = requests.post(*args, **kwargs)
            state["http_status_class"] = str(response.status_code // 100) + "xx"
            return response
        try:
            return send_liveness(url=url, token=token, worker_id=worker_id,
                                 sequence=1, status="poll_failed", post=post) is True
        except Exception:
            if state.get("http_status_class") == "2xx":
                raise DiagnosticFailure("HTTP_2XX") from None
            if state.get("http_status_class") == "4xx":
                raise DiagnosticFailure("HTTP_4XX") from None
            if state.get("http_status_class") == "5xx":
                raise DiagnosticFailure("HTTP_5XX") from None
            raise

    def calendar():
        # GET same broker clock/calendar as the existing supervised
        # execution gate, never a guessed weekday schedule.
        trading = client()
        clock = trading.get_clock()
        if type(clock.is_open) is not bool:
            raise DiagnosticFailure("SCHEMA_MISMATCH")
        # Closed market is a legitimate read-only observation, not an
        # authorization or diagnostic error.
        from zoneinfo import ZoneInfo
        today = datetime.now(timezone.utc).astimezone(
            ZoneInfo("America/New_York")).date()
        sessions = trading.get_calendar(
            GetCalendarRequest(start=today, end=today))
        if not isinstance(sessions, (tuple, list)):
            raise DiagnosticFailure("SCHEMA_MISMATCH")
        if not sessions:
            raise DiagnosticFailure("MARKET_CLOSED")
        if len(sessions) != 1 or getattr(sessions[0], "date", None) != today:
            raise DiagnosticFailure("SCHEMA_MISMATCH")
        state["market_open"] = clock.is_open
        return True

    def first_heartbeat():
        if "heartbeat_accepted" not in state:
            raise DiagnosticFailure("NOT_FIRST_BEAT")
        if (state["heartbeat_accepted"] is False
                and state.get("observation_valid_for_pulse") is True):
            return "EXPECTED_FIRST_BEAT"
        # An existing worker lease must never be mislabeled first-beat.
        raise DiagnosticFailure("NOT_FIRST_BEAT")

    stages = dict(zip(DIAGNOSTIC_STAGES, (
        env, account, positions_orders, journal, control, observation,
        reconciliation, assessment, heartbeat, liveness, calendar,
        first_heartbeat,
    )))
    return run_diagnostic_stages(stages)


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
    parser.add_argument(
        "--external-liveness", action="store_true",
        help="Opt-in separate Cloudflare D1 worker-liveness heartbeat; "
             "required for Neon idle, never authorizes orders")
    parser.add_argument("--diagnose", action="store_true",
                        help="One bounded sanitized read-only stage diagnostic")
    args = parser.parse_args()
    load_dotenv()
    if args.diagnose:
        return diagnose_supervisor()
    from .monitor_notify import NotificationThrottle, validate_topic
    from .monitor_policy import make_incident

    # Without a private subscribed notification topic, a host must not
    # masquerade as supervised operation. A dead process needs an additional
    # independent heartbeat receiver, not just this in-process transport.
    try:
        topic = validate_topic(os.getenv("PAPER_MONITOR_NTFY_TOPIC"))
    except Exception:
        print("BLOCKED: independent alert topic unavailable.", file=sys.stderr)
        return 2
    throttle = NotificationThrottle(cooldown_seconds=3600)

    def send_supervisor_alarm(code):
        incident = make_incident("critical", "unverifiable", (code,))
        now = time.monotonic()
        previous = throttle.last_sent.get(incident.fingerprint)
        if previous is not None and 0 <= now - previous < 3600:
            return True  # confirmed prior delivery, not a fresh network claim
        return throttle.maybe_send(
            incident, topic=topic, now_seconds=now) is True
    if not args.external_liveness:
        print("BLOCKED: independent dead-worker alert channel is required.",
              file=sys.stderr)
        return 2
    if args.idle_neon_when_broker_closed_flat and not args.external_liveness:
        print("BLOCKED: Neon idle requires external liveness signal.",
              file=sys.stderr)
        return 2

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
        liveness_sink = None
        if args.external_liveness:
            from .paper_external_liveness import (
                check_liveness_settings, send_liveness)
            external_url, external_token = check_liveness_settings(
                os.environ.get("PAPER_EXTERNAL_LIVENESS_URL"),
                os.environ.get("PAPER_EXTERNAL_LIVENESS_TOKEN"))
            liveness_sink = lambda **details: send_liveness(
                url=external_url, token=external_token, **details)
        outcome = run_supervisor_loop(
            fetch_read_only_report=lambda: run_once(output=""),
            heartbeat_writer=writer,
            interval_seconds=args.interval_seconds,
            max_ticks=args.max_ticks,
            closed_flat_broker_probe=closed_flat_probe,
            external_liveness_sink=liveness_sink,
            critical_alert_sink=send_supervisor_alarm,
        )
    except Exception:
        print("BLOCKED: cannot operate a verified PAPER read-only worker; "
              "no broker orders.", file=sys.stderr)
        return 2
    return 0 if (outcome.blocked_polls == 0
                 and outcome.external_liveness_failures == 0
                 and outcome.alert_delivery_failures == 0) else 2


if __name__ == "__main__":
    raise SystemExit(main())
