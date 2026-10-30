"""Isolated broker/DB fakes: NO broker POST and NO live Neon credentials.

The runtime and SQL privilege rollout are still NOT deployed or authorized.
"""
from datetime import date
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked, plan_paper_entry
from bot.paper_status_reconciler import reconcile_verified_paper_order


PLAN = plan_paper_entry("F", date(2026, 10, 12), 25, 500, 500, .01)


def parent(*, status="new", filled=0, legs=None, broker_id="broker-paper-id"):
    return SimpleNamespace(
        id=broker_id, client_order_id=PLAN.client_order_id,
        symbol=PLAN.symbol, side="buy", order_class="oto",
        type="limit", time_in_force="day", status=status,
        qty=str(PLAN.whole_shares), filled_qty=str(filled),
        limit_price=str(PLAN.estimated_entry),
        legs=legs if legs is not None else [],
    )


def stop(*, status="new", tif="gtc"):
    return SimpleNamespace(
        id="broker-stop-id", parent_order_id="broker-paper-id",
        symbol=PLAN.symbol, side="sell", type="stop",
        time_in_force=tif, status=status,
        qty=str(PLAN.whole_shares), filled_qty="0",
        stop_price=str(PLAN.protective_stop),
    )


class FakeBroker:
    _base_url = PAPER_ENDPOINT

    def __init__(self, p=None, exposure=0):
        self.parent = p if p is not None else parent()
        self.exposure = exposure
        self.calls = []

    def get_all_positions(self):
        self.calls.append("positions")
        if self.exposure == 0:
            return []
        return [SimpleNamespace(symbol=PLAN.symbol, qty=str(self.exposure))]

    def get_order_by_client_id(self, cid):
        self.calls.append("lookup")
        assert cid == PLAN.client_order_id
        return SimpleNamespace(id=self.parent.id, client_order_id=cid)

    def get_order_by_id(self, pid, *, filter):
        self.calls.append("nested")
        assert pid == self.parent.id
        assert filter.nested is True
        return self.parent

    def submit_order(self, *_a, **_kw):
        raise AssertionError("Reconciliation must never submit an order")

    def cancel_order(self, *_a, **_kw):
        raise AssertionError("Reconciliation must never cancel an order")


class FakeJournal:
    def __init__(self, status="reserved", broker_id=None, permitted=True):
        self.status = status
        self.broker_id = broker_id
        self.permitted = permitted
        self.calls = []
        self.modified = []

    def assert_reconciler_permissions(self):
        self.calls.append("attest")
        if not self.permitted:
            raise TradeBlocked("Unsafe or incomplete PAPER broker-reconciler role")

    def get_durable_intent_terms(self, cid):
        self.calls.append("read_intent")
        assert cid == PLAN.client_order_id
        return (
            self.status, self.broker_id, PLAN.strategy,
            PLAN.symbol, date.fromisoformat(PLAN.trading_day),
            PLAN.whole_shares, PLAN.estimated_entry, PLAN.protective_stop,
        )

    def record_broker_verified_state(self, cid, old, new, broker_id):
        self.calls.append("atomic_transition")
        self.modified.append((cid, old, new, broker_id))


def run(*, broker=None, journal=None):
    broker = broker if broker is not None else FakeBroker()
    journal = journal if journal is not None else FakeJournal()
    result = reconcile_verified_paper_order(
        trading=broker, journal=journal, plan=PLAN)
    return result, broker, journal


def test_unfilled_new_broker_parent_only_transitions_to_submitted():
    state, broker, journal = run()
    assert state == "submitted"
    assert journal.modified == [
        (PLAN.client_order_id, "reserved", "submitted", "broker-paper-id")]
    assert journal.calls == ["attest", "read_intent", "atomic_transition"]
    assert broker.calls == ["positions", "lookup", "nested"]


def test_accepted_broker_order_moves_submitted_to_accepted():
    state, _, journal = run(
        broker=FakeBroker(parent(status="accepted")),
        journal=FakeJournal(status="submitted", broker_id="broker-paper-id"))
    assert state == "accepted"
    assert journal.modified[0][2] == "accepted"


def test_filled_broker_entry_requires_live_matching_gtc_protection():
    state, _, journal = run(
        broker=FakeBroker(parent(
            status="filled", filled=PLAN.whole_shares, legs=[stop()]),
            exposure=PLAN.whole_shares),
        journal=FakeJournal(status="accepted", broker_id="broker-paper-id"))
    assert state == "filled"
    assert journal.modified[0][2] == "filled"


@pytest.mark.parametrize("leg", [None, stop(tif="day"),
                                   stop(status="canceled"),
                                   stop(status="rejected")])
def test_missing_or_unreliable_stop_never_promotes_fill(leg):
    journal = FakeJournal(status="submitted")
    broker = FakeBroker(parent(
        status="filled", filled=PLAN.whole_shares,
        legs=[] if leg is None else [leg]),
        exposure=PLAN.whole_shares)
    with pytest.raises(TradeBlocked):
        run(broker=broker, journal=journal)
    assert journal.modified == []


