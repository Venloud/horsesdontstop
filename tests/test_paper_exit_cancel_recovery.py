"""Incident 2026-11-20: the stop-leg cancel confirmed ~20 s after the exit run
stopped waiting, leaving WMT held with no stop and invisible to later runs.
Recovery: the newest filled intent with a canceled stop leg is still managed;
the next run sells it if an exit is due, otherwise re-protects. Fakes only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

import bot.paper_reconcile as reconcile
from bot.paper_exit_manager import (
    ExitAction, bot_positions, execute_exit, plan_exits,
)
from bot.paper_exit_service import manage_once

OLD_CID, NEW_CID = "hds-20261110-WMT-e9ac0958d6bb", "hds-20261110-WMT-507c60f35c72"
OLD_PID, NEW_PID = "old-parent", "new-parent"
OLD_LEG, NEW_LEG = "old-leg", "new-leg"
STOP = Decimal("122.23")
APPROVED = [(OLD_CID, "filled", "WMT", 1, Decimal("121.67"), OLD_PID, "gtc"),
            (NEW_CID, "filled", "WMT", 1, STOP, NEW_PID, "gtc")]
OLD_FILL = datetime(2026, 11, 10, 16, 10, tzinfo=timezone.utc)
NEW_FILL = datetime(2026, 11, 10, 17, 11, tzinfo=timezone.utc)
FRI_OPEN = datetime(2026, 11, 20, 14, 36, tzinfo=timezone.utc)


def nf():
    return APIError("nf", http_error=SimpleNamespace(response=SimpleNamespace(status_code=404)))


def leg(lid, status):
    return SimpleNamespace(id=lid, side="sell", type="stop", status=status, symbol="WMT",
                           qty="1", legs=[], client_order_id=None)


def parent(pid, cid, fill, leg_status, lid, filled_qty="1"):
    return SimpleNamespace(id=pid, client_order_id=cid, symbol="WMT", side="buy", type="limit",
                           order_class="oto", status="filled", qty="1", filled_qty=filled_qty,
                           legs=[leg(lid, leg_status)], filled_at=fill, filled_avg_price="126.20")


def orders(new_leg="canceled", filled_qty="1"):
    # Old trade: closed earlier (its stop canceled, shares sold). New trade: held.
    return [parent(OLD_PID, OLD_CID, OLD_FILL, "canceled", OLD_LEG),
            parent(NEW_PID, NEW_CID, NEW_FILL, new_leg, NEW_LEG, filled_qty)]


POS = [SimpleNamespace(symbol="WMT", qty="1", current_price="129.34")]


def managed(ords):
    return [(cid, str(l.id)) for _, cid, _, _, l in
            bot_positions(positions=POS, orders=ords, approved_stops=APPROVED)]


def test_newest_intent_with_canceled_stop_is_still_managed():
    assert managed(orders()) == [(NEW_CID, NEW_LEG)]


def test_live_stop_wins_and_old_history_is_ignored():
    assert managed(orders(new_leg="new")) == [(NEW_CID, NEW_LEG)]


def test_partial_mismatch_is_not_recovered():
    assert managed(orders(filled_qty="2")) == []


def test_overdue_unprotected_position_plans_the_time_limit_exit():
    acts = plan_exits(positions=POS, orders=orders(), approved_stops=APPROVED, now=FRI_OPEN,
                      market={"WMT": {"bid": "129.34", "high_water": "129.81",
                                      "sessions_held": 9}}, market_open=True)
    assert [(a.reason, a.stop_leg_id) for a in acts] == [("time_limit", NEW_LEG)]


class Broker:
    """Order-level fake for execute_exit."""
    def __init__(self, leg_status="new", cancel_lands_after=0):
        self.leg_status, self.cancel_lands_after = leg_status, cancel_lands_after
        self.calls, self.polls, self.exit = [], 0, None

    def get_order_by_client_id(self, cid):
        if cid.endswith("-x") and self.exit is not None:
            return self.exit
        raise nf()

    def get_order_by_id(self, oid):
        if any(c[0] == "cancel" for c in self.calls):
            self.polls += 1
            if self.polls > self.cancel_lands_after:
                self.leg_status = "canceled"
            else:
                self.leg_status = "pending_cancel"
        return SimpleNamespace(id=oid, status=self.leg_status)

    def cancel_order_by_id(self, oid):
        self.calls.append(("cancel", oid))

    def get_all_positions(self):
        return [SimpleNamespace(symbol="WMT", qty="1")]

    def submit_order(self, *, order_data):
        self.calls.append(("submit", order_data.client_order_id))
        if order_data.client_order_id.endswith("-x"):
            self.exit = SimpleNamespace(status="filled", filled_avg_price="129.30")


ACTION = ExitAction(client_order_id=NEW_CID, symbol="WMT", shares=Decimal(1),
                    stop_leg_id=NEW_LEG, durable_stop=STOP, reason="time_limit")


def test_already_canceled_leg_sells_without_cancelling_again():
    b = Broker(leg_status="canceled")
    out = execute_exit(b, ACTION, released=True, sleeper=lambda _: None, polls=2)
    assert out["result"] == "exit_filled"
    assert b.calls == [("submit", NEW_CID + "-x")]


def test_slow_cancel_confirmation_is_waited_for():
    # Alpaca took ~20 s on 2026-11-20; a 20-poll delay must still complete.
    b = Broker(cancel_lands_after=20)
    out = execute_exit(b, ACTION, released=True, sleeper=lambda _: None, polls=10)
    assert out["result"] == "exit_filled"


# ---- service: sell when due, otherwise re-protect --------------------------

class Trading(Broker):
    def __init__(self, *, is_open=True, leg_status="canceled"):
        super().__init__(leg_status=leg_status)
        self.is_open = is_open

    def get_clock(self):
        return SimpleNamespace(is_open=self.is_open)

    def get_orders(self, filter=None):
        return orders(new_leg=self.leg_status)

    def get_calendar(self, req):
        from datetime import date
        days = [date(2026, 11, d) for d in (10, 11, 12, 13, 16, 17, 18, 19, 20)]
        return [d for d in days if req.start <= d <= req.end]

    def get_all_positions(self):
        return POS


class Data:
    def get_stock_latest_trade(self, req):
        return {"WMT": SimpleNamespace(price="129.34")}

    def get_stock_latest_quote(self, req):
        return {"WMT": SimpleNamespace(bid_price="129.30")}

    def get_stock_bars(self, req):
        return SimpleNamespace(data={"WMT": [SimpleNamespace(high="129.81")]})


class Monitor:
    def read_approved_stop_intents(self, limit):
        return APPROVED


@pytest.fixture(autouse=True)
def no_parent_fetch(monkeypatch):
    monkeypatch.setattr(reconcile, "orders_with_recent_parents",
                        lambda trading, orders, now: (orders, None))


def service(t, released=True):
    return manage_once(trading=t, market_data=Data(), monitor=Monitor(), now=FRI_OPEN,
                       released=released)


def test_service_sells_overdue_unprotected_position_and_does_not_restop():
    t = Trading()
    rec = service(t)
    assert rec["positions"][0]["protected"] is False
    assert [e["result"] for e in rec["exits"]] == ["exit_filled"]
    assert t.calls == [("submit", NEW_CID + "-x")]


def test_service_reprotects_when_market_closed():
    t = Trading(is_open=False)
    rec = service(t)
    assert [e["result"] for e in rec["exits"]] == ["reprotected"]
    assert t.calls == [("submit", NEW_CID + "-s")] and rec["status"] == "blocked"


def test_dry_run_reports_unprotected_without_writing():
    t = Trading(is_open=False)
    rec = service(t, released=False)
    assert rec["status"] == "blocked" and t.calls == []


def test_protected_position_needs_no_reprotect():
    t = Trading(is_open=False, leg_status="new")
    rec = service(t)
    assert rec["exits"] == [] and rec["status"] == "ok" and t.calls == []
