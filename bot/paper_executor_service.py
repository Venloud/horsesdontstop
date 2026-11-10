"""Production Alpaca PAPER pilot coordinator.

The default command only reads PAPER/Neon state and writes a local receipt.
It may create one durable, bounded GTC OTO reservation only after all live
evidence gates pass. Actual broker dispatch remains hard-disabled at the
gateway until its separate release gate and an owner-only Neon approval pass.
An ambiguous broker POST is never retried.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo
from uuid import uuid4

from .paper_safety import PAPER_ENDPOINT, PaperSafetyState, TradeBlocked, money


NY = ZoneInfo("America/New_York")


def build_safety_state(*, account, clock, positions, orders, control,
                       unresolved, day_open_equity, recorded_peak_equity,
                       lifetime_evidence, lease, calendar_verified,
                       quote_age_seconds, journal_healthy,
                       broker_reconciled) -> PaperSafetyState:
    """Build a fail-closed state only from explicit independently read facts."""
    if not isinstance(lifetime_evidence, dict):
        raise TradeBlocked("Authenticated lifetime accounting proof missing")
    history_complete = (
        lifetime_evidence.get("history_scope") ==
        "authenticated cash-only account lifetime"
        and lifetime_evidence.get("equity_history_explained") is True
        and lifetime_evidence.get("cashflows_reconciled") is True
        and lifetime_evidence.get("can_submit_orders") is False
        and lifetime_evidence.get("account_id_sha256") == _account_hash(account)
        and money(lifetime_evidence.get("equity")) == money(getattr(account, "equity", None))
        and money(lifetime_evidence.get("cash")) == money(getattr(account, "cash", None))
    )
    lease_age = (int(lease.age_seconds)
                 if getattr(lease, "healthy", False) is True
                 and type(getattr(lease, "age_seconds", None)) in (int, float)
                 and lease.age_seconds >= 0 else None)
    kill = getattr(control, "kill_switch_active", None)
    enabled = getattr(control, "submissions_enabled", None)
    trading_blocked = getattr(account, "trading_blocked", None)
    equity = getattr(account, "equity", None)
    cash = getattr(account, "cash", None)
    if (type(kill) is not bool or type(enabled) is not bool
            or type(trading_blocked) is not bool
            or not isinstance(positions, (list, tuple))
            or not isinstance(orders, (list, tuple))
            or not isinstance(unresolved, (list, tuple))):
        raise TradeBlocked("Live safety observations are malformed")
    return PaperSafetyState(
        equity=float(equity), available_cash=float(cash),
        day_open_equity=(None if day_open_equity is None
                         else float(day_open_equity)),
        recorded_peak_equity=(None if recorded_peak_equity is None
                              else float(recorded_peak_equity)),
        open_positions=len(positions), open_orders=len(orders),
        market_open=getattr(clock, "is_open", None) is True,
        trading_blocked=trading_blocked,
        recent_quote_seconds=(int(quote_age_seconds)
                              if type(quote_age_seconds) in (int, float)
                              and quote_age_seconds >= 0 else None),
        durable_journal_healthy=journal_healthy is True,
        reconciled_with_broker=(broker_reconciled is True and not unresolved),
        watchdog_heartbeat_seconds=lease_age,
        kill_switch_active=kill,
        order_submissions_explicitly_enabled=enabled,
        broker_endpoint=PAPER_ENDPOINT,
        audited_external_flows_complete=history_complete,
        verified_equity_history_complete=history_complete,
        exchange_calendar_verified=calendar_verified is True,
    )


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                prefix=path.name + ".", delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _account_hash(account) -> str:
    from uuid import UUID
    try:
        normalized = str(UUID(str(account.id))).lower()
    except (ValueError, TypeError, AttributeError):
        raise TradeBlocked("Authenticated PAPER account identity unavailable") from None
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def _calendar_for_today(trading, now):
    from alpaca.trading.requests import GetCalendarRequest
    today = now.astimezone(NY).date()
    try:
        sessions = trading.get_calendar(GetCalendarRequest(
            start=today - timedelta(days=14), end=today))
    except Exception:
        raise TradeBlocked("Authenticated PAPER market calendar unavailable") from None
    if not isinstance(sessions, (list, tuple)):
        raise TradeBlocked("PAPER market calendar response malformed")
    days = [getattr(session, "date", None) for session in sessions]
    if (not days or len(days) != len(set(days))
            or any(not isinstance(day, date) for day in days)
            or any(day < today - timedelta(days=14) or day > today for day in days)):
        raise TradeBlocked("PAPER market calendar history incomplete")
    return today, sessions, max((day for day in days if day < today), default=None)


def _opening_and_peak(journal, now, sessions):
    today = now.astimezone(NY).date()
    session = next((row for row in sessions
                    if getattr(row, "date", None) == today), None)
    if session is None:
        return None, None
    opens = getattr(session, "open", None)
    if not isinstance(opens, datetime):
        return None, None
    if opens.tzinfo is None or opens.utcoffset() is None:
        opens = opens.replace(tzinfo=NY)
    open_utc = opens.astimezone(timezone.utc)
    return journal.read_equity_references(open_utc, now)


def _verify_supervisor_receipt(receipt_path: Path, heartbeat_journal, now,
                               *, expected_pid=None, check_observer=None):
    def record(name, status, reason=None, age_seconds=None, error_type=None):
        if check_observer is not None:
            check_observer(name, {
                "status": status, "reason": reason,
                "age_seconds": age_seconds,
                "error_type": error_type,
            })

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(
            receipt["observed_at_utc"].replace("Z", "+00:00"))
    except Exception:
        record("local_supervisor_receipt", "FAIL", "missing_or_malformed")
        record("supervisor_process_identity", "NOT_VERIFIED", "receipt_unavailable")
        record("supervisor_poll_status", "NOT_VERIFIED", "receipt_unavailable")
        record("cloudflare_external_ack", "NOT_VERIFIED", "receipt_unavailable")
        record("neon_supervisor_lease", "NOT_VERIFIED", "worker_identity_unavailable")
        raise TradeBlocked("Independent supervisor lease or external ACK is stale") from None

    record("local_supervisor_receipt", "PASS")
    receipt_pid = receipt.get("pid")
    if type(receipt_pid) is int and expected_pid is not None \
            and receipt_pid == expected_pid:
        record("supervisor_process_identity", "PASS")
    else:
        record("supervisor_process_identity", "FAIL", "pid_does_not_match_live_supervisor")
    receipt_status = receipt.get("status")
    if receipt_status == "read_only_poll_ok":
        record("supervisor_poll_status", "PASS")
    elif receipt_status == "closed_flat_idle":
        record("supervisor_poll_status", "FAIL", "closed_flat_idle_not_trading_lease")
    else:
        record("supervisor_poll_status", "FAIL", "not_a_successful_active_poll")

    if receipt.get("external_ack") is True:
        record("cloudflare_external_ack", "PASS")
    else:
        record("cloudflare_external_ack", "FAIL", "ack_not_recorded")

    age = None
    if (observed.tzinfo is None or observed.utcoffset() is None
            or now.tzinfo is None or now.utcoffset() is None):
        record("local_receipt_freshness", "FAIL", "timestamp_not_timezone_aware")
    else:
        age = (now - observed).total_seconds()
        if 0 <= age <= 120:
            record("local_receipt_freshness", "PASS", age_seconds=age)
        elif age < 0:
            record("local_receipt_freshness", "FAIL", "receipt_timestamp_in_future",
                   age_seconds=age)
        else:
            record("local_receipt_freshness", "FAIL", "receipt_older_than_120_seconds",
                   age_seconds=age)

    worker_id = receipt.get("worker_id")
    try:
        if not isinstance(worker_id, str):
            raise ValueError
        lease = heartbeat_journal.inspect(expected_worker_id=worker_id)
    except Exception as exc:
        # A fixed exception class distinguishes a role/permission failure from
        # an unhealthy lease without persisting a DSN or driver error string.
        record("neon_supervisor_lease", "FAIL", "independent_inspection_failed",
               error_type=type(exc).__name__)
        lease = None
    if lease is not None:
        if lease.healthy is True:
            record("neon_supervisor_lease", "PASS",
                   age_seconds=lease.age_seconds)
        else:
            record("neon_supervisor_lease", "FAIL",
                   ",".join(lease.blockers) or "lease_unhealthy",
                   age_seconds=lease.age_seconds)

    if receipt_status == "closed_flat_idle":
        raise TradeBlocked(
            "Supervisor is broker-verified closed/flat idle; no active trading lease")
    if receipt_status != "read_only_poll_ok":
        raise TradeBlocked("Supervisor receipt does not prove a successful active poll")
    if not (type(receipt_pid) is int and expected_pid is not None
            and receipt_pid == expected_pid):
        raise TradeBlocked("Supervisor receipt does not match the live supervisor process")
    if receipt.get("external_ack") is not True:
        raise TradeBlocked("Cloudflare external heartbeat was not acknowledged")
    if age is None or not 0 <= age <= 120:
        if age is not None and age < 0:
            raise TradeBlocked("Supervisor receipt timestamp is ahead of executor clock")
        raise TradeBlocked("Local supervisor receipt is older than 120 seconds")
    if lease is None or lease.healthy is not True:
        raise TradeBlocked("Independent Neon supervisor lease is stale or unhealthy")
    return lease


def _live_components(*, stage_observer=None):
    """Create fixed PAPER clients and least-privilege database adapters."""
    def stage(name):
        if stage_observer:
            stage_observer(name)
    stage("validate_runtime_configuration")
    names = ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
             "PAPER_JOURNAL_DSN", "PAPER_EXECUTOR_DSN",
             "PAPER_RECONCILER_DSN", "PAPER_ACCOUNT_SHA256",
             "PAPER_SUPERVISOR_RECEIPT", "PAPER_SUPERVISOR_RECEIPT_PID")
    if (any(not os.environ.get(name) for name in names)
            or not os.environ["PAPER_SUPERVISOR_RECEIPT_PID"].isdecimal()
            or int(os.environ["PAPER_SUPERVISOR_RECEIPT_PID"]) < 1):
        raise TradeBlocked("Required DPAPI-backed PAPER runtime configuration missing")
    from alpaca.data.historical import StockHistoricalDataClient
    from .paper_store import PostgresPaperJournal
    from .paper_execution_journal import PaperExecutorJournal
    from .paper_status_reconciler import PaperStatusReconcilerJournal
    from .paper_worker_lease import PaperWorkerHeartbeat
    from .paper_broker_transport import (
        make_reconciler_paper_client, make_supervised_paper_client,
    )
    key, secret = os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
    stage("initialize_alpaca_paper_client")
    trading = make_supervised_paper_client(key, secret)
    stage("initialize_market_data_client")
    market_data = StockHistoricalDataClient(key, secret)
    stage("initialize_monitor_database_role")
    monitor = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
    stage("initialize_executor_database_role")
    executor = PaperExecutorJournal(os.environ["PAPER_EXECUTOR_DSN"])
    stage("initialize_reconciler_database_role")
    reconciler = PaperStatusReconcilerJournal(os.environ["PAPER_RECONCILER_DSN"])
    stage("initialize_independent_broker_reader")
    reconciler_trading = make_reconciler_paper_client(key, secret)
    stage("initialize_heartbeat_reader")
    heartbeat = PaperWorkerHeartbeat(os.environ["PAPER_JOURNAL_DSN"])
    return {
        "trading": trading,
        "market_data": market_data,
        "monitor": monitor, "executor": executor, "reconciler": reconciler,
        "reconciler_trading": reconciler_trading, "heartbeat": heartbeat,
        "expected_account_hash": os.environ["PAPER_ACCOUNT_SHA256"],
        "receipt_path": Path(os.environ["PAPER_SUPERVISOR_RECEIPT"]),
        "expected_supervisor_pid": int(os.environ["PAPER_SUPERVISOR_RECEIPT_PID"]),
    }


def _run_maintenance(trading, monitor, positions, orders, now):
    """Plan/apply risk-reducing actions; re-plan once after a cancel."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .paper_position_manager import execute_maintenance, plan_maintenance
    from .paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED
    released = PAPER_ORDER_SUBMISSIONS_RELEASED is True
    results, unexplained, writes = [], set(), 0
    for _ in range(2):
        if not positions and not orders:
            break
        from .paper_reconcile import orders_with_recent_parents
        with_parents, _ = orders_with_recent_parents(trading, orders, now=now)
        plan = plan_maintenance(
            positions=positions, orders=with_parents,
            unresolved=monitor.read_unresolved_intents(limit=501),
            approved_stops=monitor.read_approved_stop_intents(limit=501), now=now)
        unexplained.update(plan.unexplained_symbols)
        if not plan.actions:
            break
        done = execute_maintenance(trading, plan, released=released)
        results.extend(done)
        applied = [d for d in done if d[0] in ("canceled", "flatten_submitted")]
        writes += len(applied)
        for kind, action, cid in applied:
            _notify("risk action", f"{kind} for {cid}", "high")
        if not any(d[0] == "canceled" for d in done):
            break
        positions = trading.get_all_positions()
        orders = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, nested=True, limit=500))
    if unexplained:
        _notify("unexplained position",
                "Not touched: " + ",".join(sorted(unexplained)), "urgent")
    return ({"results": [list(r) for r in results],
             "unexplained_symbols": sorted(unexplained),
             "released": released, "broker_writes": writes}, positions, orders)


