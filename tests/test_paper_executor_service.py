from hashlib import sha256
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.paper_executor_service import (
    build_safety_state, dispatch_approved_artifact, main,
)
from bot.paper_safety import TradeBlocked, execution_blockers


ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
ACCOUNT_HASH = sha256(ACCOUNT_ID.encode()).hexdigest()


def inputs(**overrides):
    values = dict(
        account=SimpleNamespace(id=ACCOUNT_ID, equity="500", cash="500",
                                 trading_blocked=False),
        clock=SimpleNamespace(is_open=True),
        positions=[], orders=[],
        control=SimpleNamespace(kill_switch_active=True,
                                submissions_enabled=False),
        unresolved=[], day_open_equity="500", recorded_peak_equity="500",
        lifetime_evidence={
            "history_scope": "authenticated cash-only account lifetime",
            "equity_history_explained": True, "cashflows_reconciled": True,
            "can_submit_orders": False, "account_id_sha256": ACCOUNT_HASH,
            "equity": "500", "cash": "500",
        },
        lease=SimpleNamespace(healthy=True, age_seconds=5),
        calendar_verified=True, quote_age_seconds=1,
        journal_healthy=True, broker_reconciled=True,
    )
    values.update(overrides)
    return values


def test_live_evidence_builder_accepts_only_complete_cash_lifetime_for_proposal():
    state = build_safety_state(**inputs())
    blockers = execution_blockers(state)
    assert "kill_switch_active" in blockers
    assert "paper_order_submission_disabled" in blockers
    assert not set(blockers) - {"kill_switch_active", "paper_order_submission_disabled"}


@pytest.mark.parametrize("field,value", [
    ("day_open_equity", None),
    ("recorded_peak_equity", None),
    ("calendar_verified", False),
    ("journal_healthy", False),
    ("broker_reconciled", False),
    ("unresolved", [("hds-20261030-ABC-0123456789ab", "unknown", "ABC", 1)]),
    ("positions", [object()]),
    ("orders", [object()]),
])
def test_missing_or_conflicting_evidence_remains_blocked(field, value):
    state = build_safety_state(**inputs(**{field: value}))
    assert execution_blockers(state)


def test_lifetime_evidence_from_another_account_is_rejected_as_incomplete():
    evidence = dict(inputs()["lifetime_evidence"], account_id_sha256="f" * 64)
    state = build_safety_state(**inputs(lifetime_evidence=evidence))
    assert "external_cash_flows_unverified" in execution_blockers(state)
    assert "equity_history_unverified" in execution_blockers(state)


def test_untrusted_boolean_values_are_not_coerced_to_safe_states():
    with pytest.raises(TradeBlocked):
        build_safety_state(**inputs(control=SimpleNamespace(
            kill_switch_active="true", submissions_enabled="false")))


def test_missing_watchdog_lease_does_not_become_healthy():
    state = build_safety_state(**inputs(lease=SimpleNamespace(
        healthy=False, age_seconds=None)))
    assert "no_healthy_persistent_watchdog" in execution_blockers(state)


def test_builder_rejects_malformed_lifetime_values():
    evidence = dict(inputs()["lifetime_evidence"], equity="NaN")
    with pytest.raises(ValueError):
        build_safety_state(**inputs(lifetime_evidence=evidence))


def test_dispatch_cli_is_hard_disabled_before_reading_or_connecting(capsys):
    assert main(["--dispatch-file", "does-not-exist.json"]) == 2
    output = capsys.readouterr()
    assert "PAPER broker submissions NOT RELEASED" in output.err
    assert "no broker order sent" in output.err


def test_dispatch_helper_is_hard_disabled_before_using_components(monkeypatch):
    monkeypatch.setattr(
        "bot.paper_supervised_execution.PAPER_ORDER_SUBMISSIONS_RELEASED", False)
    with pytest.raises(TradeBlocked, match="NOT RELEASED"):
        dispatch_approved_artifact(components=None, artifact=None)


