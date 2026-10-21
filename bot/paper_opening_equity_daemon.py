"""Continuous, strictly PAPER opening-equity sampler for a persistent host.

This runs in its own restricted executor-role process, NOT the heartbeat
writer or monitor process. It can INSERT a time-gated equity snapshot,
but it cannot submit/cancel a broker order or change the kill switch.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as clocktime, timezone
from zoneinfo import ZoneInfo

from .paper_equity_open import opening_window, snapshot_one_opening
from .paper_safety import PAPER_ENDPOINT, TradeBlocked
from .paper_execution_journal import PaperExecutorJournal

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class EquityDaemonReport:
    ticks: int
    confirmed_sessions: int
    missed_sessions: int
    failed_observations: int
    alert_delivery_failures: int


def inspect_equity_window(*, trading, journal, now, clock):
    """Poll only the NY opening window; attest a missed opening just after it.

    No late snapshots are fabricated and a closed broker market never gets
    an inferred baseline. The database server independently enforces time.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Opening sampler needs an aware clock")
    local = now.astimezone(NY)
    if opening_window(now) is not None:
        return snapshot_one_opening(trading, journal, now=now,
                                    after_read_now=clock())
    if local.weekday() > 4 or not (local.hour == 9 and 35 <= local.minute < 37):
        return "outside_opening_window"
    try:
        broker_open = getattr(trading.get_clock(), "is_open", None)
        if broker_open is False:
            return "market_closed"
        if broker_open is not True:
            raise TradeBlocked("Broker opening state unknown")
        start = datetime.combine(local.date(), clocktime(9, 30),
                                 tzinfo=NY).astimezone(timezone.utc)
        journal.assert_executor_permissions()
        verified, _peak = journal.read_equity_references(start, now)
        return "confirmed" if verified is not None else "missing_opening_equity"
    except Exception:
        return "unverifiable_opening_equity"


def run_equity_daemon(*, trading, journal, clock=None, sleeper=time.sleep,
                      interval_seconds=15, max_ticks=0, critical_alert_sink=None):
    if (type(interval_seconds) is not int or
            not 10 <= interval_seconds <= 60 or
            type(max_ticks) is not int or not 0 <= max_ticks <= 500):
        raise TradeBlocked("Invalid sampler configuration")
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    ticks = failures = alerts_failed = 0
    confirmed_days, missed_days, alerted = set(), set(), set()
    while max_ticks == 0 or ticks < max_ticks:
        ticks += 1
        current = clock()
        try:
            result = inspect_equity_window(
                trading=trading, journal=journal, now=current, clock=clock)
        except Exception:
            result = "unverifiable_opening_equity"
        day = current.astimezone(NY).date().isoformat()
        if getattr(result, "state", None) in ("recorded", "already_recorded") or result == "confirmed":
            confirmed_days.add(day)
            missed_days.discard(day)
        elif result == "missing_opening_equity":
            missed_days.add(day)
        elif result == "unverifiable_opening_equity":
            failures += 1
        if (result in ("missing_opening_equity", "unverifiable_opening_equity")
                and (day, result) not in alerted):
            try:
                if critical_alert_sink is None or critical_alert_sink(result) is not True:
                    raise TradeBlocked("Opening equity alert not delivered")
                alerted.add((day, result))
            except Exception:
                alerts_failed += 1
                print("PAPER EQUITY ALERT: delivery unverified.", file=sys.stderr)
        if max_ticks == 0 or ticks < max_ticks:
            sleeper(interval_seconds)
    return EquityDaemonReport(
        ticks, len(confirmed_days), len(missed_days), failures, alerts_failed)


def main(argv=None):
    import argparse
    from dotenv import load_dotenv
    from alpaca.trading.client import TradingClient
    from .monitor_policy import make_incident
    from .monitor_notify import NotificationThrottle, validate_topic

    parser = argparse.ArgumentParser(description="Persistent PAPER opening baseline")
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--max-ticks", type=int, default=0)
    args = parser.parse_args(argv)
    load_dotenv()
    try:
        topic = validate_topic(os.getenv("PAPER_MONITOR_NTFY_TOPIC"))
        key = os.environ["ALPACA_PAPER_API_KEY"]
        secret = os.environ["ALPACA_PAPER_SECRET_KEY"]
        journal = PaperExecutorJournal(os.environ["PAPER_EXECUTOR_DSN"])
        journal.assert_executor_permissions()
        trading = TradingClient(key, secret, paper=True,
                                url_override=PAPER_ENDPOINT)
        throttle = NotificationThrottle(cooldown_seconds=3600)

        def alert(code):
            incident = make_incident("critical", "unverifiable", (code,))
            now = time.monotonic()
            previous = throttle.last_sent.get(incident.fingerprint)
            if previous is not None and 0 <= now - previous < 3600:
                return True
            return throttle.maybe_send(incident, topic=topic,
                                       now_seconds=now) is True

        outcome = run_equity_daemon(
            trading=trading, journal=journal, interval_seconds=args.interval_seconds,
            max_ticks=args.max_ticks, critical_alert_sink=alert)
    except Exception:
        print("BLOCKED: PAPER equity service configuration or runtime failed.",
              file=sys.stderr)
        return 2
    return 2 if outcome.missed_sessions or outcome.failed_observations or outcome.alert_delivery_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
