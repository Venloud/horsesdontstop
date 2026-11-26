"""Stale entries, partial fills, missing stops, loss cutoffs. Fakes only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.paper_broker_recovery import exit_client_order_id, inspect_nested_oto
from bot.paper_position_manager import execute_maintenance, plan_maintenance
from bot.paper_safety import TradeBlocked, plan_paper_entry
from datetime import date

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)      # 10:00 NY
PID = "11111111-2222-3333-4444-555555555555"
CID = "hds-20261110-F-0123456789ab"


def order(oid=PID, *, status="accepted", filled=0, qty=2, legs=(), cid=CID,
          side="buy", kind="limit", klass="oto", submitted=NOW - timedelta(minutes=11),
          filled_at=None, stop_price=None, tif="gtc"):
    return SimpleNamespace(
        id=oid, client_order_id=cid, symbol="F", side=side, type=kind,
        order_class=klass, status=status, qty=str(qty), filled_qty=str(filled),
        time_in_force=tif, legs=list(legs), parent_order_id=None,
        submitted_at=submitted, filled_at=filled_at, stop_price=stop_price,
        limit_price="25", expires_at=None)


def stop_leg(status="held", qty=2):
    return order("99999999-2222-3333-4444-555555555555", status=status, qty=qty,
                 cid=None, side="sell", kind="stop", klass="simple", stop_price="24")


def plan(positions, orders, unresolved=((CID, "accepted", "F", 2),), approved=None, now=NOW):
    return plan_maintenance(positions=positions, orders=orders, unresolved=list(unresolved),
                            approved_stops=approved or [], now=now)


def test_fresh_unfilled_entry_is_left_alone():
    p = plan([], [order(submitted=NOW - timedelta(minutes=3), legs=(stop_leg(),))])
    assert p.actions == ()


def test_stale_unfilled_entry_is_canceled_by_single_order_id():
    p = plan([], [order(legs=(stop_leg(),))])
    assert [(a.kind, a.broker_order_id, a.reason) for a in p.actions] == [
        ("cancel_entry", PID, "stale_unfilled_entry")]


def test_any_open_entry_after_1545_ny_is_canceled():
    late = datetime(2026, 11, 10, 20, 46, tzinfo=timezone.utc)
    p = plan([], [order(submitted=late - timedelta(minutes=1), legs=(stop_leg(),))], now=late)
    assert p.actions[0].reason == "late_session_entry"


def test_partial_fill_cancels_first_then_flattens_after_replan():
    partial = order(status="partially_filled", filled=1, legs=(stop_leg(),))
    first = plan([SimpleNamespace(symbol="F", qty="1")], [partial])
    assert [a.kind for a in first.actions] == ["cancel_entry"]   # no flatten yet
    canceled = order(status="canceled", filled=1, legs=(stop_leg("canceled"),))
    # After cancel, the open-order list no longer contains the parent.
    second = plan([SimpleNamespace(symbol="F", qty="1")], [])
    assert [(a.kind, a.shares) for a in second.actions] == [("flatten", "1")]


def test_filled_position_missing_stop_is_flattened_after_grace_only():
    filled_recent = order(status="filled", filled=2, filled_at=NOW - timedelta(seconds=30),
                          legs=(stop_leg("canceled"),))
    approved = [(CID, "filled", "F", 2, 24, PID, "gtc")]
    pos = [SimpleNamespace(symbol="F", qty="2")]
    assert plan(pos, [filled_recent], (), approved).actions == ()
    filled_old = order(status="filled", filled=2, filled_at=NOW - timedelta(minutes=5),
                       legs=(stop_leg("canceled"),))
    acts = plan(pos, [filled_old], (), approved).actions
    assert [(a.kind, a.shares) for a in acts] == [("flatten", "2")]


def test_verified_stop_position_needs_no_action():
    leg = stop_leg("new")
    leg.parent_order_id = PID
    filled = order(status="filled", filled=2, filled_at=NOW - timedelta(hours=1), legs=(leg,))
    approved = [(CID, "filled", "F", 2, 24, PID, "gtc")]
    assert plan([SimpleNamespace(symbol="F", qty="2")], [filled], (), approved).actions == ()


def test_unexplained_position_is_never_touched():
    p = plan([SimpleNamespace(symbol="XOM", qty="3")], [], ())
    assert p.actions == () and p.unexplained_symbols == ("XOM",)


class Broker:
    def __init__(self, *, exit_exists=False, position_qty="1"):
        self.calls, self.exit_exists, self.position_qty = [], exit_exists, position_qty
    def cancel_order_by_id(self, oid): self.calls.append(("cancel", oid))
    def get_order_by_client_id(self, cid):
        self.calls.append(("lookup", cid))
        if self.exit_exists:
            return SimpleNamespace(id="x", client_order_id=cid)
        raise APIError("nf", http_error=SimpleNamespace(response=SimpleNamespace(status_code=404)))
    def get_all_positions(self): return [SimpleNamespace(symbol="F", qty=self.position_qty)]
    open_orders = ()
    def get_orders(self, *, filter): return list(self.open_orders)
    def submit_order(self, *, order_data): self.calls.append(("submit", order_data))


def test_maintenance_is_dry_run_while_release_flag_off():
    broker = Broker()
    p = plan([], [order(legs=(stop_leg(),))])
    assert execute_maintenance(broker, p, released=False) == (("dry_run", "cancel_entry", CID),)
    assert broker.calls == []


def test_flatten_is_one_market_sell_with_deterministic_exit_id():
    broker = Broker()
    p = plan([SimpleNamespace(symbol="F", qty="1")], [])
    done = execute_maintenance(broker, p, released=True, sleeper=lambda _: None)
    submits = [c[1] for c in broker.calls if c[0] == "submit"]
    assert done == (("flatten_submitted", "flatten", CID),) and len(submits) == 1
    o = submits[0]
    assert o.client_order_id == exit_client_order_id(CID)
    assert str(o.side.value) == "sell" and float(o.qty) == 1.0


def test_flatten_never_resubmits_an_existing_exit():
    broker = Broker(exit_exists=True)
    p = plan([SimpleNamespace(symbol="F", qty="1")], [])
    assert execute_maintenance(broker, p, released=True)[0][0] == "exit_already_exists"
    assert not any(c[0] == "submit" for c in broker.calls)


def test_flatten_refuses_when_position_changed():
    broker = Broker(position_qty="2")
    p = plan([SimpleNamespace(symbol="F", qty="1")], [])
    with pytest.raises(TradeBlocked, match="Position changed"):
        execute_maintenance(broker, p, released=True)


# ---- recovery after flatten ----

PLAN = plan_paper_entry("F", date(2026, 11, 10), 25, 500, 500, .01)


def nested_parent(status, filled):
    return SimpleNamespace(
        id=PID, client_order_id=PLAN.client_order_id, symbol="F", order_class="oto",
        side="buy", type="limit", time_in_force=PLAN.time_in_force, status=status,
        qty=str(PLAN.whole_shares), filled_qty=str(filled),
        limit_price=str(PLAN.estimated_entry), legs=[])


def exit_order(filled, *, kind="market", cid=None):
    return SimpleNamespace(client_order_id=cid or exit_client_order_id(PLAN.client_order_id),
                           symbol="F", side="sell", type=kind, status="filled",
                           filled_qty=str(filled))


def test_canceled_partial_flattened_by_bot_resolves_cleanly():
    audit = inspect_nested_oto(PLAN, nested_parent("canceled", 1), position_qty=0,
                               exit_order=exit_order(1))
    assert audit.blockers == () and audit.status == "canceled"


@pytest.mark.parametrize("exit_,qty", [
    (None, 0),                                  # flat with no explanation
    ("wrong_qty", 0), ("limit", 0), ("foreign", 0),
    ("ok", 1),                                  # still holding shares
])
def test_unverified_flatten_keeps_blocking(exit_, qty):
    exits = {None: None, "ok": exit_order(1), "wrong_qty": exit_order(2),
             "limit": exit_order(1, kind="limit"), "foreign": exit_order(1, cid="manual")}
    audit = inspect_nested_oto(PLAN, nested_parent("canceled", 1), position_qty=qty,
                               exit_order=exits[exit_])
    assert audit.blockers


# ---- transport ----

@pytest.mark.parametrize("method,path,ok", [
    ("DELETE", "/v2/orders/" + PID, True),
    ("DELETE", "/v2/orders", False),                 # bulk cancel
    ("DELETE", "/v2/positions", False),              # liquidate all
    ("DELETE", "/v2/positions/F", False),
    ("PATCH", "/v2/orders/" + PID, False),
    ("POST", "/v2/positions/F", False),
])
def test_transport_allows_only_single_order_cancel(monkeypatch, method, path, ok):
    import requests
    from bot.paper_broker_transport import BoundedPaperSession
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: "sent")
    session = BoundedPaperSession()
    if ok:
        assert session.request(method, "https://paper-api.alpaca.markets" + path) == "sent"
    else:
        with pytest.raises(TradeBlocked):
            session.request(method, "https://paper-api.alpaca.markets" + path)


def test_reconciler_transport_still_get_only():
    from bot.paper_broker_transport import make_reconciler_paper_client
    client = make_reconciler_paper_client("k", "s")
    with pytest.raises(TradeBlocked):
        client._session.request("DELETE", "https://paper-api.alpaca.markets/v2/orders/" + PID)


# ---- loss limits ----

def state(**kw):
    from bot.paper_safety import PaperSafetyState, PAPER_ENDPOINT
    base = dict(equity=500, available_cash=500, day_open_equity=500,
                recorded_peak_equity=500, open_positions=0, open_orders=0,
                market_open=True, trading_blocked=False, recent_quote_seconds=1,
                durable_journal_healthy=True, reconciled_with_broker=True,
                watchdog_heartbeat_seconds=1, kill_switch_active=False,
                order_submissions_explicitly_enabled=True, broker_endpoint=PAPER_ENDPOINT,
                audited_external_flows_complete=True, verified_equity_history_complete=True,
                exchange_calendar_verified=True)
    base.update(kw)
    return PaperSafetyState(**base)


def test_daily_loss_cutoff_at_three_percent():
    from bot.paper_safety import execution_blockers
    assert "daily_three_percent_loss_cutoff" not in execution_blockers(state(equity=486, available_cash=486))
    assert "daily_three_percent_loss_cutoff" in execution_blockers(state(equity=485, available_cash=485))


def test_drawdown_cutoff_at_fifteen_percent_of_peak():
    from bot.paper_safety import execution_blockers
    s = state(equity=425, available_cash=425, day_open_equity=425, recorded_peak_equity=500)
    assert "fifteen_percent_drawdown_cutoff" in execution_blockers(s)


def test_missing_baselines_block_entries():
    from bot.paper_safety import execution_blockers
    b = execution_blockers(state(day_open_equity=None, recorded_peak_equity=None))
    assert {"missing_daily_open_equity", "missing_durable_equity_peak"} <= set(b)


def test_no_flatten_while_an_open_sell_holds_the_shares():
    # Stop leg live but unverified (e.g. journal still UNKNOWN): never double-sell.
    leg = stop_leg("new")
    filled = order(status="filled", filled=2, filled_at=NOW - timedelta(minutes=5), legs=(leg,))
    p = plan([SimpleNamespace(symbol="F", qty="2")], [filled], ((CID, "unknown", "F", 2),))
    assert p.actions == () and p.unexplained_symbols == ("F",)


def test_flatten_aborts_if_a_sell_appears_before_post():
    broker = Broker()
    broker.open_orders = [stop_leg("new", qty=1)]
    p = plan([SimpleNamespace(symbol="F", qty="1")], [])
    with pytest.raises(TradeBlocked, match="Open SELL appeared"):
        execute_maintenance(broker, p, released=True)
    assert not any(c[0] == "submit" for c in broker.calls)


def test_broker_rejection_becomes_a_clean_block():
    class Rejecting(Broker):
        def submit_order(self, *, order_data):
            raise APIError("rejected", http_error=SimpleNamespace(response=SimpleNamespace(status_code=403)))
    p = plan([SimpleNamespace(symbol="F", qty="1")], [])
    with pytest.raises(TradeBlocked, match="rejected by broker"):
        execute_maintenance(Rejecting(), p, released=True)


def test_newest_intent_explains_a_repeat_symbol():
    old = ("hds-20261101-F-aaaaaaaaaaaa", "filled", "F", 2, 24, "old-parent", "gtc")
    p = plan([SimpleNamespace(symbol="F", qty="1")], [], (), [old])
    assert [a.client_order_id for a in p.actions] == [old[0]]
    p2 = plan([SimpleNamespace(symbol="F", qty="1")], [], ((CID, "accepted", "F", 2),), [old])
    assert p2.actions[0].client_order_id == CID


def test_queued_bot_exit_is_awaited_not_alerted():
    exit_leg = order("77777777-2222-3333-4444-555555555555", status="accepted", qty=1,
                     cid=CID + "-x", side="sell", kind="market", klass="simple")
    p = plan([SimpleNamespace(symbol="F", qty="1")], [exit_leg])
    assert p.actions == () and p.unexplained_symbols == ()
