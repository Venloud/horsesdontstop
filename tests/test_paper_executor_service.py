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

    monkeypatch.setattr(service, "_verify_supervisor_receipt", lambda *_a: SimpleNamespace(
        healthy=True, age_seconds=3))
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
    }
    persisted = []
    result = service.prepare_live_candidate(
        components=components, now=now, symbols=("F",), utcnow=lambda: now,
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