def _managed_position_report(monitor, positions, orders, unresolved, *,
                             trading=None, now=None):
    """Holding: prove the exposure is journaled and stop-protected, or block."""
    from .paper_reconcile import orders_with_recent_parents, reconcile_observation
    complete = len(orders) < 500
    if trading is not None:
        orders, complete = orders_with_recent_parents(
            trading, orders, now=now or datetime.now(timezone.utc))
    rec = reconcile_observation(
        positions, orders, unresolved, order_fetch_complete=complete,
        approved_stops=monitor.read_approved_stop_intents(limit=501))
    if rec.managed_exposure is not True or rec.uncovered_symbols:
        raise TradeBlocked("Open PAPER exposure is not verified as stop-protected")
    return {
        "mode": "POSITION_MANAGED",
        "reason": "Open PAPER exposure verified against durable GTC OTO stop",
        "positions": [{"symbol": getattr(p, "symbol", None),
                       "qty": str(getattr(p, "qty", None)),
                       "unrealized_pl": str(getattr(p, "unrealized_pl", None))}
                      for p in positions],
        "open_orders": rec.open_broker_orders,
        "broker_orders": 0, "database_writes": 0,
    }


def prepare_live_candidate(*, components, now=None, symbols=None, utcnow=None,
                          prepared_observer=None, stage_observer=None,
                          check_observer=None,
                          candidate_evaluation_observer=None, autonomous=False):
    """Read fresh evidence; reserve one proposal, or in autonomous mode
    policy-authorize and submit at most one protected PAPER entry."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestQuoteRequest
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .live_quotes import preview_live_paper_entry
    from .paper_accounting import fetch_account_history, reconcile_cash_lifetime
    from .paper_execution_journal import PaperExecutorJournal
    from .paper_readiness_cli import research_candidates
    from .paper_supervised_execution import submit_paper_once_supervised
    from .robustness_cli import BROADER_SYMBOLS

    utcnow = utcnow or (lambda: datetime.now(timezone.utc))
    def stage(name):
        if stage_observer:
            stage_observer(name)
    stage("validate_executor_clock")
    now = now or utcnow()
    if now.tzinfo is None:
        raise TradeBlocked("Timezone-aware executor clock required")
    symbols = tuple(symbols or BROADER_SYMBOLS)
    trading, market_data = components["trading"], components["market_data"]
    monitor, executor = components["monitor"], components["executor"]
    reconciler = components["reconciler"]
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        raise TradeBlocked("Executor client is not bound to Alpaca PAPER")
    stage("verify_database_role_permissions")
    monitor.assert_monitor_only()
    executor.assert_executor_permissions(reservation=True)
    stage("read_safety_controls")
    control = monitor.read_control()
    paused = False
    if autonomous is True:
        paused = (control.kill_switch_active, control.submissions_enabled) == (True, False)
        if not paused and (control.kill_switch_active,
                           control.submissions_enabled) != (False, True):
            raise TradeBlocked("PAPER controls are incoherent")
    elif control.kill_switch_active is not True or control.submissions_enabled is not False:
        raise TradeBlocked("PAPER preparation requires kill ON and submissions OFF")
    stage("authenticate_alpaca_paper_account")
    account = trading.get_account()
    expected_hash = components["expected_account_hash"]
    if _account_hash(account) != expected_hash:
        raise TradeBlocked("Authenticated PAPER account does not match pinned identity")
    stage("read_positions_orders_and_journal")
    clock = trading.get_clock()
    positions = trading.get_all_positions()
    orders = trading.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, nested=True, limit=500))
    # Independent GET-only reconciliation runs before the blocker check so an
    # earlier unapproved, never-dispatched proposal can expire (audited, via
    # the reconciler role) instead of blocking every later session forever.
    stage("reconcile_prior_reservations")
    from .paper_status_reconciler import pending_blockers, reconcile_pending_once
    from .live_quotes import (
        MAX_SPREAD_BPS, inspect_live_quote, preview_live_paper_entry,
    )
    from .paper_reconciler_service import PAPER_RECONCILIATION_SERVICE_RELEASED
    reconcile_error = None
    try:
        reconciled = reconcile_pending_once(
            trading=components["reconciler_trading"], journal=reconciler,
            record=(autonomous is True
                    and PAPER_RECONCILIATION_SERVICE_RELEASED is True),
            expire_unapproved=True)
    except TradeBlocked as exc:
        # Partial fills, unverified stops and lookup errors raise here, and
        # those are exactly the cases that need risk reduction. Maintenance
        # still runs; the original block is re-raised afterwards.
        if autonomous is not True:
            raise
        reconcile_error, reconciled = exc, ()
    expired = [cid for cid, status in reconciled if status == "expired"]
    maintenance = None
    if autonomous is True:
        # After reconciliation so a just-filled entry is judged on current
        # journal state; risk-reducing, so it also runs while paused.
        stage("risk_reducing_maintenance")
        maintenance, positions, orders = _run_maintenance(
            trading, monitor, positions, orders, utcnow())
        if reconcile_error is not None:
            stage("reconcile_prior_reservations")
            raise reconcile_error
        if paused:
            return {"mode": "PAUSED", "reason": "Kill switch engaged; no entries",
                    "maintenance": maintenance,
                    "broker_orders": maintenance["broker_writes"],
                    "database_writes": len(expired)}
    stage("read_positions_orders_and_journal")
    unresolved = monitor.read_unresolved_intents(limit=501)
    if len(unresolved) >= 501:
        raise TradeBlocked("PAPER journal unresolved intent list incomplete")
    awaiting = [cid for cid, status in pending_blockers(reconciled)
                if status == "reserved"]
    if (unresolved and not positions and not orders
            and len(awaiting) == len(unresolved)
            and {row[0] for row in unresolved} == set(awaiting)
            and all(row[1] == "reserved" for row in unresolved)):
        return {"mode": "AWAITING_OWNER_APPROVAL",
                "reason": "Prepared proposal is inside its owner-approval window; "
                          "it expires automatically if unapproved",
                "pending_client_order_ids": awaiting,
                "expired_client_order_ids": expired,
                "broker_orders": 0, "database_writes": len(expired)}
    if autonomous is True and (positions or orders):
        report = _managed_position_report(monitor, positions, orders, unresolved,
                                          trading=trading, now=utcnow())
        report["maintenance"] = maintenance
        return report
    if unresolved or positions or orders:
        raise TradeBlocked("Earlier PAPER orders or existing exposure require reconciliation")
    if autonomous is True and monitor.count_entries_today() >= 1:
        return {"mode": "NO_TRADE", "reason": "Daily PAPER entry already used",
                "expired_client_order_ids": expired,
                "broker_orders": 0, "database_writes": len(expired)}
    if pending_blockers(reconciled):
        raise TradeBlocked("Existing PAPER order requires independent reconciliation")
    stage("verify_account_lifetime_history")
    account, activities, history, complete = fetch_account_history(trading)
    lifetime = reconcile_cash_lifetime(
        account=account, activities=activities, history=history,
        positions=positions, orders=orders,
        expected_account_hash=expected_hash, observed_at=now,
        activities_complete=complete)

    stage("verify_market_calendar_and_equity_baseline")
    today, sessions, latest_session = _calendar_for_today(trading, now)
    if latest_session is None:
        return {"mode": "NO_TRADE", "reason": "No previous verified trading session",
                "broker_orders": 0, "database_writes": 0}
    day_open, peak = _opening_and_peak(monitor, now, sessions)
    stage("verify_supervisor_lease_and_external_ack")
    lease = _verify_supervisor_receipt(
        # The supervisor updates its receipt every poll tick. A preflight can
        # take long enough for that receipt to be newer than the initial `now`;
        # compare against a fresh clock sample or valid evidence looks future.
        components["receipt_path"], components["heartbeat"], utcnow(),
        expected_pid=components["expected_supervisor_pid"],
        check_observer=check_observer)
    stage("evaluate_strategy_candidates")
    candidates = research_candidates(
        market_data, symbols, now=now, required_session=latest_session)
    if not candidates:
        return {"mode": "NO_TRADE", "reason": "No eligible momentum_volume signal",
                "session": today.isoformat(), "broker_orders": 0,
                "database_writes": 0}

    candidate_evaluations = []

    def record_candidate(candidate, quote, at, *, status, reason=None,
                         preview=None):
        row = {
            "symbol": candidate["symbol"],
            "momentum_score": candidate["momentum_score"],
            "status": status,
            "reason": reason,
        }
        if quote is not None and at is not None:
            try:
                row.update(inspect_live_quote(quote, now=at).diagnostic())
            except TradeBlocked as exc:
                row["quote_validation_reason"] = str(exc)
            row["max_spread_bps"] = float(MAX_SPREAD_BPS)
        if preview is not None:
            row.update({
                "shares": preview.shares,
                "buy_limit_usd": preview.buy_limit_usd,
                "stop_usd": preview.stop_usd,
            })
        candidate_evaluations.append(row)
        if candidate_evaluation_observer is not None:
            candidate_evaluation_observer(row)

    candidate_rejections = {
        "Spread too wide for risk-controlled entry",
        "No affordable whole share within 1% risk, equity exposure, and available buying power",
        "Live market quote is stale or timestamp is in the future",
        "Quote missing timezone-aware timestamp",
        "Crossed or zero-spread quote cannot be trusted",
        "Invalid bid", "Invalid ask", "Missing bid_size", "Invalid bid_size",
        "Missing ask_size", "Invalid ask_size", "Invalid attached stop trigger",
    }

    for candidate in candidates:
        symbol = candidate["symbol"]
        stage("verify_live_quote_and_risk")
        latest = market_data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))
        quote = latest.get(symbol)
        if quote is None:
            record_candidate(candidate, None, None, status="BLOCKED",
                             reason="latest_quote_unavailable")
            continue
        if (getattr(clock, "is_open", None) is not True
                or getattr(account, "trading_blocked", None) is not False):
            raise TradeBlocked("PAPER account or regular market session is not eligible")
        now = utcnow()
        try:
            preview = preview_live_paper_entry(
                symbol=symbol, quote=quote, equity=account.equity,
                available_cash=account.cash,
                available_buying_power=account.buying_power,
                now=now, market_open=clock.is_open)
        except TradeBlocked as exc:
            if str(exc) not in candidate_rejections:
                raise
            record_candidate(candidate, quote, now, status="BLOCKED",
                             reason=str(exc))
            continue
        record_candidate(candidate, quote, now, status="ELIGIBLE",
                         preview=preview)
        lease = _verify_supervisor_receipt(
            components["receipt_path"], components["heartbeat"], now,
            expected_pid=components["expected_supervisor_pid"])

        def verify_watchdog():
            fresh_lease = _verify_supervisor_receipt(
                components["receipt_path"], components["heartbeat"],
                utcnow(), expected_pid=components["expected_supervisor_pid"])
            return int(fresh_lease.age_seconds)

        state = build_safety_state(
            account=account, clock=clock, positions=positions, orders=orders,
            control=control, unresolved=unresolved,
            day_open_equity=day_open, recorded_peak_equity=peak,
            lifetime_evidence=lifetime, lease=lease,
            calendar_verified=True, quote_age_seconds=preview.quote_age_seconds,
            journal_healthy=True, broker_reconciled=True)
        from .paper_safety import execution_blockers
        cutoffs = {"daily_three_percent_loss_cutoff",
                   "fifteen_percent_drawdown_cutoff"} & set(execution_blockers(state))
        if cutoffs:
            # Independent self-stop: any service may PAUSE; only the operator re-arms.
            executor.engage_kill_switch("executor: " + ",".join(sorted(cutoffs)))
            raise TradeBlocked("Loss cutoff reached; kill switch engaged")
        if autonomous is True:
            stage("policy_authorize_and_submit")
            submitted = submit_paper_once_supervised(
                trading=trading, journal=executor, market_quote=quote,
                preview=preview, safety_state=state, approval=None, now=now,
                expected_account_sha256=expected_hash, market_data=market_data,
                policy_authorize=True, verify_watchdog=verify_watchdog)
            return {
                "mode": "SUBMITTED_POLICY_AUTHORIZED",
                "client_order_id": submitted.client_order_id,
                "broker_order_id": submitted.broker_order_id,
                "result": submitted.result,
                "symbol": preview.symbol, "shares": preview.shares,
                "buy_limit_usd": preview.buy_limit_usd,
                "stop_usd": preview.stop_usd,
                "estimated_stop_risk_usd": preview.stop_distance_risk_usd,
                "order_notional_usd": preview.max_limit_purchase_usd,
                "momentum_score": candidate["momentum_score"],
                "candidate_evaluations": candidate_evaluations,
                "expired_client_order_ids": expired,
                "broker_orders": int(submitted.submitted_to_paper_broker),
            }
        stage("reserve_exact_pilot_proposal")
        prepared = submit_paper_once_supervised(
            trading=trading, journal=executor, market_quote=quote,
            preview=preview, safety_state=state, approval=None, now=now,
            expected_account_sha256=expected_hash, market_data=market_data,
            prepare_only=True, verify_watchdog=verify_watchdog,
            prepared_observer=prepared_observer)
        from .paper_pilot_approval import render_owner_approval_sql
        result = {
            "mode": "PREPARED_FOR_EXACT_OWNER_REVIEW",
            "prepared": prepared.to_dict(),
            "owner_approval_sql": render_owner_approval_sql(prepared, now=now),
            "momentum_score": candidate["momentum_score"],
            "candidate_evaluations": candidate_evaluations,
            "risk_gate": "PASS_FOR_PROPOSAL_ONLY",
            "risk_inputs": {
                "account_equity_usd": float(money(account.equity)),
                "available_cash_usd": float(money(account.cash)),
                "available_buying_power_usd": float(money(account.buying_power)),
                "max_equity_exposure_fraction": 1.0,
                "fee_reserve_usd": 0.50,
                "available_notional_after_reserve_usd": float(max(
                    min(money(account.equity), money(account.cash),
                        money(account.buying_power)) - money("0.50"),
                    money("0"))),
                "per_trade_risk_budget_usd": preview.risk_budget_usd,
                "estimated_stop_risk_usd": preview.stop_distance_risk_usd,
                "order_notional_usd": preview.max_limit_purchase_usd,
            },
            "durable_reservation_written": True,
            "expired_client_order_ids": expired,
            "database_writes": 1 + len(expired),
            "broker_orders": 0,
            "submissions_enabled": False,
            "kill_switch_active": True,
        }
        return result
    return {"mode": "NO_TRADE", "reason": "No candidate passed live quote, risk, exposure, cash, buying-power, and whole-share gates",
            "candidate_evaluations": candidate_evaluations,
            "broker_orders": 0, "database_writes": 0}


def dispatch_approved_artifact(*, components, artifact, now=None, utcnow=None):
    """Attempt one dispatch through the hard-gated gateway after owner SQL.

    The supplied JSON is not authority. The database's owner-only approval
    row and single-use dispatch function remain the authentication boundary.
    """
    from datetime import timedelta
    from .paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED
    if PAPER_ORDER_SUBMISSIONS_RELEASED is not True:
        raise TradeBlocked("PAPER broker submissions NOT RELEASED")

    from .live_quotes import LivePaperEntryPreview, build_live_protected_oto_request
    from .paper_supervised_execution import (
        ExplicitPaperTradeApproval, _plan_from_quoted_preview,
        _paper_account_sha256, _broker_did_not_find_order, _request_sha256,
        submit_paper_once_supervised,
    )
    from .paper_pilot_approval_cli import approval_sql_from_document
    from alpaca.data.requests import StockLatestQuoteRequest
    from alpaca.data.enums import DataFeed
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .paper_accounting import fetch_account_history, reconcile_cash_lifetime
    from .paper_execution_journal import PaperExecutorJournal
    from .paper_readiness_cli import research_candidates
    from .robustness_cli import BROADER_SYMBOLS

    if not isinstance(artifact, dict) or artifact.get("mode") != "PREPARED_FOR_EXACT_OWNER_REVIEW":
        raise TradeBlocked("Prepared exact-order artifact is required")
    prepared_doc = artifact.get("prepared")
    if not isinstance(prepared_doc, dict):
        raise TradeBlocked("Prepared order terms are missing")
    preview_doc = prepared_doc.get("quote_preview")
    if not isinstance(preview_doc, dict):
        raise TradeBlocked("Prepared live quote is missing")
    try:
        preview = LivePaperEntryPreview(**preview_doc)
        # Validate artifact structure and visible order terms with the same
        # pure checks used before an owner approval SQL statement is rendered.
        approval_sql_from_document(prepared_doc)
    except Exception:
        raise TradeBlocked("Prepared order artifact failed integrity checks") from None
    plan = _plan_from_quoted_preview(preview)
    utcnow = utcnow or (lambda: datetime.now(timezone.utc))
    now = now or utcnow()
    if now.tzinfo is None:
        raise TradeBlocked("Timezone-aware dispatch clock required")
    trading, market_data = components["trading"], components["market_data"]
    monitor, executor = components["monitor"], components["executor"]
    control = monitor.read_control()
    if control.kill_switch_active is not False or control.submissions_enabled is not True:
        raise TradeBlocked("PAPER dispatch requires a separately released kill switch and submission control")
    executor.assert_executor_permissions(reservation=True)
    expected_hash = components["expected_account_hash"]
    account = trading.get_account()
    if _paper_account_sha256(account) != expected_hash:
        raise TradeBlocked("Authenticated PAPER account does not match pinned identity")
    clock = trading.get_clock()
    positions = trading.get_all_positions()
    orders = trading.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, nested=True, limit=500))
    unresolved = monitor.read_unresolved_intents(limit=501)
    if (len(unresolved) != 1
            or unresolved[0][0] != plan.client_order_id
            or unresolved[0][1] != "reserved"):
        raise TradeBlocked("A prior or conflicting PAPER intent requires reconciliation")
    # An exact, definite broker 404 is required before dispatch. Any API error,
    # found order, positions, or open order blocks; no retry path exists.
    _broker_did_not_find_order(trading, plan.client_order_id)
    if positions or orders:
        raise TradeBlocked("Existing PAPER exposure blocks dispatch")
    account, activities, history, complete = fetch_account_history(trading)
    lifetime = reconcile_cash_lifetime(
        account=account, activities=activities, history=history,
        positions=positions, orders=orders,
        expected_account_hash=expected_hash, observed_at=now,
        activities_complete=complete)
    today, sessions, latest_session = _calendar_for_today(trading, now)
    day_open, peak = _opening_and_peak(monitor, now, sessions)
    lease = _verify_supervisor_receipt(
        components["receipt_path"], components["heartbeat"], now,
        expected_pid=components["expected_supervisor_pid"])
    if not getattr(clock, "is_open", False) or latest_session is None:
        raise TradeBlocked("PAPER market session is not open")
    candidates = research_candidates(
        market_data, [plan.symbol], now=now, required_session=latest_session)
    if len(candidates) != 1 or candidates[0]["symbol"] != plan.symbol:
        raise TradeBlocked("Approved symbol no longer has a verified strategy signal")
    latest = market_data.get_stock_latest_quote(StockLatestQuoteRequest(
        symbol_or_symbols=plan.symbol, feed=DataFeed.IEX))
    quote = latest.get(plan.symbol)
    if quote is None:
        raise TradeBlocked("Fresh approved-symbol IEX quote unavailable")
    from .live_quotes import preview_live_paper_entry
    live_preview = preview_live_paper_entry(
        symbol=plan.symbol, quote=quote, equity=account.equity,
        available_cash=account.cash,
        available_buying_power=account.buying_power,
        now=utcnow(), market_open=True)
    if (prepared_doc.get("account_id_sha256") != expected_hash
            or _request_sha256(build_live_protected_oto_request(live_preview))
            != prepared_doc.get("request_sha256")):
        raise TradeBlocked("Fresh SDK order does not match recorded owner-approved request")
    now = utcnow()
    live_preview = preview_live_paper_entry(
        symbol=plan.symbol, quote=quote, equity=account.equity,
        available_cash=account.cash,
        available_buying_power=account.buying_power,
        now=now, market_open=True)
    lease = _verify_supervisor_receipt(
        components["receipt_path"], components["heartbeat"], now,
        expected_pid=components["expected_supervisor_pid"])
    state = build_safety_state(
        account=account, clock=clock, positions=positions, orders=orders,
        control=control, unresolved=(), day_open_equity=day_open,
        recorded_peak_equity=peak, lifetime_evidence=lifetime, lease=lease,
        calendar_verified=True, quote_age_seconds=live_preview.quote_age_seconds,
        journal_healthy=True, broker_reconciled=True)
    approval = ExplicitPaperTradeApproval(
        client_order_id=plan.client_order_id, symbol=plan.symbol,
        shares=plan.whole_shares, max_buy_limit_usd=plan.estimated_entry,
        protective_stop_usd=plan.protective_stop,
        approved_at_utc=now, expires_at_utc=now + timedelta(seconds=120),
        order_time_in_force="gtc", order_class="oto",
        fee_reserve_usd=0.50,
        account_id_sha256=expected_hash)
    def verify_watchdog():
        fresh_lease = _verify_supervisor_receipt(
            components["receipt_path"], components["heartbeat"],
            utcnow(), expected_pid=components["expected_supervisor_pid"])
        return int(fresh_lease.age_seconds)
    # The dataclass above carries consistency terms only. The owner-only
    # approval row is checked/consumed atomically by the Neon dispatch function.
    return submit_paper_once_supervised(
        trading=trading, journal=executor, market_quote=quote,
        preview=live_preview, safety_state=state, approval=approval, now=now,
        expected_account_sha256=expected_hash, market_data=market_data,
        intent_already_reserved=True, verify_watchdog=verify_watchdog)


def _notify(title, message, priority="default"):
    """Best effort; never affects trading outcome. Topic from DPAPI runner."""
    topic = os.environ.get("PAPER_MONITOR_NTFY_TOPIC")
    if not topic:
        return False
    try:
        from .monitor_notify import send_activity_ntfy
        return send_activity_ntfy(title, message, topic=topic, priority=priority)
    except Exception:
        return False


def run_once(*, output: Path, reserve: bool = False, stage_observer=None,
             check_observer=None, candidate_evaluation_observer=None,
             autonomous=False):
    if reserve is not True:
        raise TradeBlocked("Executor requires explicit --prepare to perform a durable reservation")
    components = _live_components(stage_observer=stage_observer)
    def persist_before_reservation(prepared):
        _atomic_json(output, {
            "mode": "PREPARATION_PENDING_RESERVATION",
            "prepared": prepared.to_dict(),
            "broker_orders": 0,
            "durable_reservation_written": False,
            "instructions": "Check the durable intent is reserved before executing owner approval SQL.",
        })
    report = prepare_live_candidate(
        components=components, prepared_observer=persist_before_reservation,
        stage_observer=stage_observer, check_observer=check_observer,
        candidate_evaluation_observer=candidate_evaluation_observer,
        autonomous=autonomous)
    _atomic_json(output, report)
    if report.get("mode") == "SUBMITTED_POLICY_AUTHORIZED":
        _notify("entry submitted",
                f"BUY {report['shares']} {report['symbol']} limit ${report['buy_limit_usd']} "
                f"stop ${report['stop_usd']}; risk ${report['estimated_stop_risk_usd']}; "
                f"cost ${report['order_notional_usd']}", "high")
    for cid in report.get("expired_client_order_ids") or ():
        _notify("proposal expired", f"Unfilled proposal {cid} expired and was audited")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="allow one guarded durable proposal reservation; never submit")
    mode.add_argument("--autonomous", action="store_true",
                      help="policy-authorized entry: Neon policy + armed controls; one protected order max")
    mode.add_argument("--dispatch-file", type=Path,
                      help="single approved proposal artifact; hard release gate still applies")
    parser.add_argument("--output", default=".state/paper-executor/latest.json")
    parser.add_argument("--runtime-receipt", help="sanitized operational stage receipt")
    args = parser.parse_args(argv)
    dispatch_started = False
    current_stage = "parse_arguments"
    run_id = uuid4().hex
    check_results = {}
    candidate_evaluations = []
    def set_stage(value):
        nonlocal current_stage, dispatch_started
        current_stage = value
        if value == "policy_authorize_and_submit":
            dispatch_started = True
    def record_check(name, result):
        check_results[name] = result
    def runtime_receipt(status, *, error_type=None, reason=None, exit_code=None):
        if not args.runtime_receipt:
            return
        record = {
            "schema": 1, "run_id": run_id,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": ("prepare" if args.prepare else
                     "autonomous" if args.autonomous else "dispatch"),
            "stage": current_stage, "status": status,
            "error_type": error_type, "safe_reason": reason,
            "exit_code": exit_code,
            "broker_order_attempted": bool(dispatch_started),
            "checks": check_results,
            "candidate_evaluations": candidate_evaluations,
        }
        _atomic_json(Path(args.runtime_receipt), record)
    def alert_once(reason, priority="high"):
        if not (args.autonomous and args.runtime_receipt):
            return
        path = Path(args.runtime_receipt).with_name("executor-alerts.json")
        try:
            sent = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            sent = {}
        now_s = datetime.now(timezone.utc).timestamp()
        key = f"{current_stage}:{reason}"[:200]
        if now_s - sent.get(key, 0) < 3600:
            return
        if _notify("executor blocked", f"Stage {current_stage}: {reason}"[:600], priority):
            sent[key] = now_s
            _atomic_json(path, sent)
    try:
        if args.prepare or args.autonomous:
            set_stage("autonomous_live_workflow" if args.autonomous else "prepare_live_workflow")
            result = run_once(output=Path(args.output), reserve=True,
                              stage_observer=set_stage,
                              check_observer=record_check,
                              candidate_evaluation_observer=candidate_evaluations.append,
                              autonomous=bool(args.autonomous))
            broker_orders = int(result.get("broker_orders") or 0)
        else:
            from .paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED
            if PAPER_ORDER_SUBMISSIONS_RELEASED is not True:
                print("PAPER EXECUTOR BLOCKED: PAPER broker submissions NOT RELEASED; no broker order sent.",
                      file=sys.stderr)
                set_stage("dispatch_release_gate")
                runtime_receipt("blocked", reason="PAPER broker submissions NOT RELEASED", exit_code=2)
                return 2
            artifact = json.loads(args.dispatch_file.read_text(encoding="utf-8"))
            components = _live_components()
            dispatch_started = True
            dispatched = dispatch_approved_artifact(
                components=components, artifact=artifact)
            result = asdict(dispatched)
            broker_orders = int(dispatched.submitted_to_paper_broker)
            try:
                _atomic_json(Path(args.output), result)
            except Exception:
                print("PAPER EXECUTOR: broker acknowledgement may have been observed; durable result receipt failed. Outcome requires independent reconciliation; NEVER retry.",
                      file=sys.stderr)
                return 3
        if broker_orders:
            message = ("PAPER EXECUTOR: broker acknowledgement observed; "
                       "independent reconciliation and protective stop remain unproven; NEVER retry.")
        else:
            message = ("PAPER EXECUTOR: "
                       + result.get("mode", result.get("result", "BLOCKED"))
                       + "; broker orders=0; dispatch release remains false.")
        print(message)
        runtime_receipt("completed", exit_code=0)
        return 0
    except TradeBlocked as exc:
        safe_reason = str(exc)
        runtime_receipt("blocked", error_type=type(exc).__name__,
                        reason=safe_reason, exit_code=2)
        alert_once(re.sub(r"[^A-Za-z0-9_:;,. -]", "", safe_reason),
                   "urgent" if "UNKNOWN" in safe_reason else "high")
        if dispatch_started:
            print("PAPER EXECUTOR DISPATCH OUTCOME UNKNOWN OR BLOCKED; perform independent broker reconciliation; NEVER retry this client order ID.",
                  file=sys.stderr)
        else:
            print(f"PAPER EXECUTOR BLOCKED: {exc}; no broker order sent.",
                  file=sys.stderr)
        return 2
    except Exception as exc:
        runtime_receipt("error", error_type=type(exc).__name__,
                        exit_code=2)
        alert_once("unexpected " + type(exc).__name__, "high")
        if dispatch_started:
            print("PAPER EXECUTOR DISPATCH OUTCOME UNKNOWN OR BLOCKED; perform independent broker reconciliation; NEVER retry this client order ID.",
                  file=sys.stderr)
        else:
            print("PAPER EXECUTOR BLOCKED: live evidence, authorization, or durable state unavailable; no broker order sent.",
                  file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