@pytest.mark.parametrize('status,filled', [('partially_filled',1),('filled',PLAN.whole_shares)])
def test_held_oto_child_is_not_live_protection_even_with_matching_quantity(status,filled):
    leg=stop(status='held')
    leg.qty=str(filled)
    journal=FakeJournal(status='submitted')
    broker=FakeBroker(parent(status=status,filled=filled,legs=[leg]),exposure=filled)
    with pytest.raises(TradeBlocked):run(broker=broker,journal=journal)
    assert journal.modified==[]


@pytest.mark.parametrize("status,filled,exposure", [
    ("partially_filled", 1, 1),
    ("filled", PLAN.whole_shares, 0),
    ("canceled", 1, 1),
    ("new", 1, 1),
    ("expired", 0, 0),
])
def test_unresolved_broker_state_cannot_write_journal(status, filled, exposure):
    journal = FakeJournal(status="reserved")
    broker = FakeBroker(parent(status=status, filled=filled), exposure=exposure)
    with pytest.raises(TradeBlocked):
        run(broker=broker, journal=journal)
    assert journal.modified == []


def test_terminal_journal_status_is_immutable():
    journal = FakeJournal(status="filled")
    broker = FakeBroker()
    with pytest.raises(TradeBlocked, match="Terminal"):
        run(broker=broker, journal=journal)
    assert broker.calls == [] and journal.modified == []


def test_unrelated_existing_broker_id_is_rejected():
    journal = FakeJournal(status="submitted", broker_id="different-order")
    broker = FakeBroker(parent(status="accepted"))
    with pytest.raises(TradeBlocked, match="conflicts"):
        run(broker=broker, journal=journal)
    assert journal.modified == []


def test_data_mismatch_aborts_before_any_broker_get():
    journal = FakeJournal()
    journal.get_durable_intent_terms = lambda _cid: (
        "reserved", None, PLAN.strategy, PLAN.symbol,
        date.fromisoformat(PLAN.trading_day), PLAN.whole_shares + 1,
        PLAN.estimated_entry, PLAN.protective_stop,
    )
    broker = FakeBroker()
    with pytest.raises(TradeBlocked, match="mismatch"):
        run(broker=broker, journal=journal)
    assert broker.calls == [] and journal.modified == []


def test_reconciler_role_refused_before_broker_or_journal_state_lookup():
    journal = FakeJournal(permitted=False)
    broker = FakeBroker()
    with pytest.raises(TradeBlocked, match="role"):
        run(broker=broker, journal=journal)
    assert journal.calls == ["attest"]
    assert broker.calls == [] and journal.modified == []


def test_live_money_endpoint_rejected_before_any_access():
    broker = FakeBroker()
    broker._base_url = "https://api.alpaca.markets"
    journal = FakeJournal()
    with pytest.raises(TradeBlocked, match="PAPER"):
        run(broker=broker, journal=journal)
    assert broker.calls == [] and journal.calls == []


def test_unverifiable_broker_get_never_commits():
    broker = FakeBroker()
    broker.get_all_positions = lambda: (_ for _ in ()).throw(
        RuntimeError("SENSITIVE_TOKEN"))
    journal = FakeJournal()
    with pytest.raises(TradeBlocked) as err:
        run(broker=broker, journal=journal)
    assert "SENSITIVE_TOKEN" not in str(err.value)
    assert journal.modified == []


def test_unknown_journal_can_reconcile_new_broker_order_without_resend():
    journal = FakeJournal(status="unknown")
    state, broker, journal = run(
        journal=journal, broker=FakeBroker(parent(status="new")))
    assert state == "submitted"
    assert journal.modified[0][1:3] == ("unknown", "submitted")
    assert broker.calls == ["positions", "lookup", "nested"]


def test_role_attestation_requires_separate_narrow_reconciler_grants(monkeypatch):
    from bot.paper_status_reconciler import PaperStatusReconcilerJournal

    class Cursor:
        def __init__(self, row):
            self.row = row
            self.query = None
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, sql):
            self.query = sql
        def fetchone(self):
            if "paper_acl_boundary" in self.query:
                return (True,)
            return self.row

    class Conn:
        def __init__(self, cursor):
            self.c = cursor
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def cursor(self):
            return self.c

    allowed = (True, False, False, False, False, False,
               False, False, True, True)
    j = PaperStatusReconcilerJournal("postgresql://fake.invalid/neondb")
    cursor = Cursor(allowed)
    monkeypatch.setattr(j, "_connect", lambda: Conn(cursor))
    j.assert_reconciler_permissions()
    assert "paper_acl_boundary" in cursor.query
    assert "paper_record_reconciled_intent_status" in cursor.query
    assert "paper_control" in cursor.query
    assert "paper_audit_events" in cursor.query
    assert "paper_intents" in cursor.query

    # Executor membership, direct status writes, missing function grant,
    # and owner/admin are all refused before any broker call.
    for idx in (1, 2, 3, 4, 5, 6, 7):
        current = list(allowed)
        current[idx] = True
        cursor.row = tuple(current)
        with pytest.raises(TradeBlocked, match="reconciler role"):
            j.assert_reconciler_permissions()
    for idx in (0, 8, 9):
        current = list(allowed)
        current[idx] = False
        cursor.row = tuple(current)
        with pytest.raises(TradeBlocked, match="reconciler role"):
            j.assert_reconciler_permissions()