def test_prepare_cli_reports_safe_fail_closed_reason_without_exposing_exception(monkeypatch, capsys, tmp_path):
    from bot import paper_executor_service as service

    def blocked(**_kwargs):
        raise TradeBlocked("Independent supervisor lease is not healthy")

    monkeypatch.setattr(service, "run_once", blocked)
    assert main(["--prepare", "--output", str(tmp_path / "receipt.json")]) == 2
    output = capsys.readouterr()
    assert "Independent supervisor lease is not healthy" in output.err
    assert "no broker order sent" in output.err
    assert not (tmp_path / "receipt.json").exists()


def test_runtime_failure_receipt_identifies_stage_and_type_without_exception_text(
        monkeypatch, capsys, tmp_path):
    from bot import paper_executor_service as service

    secret_marker = "postgresql://user:private-password@db.example"
    def fail(**_kwargs):
        raise RuntimeError(secret_marker)
    monkeypatch.setattr(service, "run_once", fail)
    receipt = tmp_path / "runtime.json"
    assert main(["--prepare", "--runtime-receipt", str(receipt)]) == 2
    record = __import__("json").loads(receipt.read_text())
    output = capsys.readouterr()
    assert record["status"] == "error"
    assert record["stage"] == "prepare_live_workflow"
    assert record["error_type"] == "RuntimeError"
    assert record["broker_order_attempted"] is False
    assert secret_marker not in receipt.read_text()
    assert secret_marker not in output.err


def test_runtime_failure_receipt_records_safe_trade_block_reason(monkeypatch, tmp_path):
    from bot import paper_executor_service as service

    def fail(**_kwargs):
        raise TradeBlocked("Independent supervisor lease is not healthy")
    monkeypatch.setattr(service, "run_once", fail)
    receipt = tmp_path / "runtime.json"
    assert main(["--prepare", "--runtime-receipt", str(receipt)]) == 2
    record = __import__("json").loads(receipt.read_text())
    assert record["status"] == "blocked"
    assert record["error_type"] == "TradeBlocked"
    assert record["safe_reason"] == "Independent supervisor lease is not healthy"
    assert record["exit_code"] == 2


def test_runtime_failure_receipt_persists_individual_supervisor_checks(
        monkeypatch, tmp_path):
    from bot import paper_executor_service as service

    def fail(*, check_observer, **_kwargs):
        check_observer("cloudflare_external_ack", {
            "status": "FAIL", "reason": "ack_not_recorded", "age_seconds": None})
        check_observer("neon_supervisor_lease", {
            "status": "PASS", "reason": None, "age_seconds": 4})
        raise TradeBlocked("Cloudflare external heartbeat was not acknowledged")
    monkeypatch.setattr(service, "run_once", fail)
    receipt = tmp_path / "runtime.json"
    assert main(["--prepare", "--runtime-receipt", str(receipt)]) == 2
    record = __import__("json").loads(receipt.read_text())
    assert record["checks"]["cloudflare_external_ack"]["status"] == "FAIL"
    assert record["checks"]["neon_supervisor_lease"]["status"] == "PASS"


def test_closed_flat_supervisor_is_reported_as_idle_not_stale(tmp_path):
    import json
    from bot.paper_executor_service import _verify_supervisor_receipt

    now = datetime(2026, 11, 5, 5, 24, tzinfo=timezone.utc)
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({
        "status": "closed_flat_idle", "external_ack": True,
        "observed_at_utc": now.isoformat(), "worker_id": "paper-test", "pid": 1234,
    }))
    with pytest.raises(TradeBlocked, match="closed/flat idle; no active trading lease"):
        _verify_supervisor_receipt(path, object(), now)


def test_supervisor_verification_reports_receipt_ack_and_neon_lease_separately(tmp_path):
    import json
    from bot.paper_executor_service import _verify_supervisor_receipt

    now = datetime(2026, 11, 5, 14, 46, tzinfo=timezone.utc)
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({
        "status": "read_only_poll_ok", "external_ack": False,
        "observed_at_utc": now.isoformat(), "worker_id": "paper-test", "pid": 1234,
    }))
    class Heartbeat:
        def inspect(self, *, expected_worker_id):
            assert expected_worker_id == "paper-test"
            return SimpleNamespace(healthy=True, age_seconds=3, blockers=())
    checks = {}
    with pytest.raises(TradeBlocked, match="Cloudflare external heartbeat was not acknowledged"):
        _verify_supervisor_receipt(
            path, Heartbeat(), now,
            expected_pid=1234,
            check_observer=lambda name, result: checks.update({name: result}))
    assert checks["local_supervisor_receipt"]["status"] == "PASS"
    assert checks["supervisor_poll_status"]["status"] == "PASS"
    assert checks["cloudflare_external_ack"]["status"] == "FAIL"
    assert checks["neon_supervisor_lease"]["status"] == "PASS"


