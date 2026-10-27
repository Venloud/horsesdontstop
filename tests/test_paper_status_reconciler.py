"""Isolated broker/DB fakes: NO broker POST and NO live Neon credentials.

The runtime and SQL privilege rollout are still NOT deployed or authorized.
"""
from datetime import date
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
