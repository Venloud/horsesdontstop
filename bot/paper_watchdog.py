"""Fail-closed PAPER watchdog preflight. NEVER writes or cancels broker orders.

A run compares current Alpaca PAPER account/positions/orders against a
durable Neon PostgreSQL journal. No DB changes, no submit_order(), and
no execution switch. Can run repeatedly under a future persistent
supervisor, but this module alone is NOT a reliable cloud deployment.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, time as clocktime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .monitor_policy import assess_read_only_report, unverified_incident
from .monitor_notify import NotificationThrottle, NotificationError
from .paper_reconcile import reconcile_observation
from .paper_safety import (
    PAPER_ENDPOINT, PaperSafetyState, execution_blockers, money,
)
from .paper_store import PostgresPaperJournal


def new_york_regular_open_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("Need timezone-aware UTC timestamp")
    local = now.astimezone(ZoneInfo("America/New_York"))
    local_open = datetime.combine(local.date(), clocktime(9, 30),
                                  tzinfo=ZoneInfo("America/New_York"))
    return local_open.astimezone(timezone.utc)


def read_only_tick(trading_client, journal, *, now=None) -> dict:
    """Gather broker/DB observations and emit immutable safety diagnosis.

    Unresolved journal intents, missing equity references or uncovered
    positions cannot be ignored. An order history truncated at Alpaca's
    500-item maximum must never be described as complete.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    now = now or datetime.now(timezone.utc)
    # A Neon-managed role can carry inherited admin privileges by default.
    # Refuse anything other than a properly restricted observer credential.
    journal.assert_monitor_only()
    control = journal.read_control()
    unresolved = journal.read_unresolved_intents(limit=501)
    if len(unresolved) >= 501:
        # Cannot rely on a partial durable ledger of outstanding orders.
        raise RuntimeError("Durable order intent list exceeds inspection limit")
    day_open_equity, peak_equity = journal.read_equity_references(
        new_york_regular_open_utc(now), now)
    account = trading_client.get_account()
    clock = trading_client.get_clock()
    positions = trading_client.get_all_positions()
    # Filled/accepted durable parent intents are absent from the
    # unresolved-only ledger. Fetch their immutable original stop terms
    # through SELECT-only Neon before treating any position as covered.
    durable_stops = None
    if positions:
        durable_stops = journal.read_approved_stop_intents(limit=501)
        if len(durable_stops) >= 501:
            raise RuntimeError("Durable protective-intent list is truncated")
    open_orders = trading_client.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, limit=500, nested=True))
    # Recent closed orders supplement open orders. Durable unresolved intents
    # ALWAYS remain blockers until checked individually by client order ID.
    recent_closed = trading_client.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.CLOSED, limit=500, nested=True,
        after=now - timedelta(days=30)))
    complete = len(open_orders) < 500 and len(recent_closed) < 500
    audit = reconcile_observation(
        positions, [*open_orders, *recent_closed], unresolved,
        order_fetch_complete=complete, approved_stops=durable_stops)

    state = PaperSafetyState(
        equity=float(money(account.equity)),
        available_cash=float(money(account.cash)),
        day_open_equity=float(day_open_equity)
        if day_open_equity is not None else None,
        recorded_peak_equity=float(peak_equity)
        if peak_equity is not None else None,
        open_positions=len(positions),
        open_orders=len(open_orders),
        market_open=bool(clock.is_open),
        trading_blocked=bool(getattr(account, "trading_blocked", True)),
        recent_quote_seconds=None,  # no consolidated real-time quote
        durable_journal_healthy=True,
        reconciled_with_broker=audit.consistent,
        watchdog_heartbeat_seconds=None,  # no persistent hosted watchdog
        kill_switch_active=control.kill_switch_active,
        # Fail closed even if someone changes production DB control.
        order_submissions_explicitly_enabled=False,
        broker_endpoint=PAPER_ENDPOINT,
    )
    blockers = list(dict.fromkeys([
        *execution_blockers(state), *audit.blockers,
        "preflight_has_no_broker_write_capability",
    ]))
    return {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": now.isoformat(),
        "broker_paper_url": PAPER_ENDPOINT,
        "equity_usd": round(state.equity, 2),
        "available_cash_usd": round(state.available_cash, 2),
        "market_open": state.market_open,
        "new_york_regular_open_utc": new_york_regular_open_utc(now).isoformat(),
        "daily_open_equity_available": day_open_equity is not None,
        "peak_equity_available": peak_equity is not None,
        "journal_kill_switch_active": control.kill_switch_active,
        "journal_submissions_enabled": control.submissions_enabled,
        "code_submissions_enabled": False,
        "reconciliation": audit.to_dict(),
        "safety_blockers": blockers,
        "can_submit_orders": False,
        "broker_write_operations": 0,
    }


def run_once(*, output: str) -> dict:
    from alpaca.trading.client import TradingClient
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    dsn = os.getenv("PAPER_JOURNAL_DSN")
    if not key or not secret:
        raise RuntimeError("Alpaca paper API secrets are missing")
    if not dsn:
        raise RuntimeError("PAPER_JOURNAL_DSN secret not configured")
    # Hard-coded PAPER endpoint. No option to use live broker credentials.
    trading = TradingClient(
        key, secret, paper=True, url_override=PAPER_ENDPOINT)
    journal = PostgresPaperJournal(dsn)
    report = read_only_tick(trading, journal)
    incident = assess_read_only_report(report)
    report["monitor_incident"] = incident.as_dict()
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="PAPER read-only safety worker")
    parser.add_argument("--output", default=".state/paper-worker-preflight.json")
    parser.add_argument("--loop", action="store_true",
                        help="Repeat read-only checks on a separately hosted worker")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.loop and not 30 <= args.interval_seconds <= 3600:
        parser.error("Loop interval must be 30 to 3600 seconds")
    ntfy_topic = os.getenv("PAPER_MONITOR_NTFY_TOPIC")
    notifications = NotificationThrottle(cooldown_seconds=3600)

    def attempt_notice(incident):
        if not ntfy_topic:
            return
        try:
            notifications.maybe_send(
                incident, topic=ntfy_topic, now_seconds=time.monotonic())
        except NotificationError:
            # Topic values and transport error strings are secrets.
            print("ALERT DELIVERY FAILED (credentials withheld).",
                  file=sys.stderr)

    while True:
        try:
            report = run_once(output=args.output)
        except Exception:
            # Driver errors can contain DB connection strings or API URLs
            # with credentials. Keep all exception details out of logs.
            print("BLOCKED: broker/Neon preflight unavailable; no orders sent.",
                  file=sys.stderr)
            attempt_notice(unverified_incident())
            if not args.loop:
                raise SystemExit(2)
        else:
            print(
                f"READ ONLY: paper equity ${report['equity_usd']:.2f}, "
                f"positions {report['reconciliation']['open_broker_positions']}, "
                f"open orders {report['reconciliation']['open_broker_orders']}, "
                f"blocked checks {len(report['safety_blockers'])}; "
                f"monitor level {report['monitor_incident']['level']}; "
                "ZERO order submissions."
            )
            if report["monitor_incident"]["level"] == "critical":
                print("CRITICAL: paper state unsafe or unverified; "
                      "review Alpaca positions and stop protection.",
                      file=sys.stderr)
                attempt_notice(assess_read_only_report(report))
                if not args.loop:
                    raise SystemExit(3)
            if not args.loop:
                break
        if args.loop:
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
