"""Successive scheduled executor runs vs an unapproved PAPER reservation.

Isolated fakes only: no broker POST, no live Neon. Models the 2026-11-09
incident: a 09:36 proposal was never approved and blocked later runs.
"""
from datetime import date, datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot import paper_accounting, paper_executor_service as service
from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked, plan_paper_entry


ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
ACCOUNT_HASH = sha256(ACCOUNT_ID.encode()).hexdigest()
NOW = datetime(2026, 11, 9, 14, 46, tzinfo=timezone.utc)
PLAN = plan_paper_entry("F", date(2026, 11, 9), 25, 500, 500, .01)


class ReachedEvaluation(Exception):
    """Sentinel: the run got past every reservation gate."""


class Store:
    """Shared durable state across scheduled runs (stands in for Neon)."""
    def __init__(self, status="reserved", expiry_eligible=False):
        self.status = status
        self.expiry_eligible = expiry_eligible
        self.audit = []


class Broker:
    _base_url = PAPER_ENDPOINT

    def __init__(self, *, order_exists=False, positions=()):
        self.order_exists = order_exists
        self.positions = list(positions)
        self.writes = []

    def get_account(self):
        return SimpleNamespace(id=ACCOUNT_ID, equity="500", cash="500",
                               buying_power="500", trading_blocked=False)
    def get_clock(self): return SimpleNamespace(is_open=True)
    def get_all_positions(self): return list(self.positions)
    def get_orders(self, *, filter): return []
    def get_order_by_client_id(self, cid):
        if self.order_exists:
            return SimpleNamespace(id="broker-id", client_order_id=cid)
        raise APIError("not found", http_error=SimpleNamespace(
            response=SimpleNamespace(status_code=404)))
    def submit_order(self, *a, **kw): self.writes.append("submit")
    def cancel_order_by_id(self, *a, **kw): self.writes.append("cancel")


class Monitor:
    def __init__(self, store): self.store = store
    def assert_monitor_only(self): pass
    def read_control(self):
        return SimpleNamespace(kill_switch_active=True, submissions_enabled=False)
    def read_unresolved_intents(self, *, limit):
        if self.store.status in ("reserved", "submitted", "accepted", "unknown"):
            return [(PLAN.client_order_id, self.store.status, PLAN.symbol,
                     PLAN.whole_shares)]
        return []


class Executor:
    def assert_executor_permissions(self, *, reservation): pass


class Reconciler:
    def __init__(self, store): self.store = store
    def assert_reconciler_permissions(self): pass
    def read_pending_plans(self):
        return [PLAN] if self.store.status in (
            "reserved", "submitted", "accepted", "unknown") else []
    def get_durable_intent_terms(self, cid):
        return (self.store.status, None, PLAN.strategy, PLAN.symbol,
                date.fromisoformat(PLAN.trading_day), PLAN.whole_shares,
                PLAN.estimated_entry, PLAN.protective_stop, PLAN.time_in_force)
    def expire_unapproved_reservation(self, cid):
        assert self.store.status == "reserved"
        if not self.store.expiry_eligible:
            return False
        self.store.status = "expired"
        self.store.audit.append(("reservation_expired_unapproved", cid))
        return True
    def record_broker_verified_state(self, *a):
        raise AssertionError("broker-status writes must stay unreleased")


def components(store, broker=None):
    broker = broker or Broker()
    return {
        "trading": broker, "market_data": object(), "monitor": Monitor(store),
        "executor": Executor(), "reconciler": Reconciler(store),
        "reconciler_trading": broker, "heartbeat": object(),
        "expected_account_hash": ACCOUNT_HASH,
        "receipt_path": None, "expected_supervisor_pid": 1,
    }


@pytest.fixture(autouse=True)
def stop_after_gates(monkeypatch):
    def reached(_trading):
        raise ReachedEvaluation()
    monkeypatch.setattr(paper_accounting, "fetch_account_history", reached)


def run(store, broker=None):
    return service.prepare_live_candidate(
        components=components(store, broker), now=NOW, utcnow=lambda: NOW)


def test_young_unapproved_reservation_reports_awaiting_approval_not_error():
    store = Store(expiry_eligible=False)
    result = run(store)
    assert result["mode"] == "AWAITING_OWNER_APPROVAL"
    assert result["pending_client_order_ids"] == [PLAN.client_order_id]
    assert result["broker_orders"] == 0 and store.status == "reserved"


def test_successive_runs_expire_stale_proposal_then_evaluate_new_opportunities():
    store, broker = Store(expiry_eligible=False), Broker()
    assert run(store, broker)["mode"] == "AWAITING_OWNER_APPROVAL"   # 09:41
    store.expiry_eligible = True                                      # >= 10 min
    with pytest.raises(ReachedEvaluation):                            # 09:46
        run(store, broker)
    assert store.status == "expired"
    assert store.audit == [("reservation_expired_unapproved", PLAN.client_order_id)]
    with pytest.raises(ReachedEvaluation):                            # restart / next day
        run(store, broker)
    assert len(store.audit) == 1 and broker.writes == []


def test_reservation_with_real_broker_order_is_never_expired():
    store = Store(expiry_eligible=True)
    with pytest.raises(TradeBlocked):
        run(store, Broker(order_exists=True))
    assert store.status == "reserved" and store.audit == []


def test_dispatched_or_ambiguous_intents_still_block():
    for status in ("unknown", "submitted", "accepted"):
        store = Store(status=status, expiry_eligible=True)
        with pytest.raises(TradeBlocked):
            run(store)
        assert store.status == status and store.audit == []


def test_existing_exposure_still_blocks_even_after_expiry():
    store = Store(expiry_eligible=True)
    broker = Broker(positions=[SimpleNamespace(symbol="XOM", qty="1")])
    with pytest.raises(TradeBlocked):
        run(store, broker)
    assert store.audit == []