def test_db_status_function_is_single_atomic_call(monkeypatch):
    from bot.paper_status_reconciler import PaperStatusReconcilerJournal

    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, args): calls.append((sql, args))
        def fetchone(self): return (True,)
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
    journal = PaperStatusReconcilerJournal("postgresql://fake.invalid/neondb")
    monkeypatch.setattr(journal, "_connect", lambda: Conn())
    journal.record_broker_verified_state("client-x", "submitted", "accepted", "broker-x")
    assert len(calls) == 1
    sql, args = calls[0]
    assert "SELECT public.paper_record_reconciled_intent_status(" in sql
    assert "UPDATE" not in sql and "INSERT" not in sql
    assert args == ("client-x", "submitted", "accepted", "broker-x")


@pytest.mark.parametrize("exposure", [1, PLAN.whole_shares - 1,
                                       PLAN.whole_shares + 1])
def test_unexplained_share_discrepancy_cannot_promote_journal(exposure):
    journal = FakeJournal(status="accepted")
    broker = FakeBroker(parent(status="filled", filled=PLAN.whole_shares,
                               legs=[stop()]), exposure=exposure)
    with pytest.raises(TradeBlocked):
        run(broker=broker, journal=journal)
    assert journal.modified == []


def test_independent_linked_stop_fill_can_explain_reduced_position():
    child = stop(status="partially_filled")
    child.filled_qty = str(PLAN.whole_shares - 1)
    state, broker, journal = run(broker=FakeBroker(
        parent(status="filled", filled=PLAN.whole_shares, legs=[child]),
        exposure=1), journal=FakeJournal(status="accepted"))
    assert state == "filled"
    assert len(journal.modified) == 1
    assert broker.calls == ["positions", "lookup", "nested"]


def test_exact_five_filled_one_held_unchanged_five_stop_regression(monkeypatch):
    monkeypatch.setattr(__import__(__name__), "PLAN", replace(PLAN, whole_shares=5))
    journal = FakeJournal(status="accepted")
    broker = FakeBroker(parent(status="filled", filled=5, legs=[stop()]), exposure=1)
    with pytest.raises(TradeBlocked):
        run(broker=broker, journal=journal)
    assert journal.modified == []


def test_repeated_verified_accepted_observation_does_not_rewrite_journal():
    journal = FakeJournal(status="accepted", broker_id="broker-paper-id")
    state, broker, journal = run(journal=journal, broker=FakeBroker(parent(status="accepted")))
    assert state == "accepted" and journal.modified == []
    assert broker.calls == ["positions", "lookup", "nested"]


def test_restart_uses_durable_intents_and_gets_only():
    from bot.paper_status_reconciler import reconcile_pending_once
    journal = FakeJournal(status="unknown")
    journal.read_pending_plans = lambda: [PLAN]
    broker = FakeBroker(parent(status="accepted"))
    broker.get_account = lambda: SimpleNamespace(trading_blocked=False)
    assert reconcile_pending_once(trading=broker, journal=journal) == ((PLAN.client_order_id, "accepted"),)
    assert journal.modified[0][1:3] == ("unknown", "accepted")
    assert broker.calls == ["positions", "lookup", "nested"]


@pytest.mark.parametrize("failure", ["unauthorized", "missing_account_evidence"])
def test_empty_queue_does_not_hide_broken_broker_authentication(failure):
    from bot.paper_reconciler_service import run_service
    broker = FakeBroker()
    def get_account():
        if failure == "unauthorized":
            raise RuntimeError("SENSITIVE_BROKER_CREDENTIAL")
        return SimpleNamespace()
    broker.get_account = get_account
    journal = FakeJournal()
    journal.read_pending_plans = lambda: []
    alerts = []
    with pytest.raises(TradeBlocked, match="unresolved") as exc:
        run_service(trading=broker, journal=journal,
                    alert=lambda: alerts.append(True) or True)
    assert alerts == [True] and journal.modified == []
    assert "SENSITIVE" not in str(exc.value)


def test_empty_queue_verifies_broker_authentication_on_every_tick():
    from bot.paper_reconciler_service import run_service
    broker = FakeBroker()
    calls = []
    broker.get_account = lambda: calls.append("account") or SimpleNamespace(trading_blocked=True)
    journal = FakeJournal()
    journal.read_pending_plans = lambda: []
    assert run_service(trading=broker, journal=journal, max_ticks=2,
                       sleeper=lambda _: None) == 2
    assert calls == ["account", "account"] and journal.modified == []


def test_observe_only_reconciliation_cannot_record_status():
    journal = FakeJournal(status="unknown")
    broker = FakeBroker(parent(status="accepted"))
    state = reconcile_verified_paper_order(trading=broker, journal=journal, plan=PLAN, record=False)
    assert state == "accepted" and journal.modified == []
