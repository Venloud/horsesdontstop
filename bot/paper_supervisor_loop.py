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
# The diagnostic is a ONE-SHOT read-only inspection. It never writes the
# supervisor heartbeat lease or the Cloudflare liveness row: those belong
# to the continuous supervisor alone, so a diagnostic can neither create a
# lease that later looks stale nor masquerade as continuous supervision.
DIAGNOSTIC_STAGES = (
    "required_env", "opening_equity_collector", "continuous_supervisor_process",
    "alpaca_account", "alpaca_positions_orders",
    "neon_journal", "neon_control", "observation_freshness",
    "reconciliation", "independent_assessment", "heartbeat_writer_role",
    "external_liveness_config", "nyse_calendar", "continuous_supervisor_lease",
)
# 09:20-09:29 New York: dependencies only. The opening baseline, a fresh
# full observation and a market-hours supervisor lease cannot exist yet.
PREOPEN_DIAGNOSTIC_STAGES = (
    "required_env", "opening_equity_collector", "continuous_supervisor_process",
    "alpaca_account", "alpaca_positions_orders", "neon_journal",
    "neon_control", "heartbeat_writer_role", "external_liveness_config",
    "nyse_calendar",
)
DIAGNOSTIC_REASONS = frozenset((
    "MISSING_ENV", "AUTH_REJECTED", "TIMEOUT", "TLS", "DNS",
    "PERMISSION_DENIED", "SCHEMA_MISMATCH", "STALE",
    "BLOCKERS_PRESENT", "KILL_SWITCH_ON", "MARKET_CLOSED",
    "NOT_FIRST_BEAT", "HTTP_2XX", "HTTP_4XX", "HTTP_5XX",
    "UNKNOWN", "MISSING_OPENING_EQUITY", "UNSAFE_EXPOSURE",
    "DEGRADED", "SECURITY_SWITCH", "UNEXPECTED_BROKER_WRITE",
    "FINGERPRINT_MISMATCH", "UNHEALTHY_OBSERVATION",
    "COMPETING_LEASE", "LEASE_STALE", "LEASE_UNVERIFIED",
    "PROCESS_NOT_RUNNING", "DUPLICATE_PROCESS", "PROCESS_UNVERIFIED",
    "SUPERVISOR_NOT_RUNNING",
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
    if isinstance(error, ssl.SSLError):
        return "TLS"
    name = type(error).__name__
    if name in ("InvalidPassword", "AuthenticationError", "UnauthorizedError"):
        return "AUTH_REJECTED"
    if name in ("InsufficientPrivilege", "InvalidAuthorizationSpecification"):
        return "PERMISSION_DENIED"
    if name in ("UndefinedTable", "UndefinedColumn", "DatatypeMismatch"):
        return "SCHEMA_MISMATCH"
    if name in ("QueryCanceled", "ConnectionTimeout"):
        return "TIMEOUT"
    if isinstance(error, socket.gaierror):
        return "DNS"
    if isinstance(error, PermissionError):
        return "PERMISSION_DENIED"
    # Database/HTTP drivers can embed credentials in exception messages.
    # Do not inspect them, even to search for a status code.
    if isinstance(error, RequestConnectionError):
        return "UNKNOWN"
    return "UNKNOWN"


def run_diagnostic_stages(stages, *, printer=print, names=DIAGNOSTIC_STAGES,
                          results=None):
    """Run every stage independently. Caller supplies SAME production checks.

    An exception only affects its own stage. Stage callbacks return True
    or a fixed reason from DIAGNOSTIC_REASONS. When `results` is a dict,
    each stage's fixed PASS/FAIL token is also recorded there.
    """
    failed = False
    for name in names:
        try:
            result = stages[name]()
            if result is True:
                line = "PASS"
            else:
                code = result if result in DIAGNOSTIC_REASONS else "UNKNOWN"
                line = "FAIL " + code
                failed = True
        except Exception as error:
            line = "FAIL " + _diagnostic_reason(error)
            failed = True
        if results is not None:
            results[name] = line
        printer("STAGE " + name + ": " + line)
    return 2 if failed else 0


def diagnostic_assessment_result(report):
    """Use the production validator and independent incident classifier."""
    if valid_observation(report):
        return True
    independent = assess_read_only_report(report, max_age_seconds=120)
    safe_codes = {
        "missing_opening_equity": "MISSING_OPENING_EQUITY",
        "unsafe_exposure": "UNSAFE_EXPOSURE",
        "degraded": "DEGRADED",
        "security_switch": "SECURITY_SWITCH",
        "unexpected_broker_write": "UNEXPECTED_BROKER_WRITE",
        "stale_observation": "STALE",
    }
    if independent.level == "idle" and independent.code == "idle":
        return "FINGERPRINT_MISMATCH"
    return safe_codes.get(independent.code, "BLOCKERS_PRESENT")


def diagnostic_process_count(count):
    """Exactly one root process of a long-running service is healthy.

    The Windows launcher counts root python processes by command line
    (a venv launcher and its child count once). Unknown is never healthy.
    """
    if type(count) is not int or count < 0:
        return "PROCESS_UNVERIFIED"
    if count == 0:
        return "PROCESS_NOT_RUNNING"
    if count > 1:
        return "DUPLICATE_PROCESS"
    return True


def diagnostic_supervisor_lease(row):
    """Classify the continuous supervisor's lease WITHOUT writing it.

    `row` is (observed_at, poll_succeeded, db_now) or None. Only a lease
    pulsed within 120 seconds by a successful poll is healthy.
    """
    if row is None:
        return "SUPERVISOR_NOT_RUNNING"
    if (not isinstance(row, (tuple, list)) or len(row) != 3
            or not isinstance(row[0], datetime) or not isinstance(row[2], datetime)
            or row[0].tzinfo is None or row[2].tzinfo is None
            or type(row[1]) is not bool):
        return "LEASE_UNVERIFIED"
    age = (row[2] - row[0]).total_seconds()
    if age < 0:
        return "LEASE_UNVERIFIED"
    if age > 120:
        return "SUPERVISOR_NOT_RUNNING"
    if row[1] is not True:
        return "UNHEALTHY_OBSERVATION"
    return True


def diagnostic_phase(phase, now=None):
    """`auto` picks preopen before 09:30 New York time, else postopen."""
    if phase in ("preopen", "postopen"):
        return phase
    if phase != "auto":
        raise ValueError("Invalid diagnostic phase")
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo("America/New_York"))
    return "preopen" if (local.hour, local.minute) < (9, 30) else "postopen"


def diagnose_supervisor(*, phase="postopen", collector_instances=None,
                        supervisor_instances=None, results=None):
    """One bounded read-only inspection. No Neon or broker writes at all.

    Every stage calls the same clients and validators as the regular loop.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest, GetCalendarRequest
    from .paper_store import PostgresPaperJournal
    from .paper_watchdog import run_once, make_paper_readonly_client
    from .paper_external_liveness import check_liveness_settings

    required = ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
                "PAPER_JOURNAL_DSN", "PAPER_HEARTBEAT_DSN",
                "PAPER_MONITOR_NTFY_TOPIC", "PAPER_EXTERNAL_LIVENESS_URL",
                "PAPER_EXTERNAL_LIVENESS_TOKEN")
    state = {}

    def env():
        if any(not os.environ.get(name) for name in required):
            raise DiagnosticFailure("MISSING_ENV")
        return True

    def collector():
        return diagnostic_process_count(collector_instances)

    def supervisor_process():
        return diagnostic_process_count(supervisor_instances)

    def client():
        if "trading" not in state:
            state["trading"] = make_paper_readonly_client(
                os.environ["ALPACA_PAPER_API_KEY"],
                os.environ["ALPACA_PAPER_SECRET_KEY"])
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
        reason = diagnostic_assessment_result(state["report"])
        if reason is not True:
            raise DiagnosticFailure(reason)
        return True

    def heartbeat_writer():
        # Authenticate the restricted writer's privileges only. NO pulse.
        writer = PaperWorkerHeartbeat(os.environ["PAPER_HEARTBEAT_DSN"])
        writer.assert_heartbeat_writer_only()
        state["writer"] = writer
        return True

    def liveness_config():
        # Validate settings only. The continuous supervisor alone posts.
        check_liveness_settings(
            os.environ.get("PAPER_EXTERNAL_LIVENESS_URL"),
            os.environ.get("PAPER_EXTERNAL_LIVENESS_TOKEN"))
        return True

    def calendar():
        # GET same broker clock/calendar as the existing supervised
        # execution gate, never a guessed weekday schedule.
        trading = client()
        clock = trading.get_clock()
        if type(clock.is_open) is not bool:
            raise DiagnosticFailure("SCHEMA_MISMATCH")
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
        # Pre-open, a closed clock on a real session day is expected.
        if phase == "postopen" and clock.is_open is not True:
            raise DiagnosticFailure("MARKET_CLOSED")
        return True

    def supervisor_lease():
        writer = state.get("writer") or PaperWorkerHeartbeat(
            os.environ["PAPER_HEARTBEAT_DSN"])
        # Restricted heartbeat connection; SELECT only, never show IDs.
        with writer._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT observed_at, poll_succeeded, clock_timestamp() "
                    "FROM public.paper_worker_heartbeat WHERE singleton=TRUE")
                row = cur.fetchone()
        result = diagnostic_supervisor_lease(row)
        if result is not True:
            raise DiagnosticFailure(result)
        return True

    stages = {
        "required_env": env,
        "opening_equity_collector": collector,
        "continuous_supervisor_process": supervisor_process,
        "alpaca_account": account,
        "alpaca_positions_orders": positions_orders,
        "neon_journal": journal,
        "neon_control": control,
        "observation_freshness": observation,
        "reconciliation": reconciliation,
        "independent_assessment": assessment,
        "heartbeat_writer_role": heartbeat_writer,
        "external_liveness_config": liveness_config,
        "nyse_calendar": calendar,
        "continuous_supervisor_lease": supervisor_lease,
    }
    names = (PREOPEN_DIAGNOSTIC_STAGES if phase == "preopen"
             else DIAGNOSTIC_STAGES)
    return run_diagnostic_stages(stages, names=names, results=results)


def write_diagnostic_log(path, record, *, max_bytes=512_000):
    """Append one JSON line of fixed tokens; keep one rotated backup."""
    import json
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > max_bytes:
        backup = target.with_name(target.name + ".1")
        if backup.exists():
            backup.unlink()
        target.rename(backup)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def alert_diagnostic_failure(results, *, phase, topic, state_path, now,
                             transport=None):
    """Send ONE phone alert per unchanged failure set per New York day.

    Returns SENT, SUPPRESSED_DUPLICATE or raises on delivery failure. The
    receipt is persisted only after ntfy accepts, so a failed delivery is
    retried by the next run instead of being silently dropped.
    """
    import json
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from .monitor_notify import send_critical_ntfy
    from .monitor_policy import make_incident
    failures = tuple(sorted(
        name + ":" + line[5:] for name, line in results.items()
        if line.startswith("FAIL ")))
    incident = make_incident("critical", "diagnostic_failed",
                             (phase,) + failures)
    day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    state_file = Path(state_path)
    try:
        sent = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(sent, dict):
            sent = {}
    except (OSError, ValueError):
        sent = {}
    if sent.get(incident.fingerprint) == day:
        return "SUPPRESSED_DUPLICATE"
    delivered = (transport or send_critical_ntfy)(
        incident, topic=topic, details="; ".join(failures))
    if delivered is not True:
        # Do not acknowledge a failed/ambiguous delivery.
        raise RuntimeError("Diagnostic notification unacknowledged")
    sent = {k: v for k, v in sent.items() if v == day}
    sent[incident.fingerprint] = day
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(sent, sort_keys=True), encoding="utf-8")
    return "SENT"


def run_diagnostic_cli(args) -> int:
    """Diagnostic entrypoint: stages, durable log, deduplicated alert."""
    from .monitor_notify import validate_topic
    now = datetime.now(timezone.utc)
    if args.test_alert:
        from .monitor_notify import send_critical_ntfy
        from .monitor_policy import make_incident
        try:
            acknowledged = send_critical_ntfy(
                make_incident("critical", "diagnostic_test", ("test",)),
                topic=validate_topic(os.getenv("PAPER_MONITOR_NTFY_TOPIC")))
            if acknowledged is not True:
                raise RuntimeError("Test notification unacknowledged")
        except Exception:
            print("TEST ALERT: DELIVERY FAILED")
            return 2
        print("TEST ALERT: SENT (confirm it arrived on the phone)")
        return 0
    phase = diagnostic_phase(args.phase, now)
    print("DIAGNOSTIC PHASE: " + phase)
    results = {}
    code = diagnose_supervisor(
        phase=phase, collector_instances=args.collector_instances,
        supervisor_instances=args.supervisor_instances, results=results)
    alert = "NOT_NEEDED"
    if code != 0 and args.alert_state:
        try:
            alert = alert_diagnostic_failure(
                results, phase=phase, state_path=args.alert_state, now=now,
                topic=validate_topic(os.getenv("PAPER_MONITOR_NTFY_TOPIC")))
        except Exception:
            alert = "DELIVERY_FAILED"
    print("DIAGNOSTIC RESULT: " + ("PASS" if code == 0 else "FAIL")
          + "; ALERT: " + alert)
    if args.log_file:
        try:
            write_diagnostic_log(args.log_file, {
                "at_utc": now.isoformat(), "phase": phase,
                "exit_code": code, "alert": alert, "stages": results,
            })
        except OSError:
            print("DIAGNOSTIC LOG: WRITE FAILED")
            return 2
    return code


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
    parser.add_argument("--phase", choices=("auto", "preopen", "postopen"),
                        default="auto", help="Diagnostic stage set")
    parser.add_argument("--collector-instances", type=int, default=None)
    parser.add_argument("--supervisor-instances", type=int, default=None)
    parser.add_argument("--log-file", default=None,
                        help="Diagnostic JSON-lines log of fixed tokens")
    parser.add_argument("--alert-state", default=None,
                        help="Diagnostic alert dedup file; enables phone alert")
    parser.add_argument("--test-alert", action="store_true",
                        help="Send one TEST ntfy notification and exit")
    args = parser.parse_args()
    load_dotenv()
    if args.diagnose or args.test_alert:
        return run_diagnostic_cli(args)
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