def test_supervisor_receipt_newer_than_preflight_start_passes_when_checked_at_current_time(tmp_path):
    import json
    from bot.paper_executor_service import _verify_supervisor_receipt

    started = datetime(2026, 11, 5, 14, 46, tzinfo=timezone.utc)
    observed = started + timedelta(seconds=35)
    checked = observed + timedelta(seconds=2)
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({
        "status": "read_only_poll_ok", "external_ack": True,
        "observed_at_utc": observed.isoformat(), "worker_id": "paper-test", "pid": 1234,
    }))
    class Heartbeat:
        def inspect(self, *, expected_worker_id):
            return SimpleNamespace(healthy=True, age_seconds=4, blockers=())
    checks = {}
    lease = _verify_supervisor_receipt(
        path, Heartbeat(), checked,
        expected_pid=1234,
        check_observer=lambda name, result: checks.update({name: result}))
    assert lease.healthy is True
    assert checks["local_receipt_freshness"]["status"] == "PASS"
    assert checks["local_receipt_freshness"]["age_seconds"] == 2


def test_live_coordinator_prepares_exact_proposal_without_any_broker_post(monkeypatch):
    from bot import paper_accounting, paper_executor_service as service
    from bot.paper_broker_transport import BoundedPaperSession
    from bot.paper_safety import PAPER_ENDPOINT

    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    today, prior = date(2026, 11, 2), date(2026, 10, 30)
    account = SimpleNamespace(
        id=ACCOUNT_ID, equity="500", cash="500", buying_power="500",
        trading_blocked=False, currency="USD",
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    sessions = [
        SimpleNamespace(date=prior),
        SimpleNamespace(date=today,
                        open=datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc),
                        close=datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)),
    ]

    class Trading:
        _base_url = PAPER_ENDPOINT
        _retry = 0
        _session = BoundedPaperSession()
        sent = []
        pending = []

        def get_account(self): return account
        def get_clock(self):
            return SimpleNamespace(is_open=True,
                                   next_close=datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc))
        def get_all_positions(self): return []
        def get_orders(self, *, filter): return []
        def get_calendar(self, request):
            if request.start == today and request.end == today:
                return [sessions[1]]
            if request.end < today:
                return [sessions[0]]
            return sessions
        def get_order_by_client_id(self, client_id):
            error = SimpleNamespace(response=SimpleNamespace(status_code=404))
            raise APIError("not found", http_error=error)
        def submit_order(self, *, order_data):
            self.sent.append(order_data)
            return SimpleNamespace(
                id="mock-paper-order", client_order_id=order_data.client_order_id,
                side="buy")

    class Data:
        def get_stock_latest_quote(self, request):
            return {"F": SimpleNamespace(
                timestamp=now - timedelta(seconds=2), bid_price=1.00,
                ask_price=1.004, bid_size=20, ask_size=20)}

    class Monitor:
        kill = True
        enabled = False
        pending = []
        def assert_monitor_only(self): pass
        def read_control(self):
            return SimpleNamespace(kill_switch_active=self.kill,
                                   submissions_enabled=self.enabled)
        def read_unresolved_intents(self, *, limit): return list(self.pending)
        def read_equity_references(self, start, as_of): return (500, 500)

    class Executor:
        reserved = []
        def assert_executor_permissions(self, *, reservation):
            assert reservation is True
        def reserve_if_enabled(self, plan):
            self.reserved.append(plan)
            components["monitor"].pending = [(
                plan.client_order_id, "reserved", plan.symbol, plan.whole_shares)]
            return True

        @contextmanager
        def authorize_reserved_submission(self, plan, *, request_sha256,
                                         attempt_id, account_id_sha256):
            assert self.approval_saved is True
            assert request_sha256 == result["prepared"]["request_sha256"]
            assert account_id_sha256 == ACCOUNT_HASH
            yield lambda: None

    class Reconciler:
        def assert_reconciler_permissions(self): pass
        def read_pending_plans(self): return []

    class Heartbeat:
        pass

    fresh_lease_times = []
    fresh_now = now + timedelta(seconds=5)
    def verify_fresh_lease(*args, **_kwargs):
        fresh_lease_times.append(args[2])
        return SimpleNamespace(healthy=True, age_seconds=3)
    monkeypatch.setattr(service, "_verify_supervisor_receipt", verify_fresh_lease)
    monkeypatch.setattr(paper_accounting, "fetch_account_history",
                        lambda trading: (account, [], {}, True))
    monkeypatch.setattr(paper_accounting, "reconcile_cash_lifetime",
                        lambda **_kw: {
                            "history_scope": "authenticated cash-only account lifetime",
                            "equity_history_explained": True,
                            "cashflows_reconciled": True, "can_submit_orders": False,
                            "account_id_sha256": ACCOUNT_HASH,
                            "equity": "500", "cash": "500",
                        })
    import bot.paper_readiness_cli as readiness
    monkeypatch.setattr(readiness, "research_candidates", lambda *a, **kw: [{
        "symbol": "F", "momentum_score": 0.5,
        "prior_completed_bar_date": prior.isoformat(), "reference_close": 1.0,
    }])
    trading, executor = Trading(), Executor()
    components = {
        "trading": trading, "market_data": Data(), "monitor": Monitor(),
        "executor": executor, "reconciler": Reconciler(),
        "reconciler_trading": Trading(), "heartbeat": Heartbeat(),
        "expected_account_hash": ACCOUNT_HASH, "receipt_path": Path("unused.json"),
        "expected_supervisor_pid": 1234,
    }
    persisted = []
    result = service.prepare_live_candidate(
        components=components, now=now, symbols=("F",), utcnow=lambda: fresh_now,
        prepared_observer=lambda prepared: persisted.append(prepared.to_dict()))
    assert result["mode"] == "PREPARED_FOR_EXACT_OWNER_REVIEW"
    assert result["prepared"]["symbol"] == "F"
    assert result["prepared"]["time_in_force"] == "gtc"
    assert result["prepared"]["order_class"] == "oto"
    assert result["prepared"]["purchase_cost_cap"] <= 5.00
    assert result["prepared"]["fee_reserve"] == 0.50
    assert result["owner_approval_sql"].startswith("-- OWNER ACTION:")
    from bot.paper_pilot_approval_cli import approval_sql_from_document
    recovery_sql = approval_sql_from_document({
        "mode": "PREPARATION_PENDING_RESERVATION",
        "prepared": result["prepared"],
    })
    assert recovery_sql.startswith("-- RECOVERY ARTIFACT:")
    assert "SELECT public.paper_approve_bounded_pilot(" in recovery_sql
    assert result["broker_orders"] == 0
    assert result["kill_switch_active"] is True
    assert result["submissions_enabled"] is False
    assert len(executor.reserved) == 1
    assert persisted[0]["client_order_id"] == result["prepared"]["client_order_id"]
    assert trading.sent == []
    assert fresh_lease_times and all(value == fresh_now for value in fresh_lease_times)

    # Synthetic dispatch only: simulate the separately enabled production
    # controls and owner approval, while keeping the real code gate OFF.
    from bot import paper_supervised_execution as gateway
    monkeypatch.setattr(gateway, "PAPER_ORDER_SUBMISSIONS_RELEASED", True)
    components["monitor"].kill = False
    components["monitor"].enabled = True
    executor.approval_saved = True
    submitted = service.dispatch_approved_artifact(
        components=components, artifact=result, now=now, utcnow=lambda: now)
    assert submitted.result == "broker_acknowledged_unreconciled_stop_not_proven"
    assert submitted.submitted_to_paper_broker is True
    assert len(trading.sent) == 1
    assert trading.sent[0].qty == result["prepared"]["quantity"]
    assert trading.sent[0].stop_loss.stop_price == result["prepared"]["stop_price"]
