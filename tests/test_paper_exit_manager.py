"""Automatic exits: trailing stop, time limit, operator close. Fakes only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.paper_exit_manager import (
    ExitAction, execute_exit, plan_exits, reprotect_client_order_id,
)
from bot.paper_safety import TradeBlocked

CID = "hds-20261110-WMT-e9ac0958d6bb"
PID = "93adfdcc-832e-4246-b1d3-62698514aa0c"
LEG = "42ea2d95-c582-477f-a566-47d5bc8e72b4"
NOW = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)      # 12:00 NY
APPROVED = [(CID, "filled", "WMT", 1, Decimal("121.67"), PID, "gtc")]


def leg(status="new"):
    return SimpleNamespace(id=LEG, side="sell", type="stop", status=status,
                           symbol="WMT", qty="1", filled_qty="0", stop_price="121.67",
                           time_in_force="gtc", legs=[], client_order_id=None)


def parent(stop_status="new"):
    return SimpleNamespace(id=PID, client_order_id=CID, symbol="WMT", side="buy",
                           type="limit", order_class="oto", status="filled", qty="1",
                           filled_qty="1", time_in_force="gtc", legs=[leg(stop_status)],
                           filled_at=NOW - timedelta(hours=1))


POS = [SimpleNamespace(symbol="WMT", qty="1")]


def plan(market, **kw):
    return plan_exits(positions=POS, orders=[parent()], approved_stops=APPROVED,
                      now=kw.pop("now", NOW), market=market, **kw)


def test_no_exit_while_price_is_above_trail_and_inside_hold():
    assert plan({"WMT": {"bid": "126", "high_water": "127", "sessions_held": 1}}) == ()


def test_trailing_stop_exits_after_5pct_pullback_from_high():
    acts = plan({"WMT": {"bid": "132.90", "high_water": "140", "sessions_held": 2}})
    assert [a.reason for a in acts] == ["trailing_stop"]       # trail = 133.00


def test_trail_below_broker_stop_is_left_to_the_broker_stop():
    # high 126 -> trail 119.70 < 121.67 broker stop: broker stop governs.
    assert plan({"WMT": {"bid": "119", "high_water": "126", "sessions_held": 1}}) == ()


def test_time_limit_exits_on_fifth_session_after_1545():
    late = datetime(2026, 11, 14, 20, 46, tzinfo=timezone.utc)   # 15:46 NY
    acts = plan({"WMT": {"bid": "126", "high_water": "127", "sessions_held": 5}}, now=late)
    assert [a.reason for a in acts] == ["time_limit"]
    early = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
    assert plan({"WMT": {"bid": "126", "high_water": "127", "sessions_held": 5}}, now=early) == ()


def test_operator_close_targets_only_journaled_positions_with_live_stop():
    acts = plan_exits(positions=POS + [SimpleNamespace(symbol="XOM", qty="3")],
                      orders=[parent()], approved_stops=APPROVED, now=NOW, market={},
                      operator_close=True)
    assert [(a.symbol, a.stop_leg_id, a.shares) for a in acts] == [("WMT", LEG, Decimal("1"))]


def test_position_without_live_stop_leg_is_not_an_exit_candidate():
    acts = plan_exits(positions=POS, orders=[parent("canceled")], approved_stops=APPROVED,
                      now=NOW, market={}, operator_close=True)
    assert acts == ()


class Broker:
    def __init__(self, *, cancel_ok=True, leg_after="canceled", sell_fails=False,
                 position_qty="1"):
        self.calls, self.cancel_ok, self.leg_after = [], cancel_ok, leg_after
        self.sell_fails, self.position_qty, self.exit = sell_fails, position_qty, None

    def get_order_by_client_id(self, cid):
        self.calls.append(("lookup", cid))
        if self.exit is None:
            raise APIError("nf", http_error=SimpleNamespace(response=SimpleNamespace(status_code=404)))
        return self.exit

    def get_order_by_id(self, oid):
        canceled = any(c[0] == "cancel" for c in self.calls)
        return SimpleNamespace(id=oid, status=self.leg_after if canceled else "new")

    def cancel_order_by_id(self, oid):
        self.calls.append(("cancel", oid))
        if not self.cancel_ok:
            raise APIError("nope", http_error=SimpleNamespace(response=SimpleNamespace(status_code=422)))

    def get_all_positions(self):
        return [SimpleNamespace(symbol="WMT", qty=self.position_qty)]

    def submit_order(self, *, order_data):
        self.calls.append(("submit", order_data))
        if order_data.client_order_id.endswith("-x"):
            if self.sell_fails:
                raise APIError("rej", http_error=SimpleNamespace(response=SimpleNamespace(status_code=403)))
            self.exit = SimpleNamespace(status="filled", filled_avg_price="126.10")


ACTION = ExitAction(client_order_id=CID, symbol="WMT", shares=Decimal("1"),
                    stop_leg_id=LEG, durable_stop=Decimal("121.67"), reason="operator_close")


def run(broker, released=True):
    return execute_exit(broker, ACTION, released=released, sleeper=lambda _: None, polls=3)


def test_dry_run_touches_nothing():
    b = Broker()
    assert run(b, released=False)["result"] == "dry_run" and b.calls == []


def test_exit_cancels_only_the_stop_leg_then_sells_exact_shares():
    b = Broker()
    out = run(b)
    assert out["result"] == "exit_filled" and out["fill_price"] == "126.10"
    kinds = [c[0] for c in b.calls if c[0] in ("cancel", "submit")]
    assert kinds == ["cancel", "submit"] and b.calls[1] == ("cancel", LEG)
    sell = [c[1] for c in b.calls if c[0] == "submit"][0]
    assert sell.client_order_id == CID + "-x" and float(sell.qty) == 1.0
    assert str(sell.side.value) == "sell" and str(sell.type.value) == "market"


def test_rejected_cancel_leaves_position_protected():
    b = Broker(cancel_ok=False)
    with pytest.raises(TradeBlocked, match="still protected"):
        run(b)
    assert not any(c[0] == "submit" for c in b.calls)


def test_unconfirmed_cancel_never_sells():
    b = Broker(leg_after="pending_cancel")
    with pytest.raises(TradeBlocked, match="not confirmed"):
        run(b)
    assert not any(c[0] == "submit" for c in b.calls)


def test_stop_filling_during_exit_is_detected_and_no_sell_sent():
    b = Broker(leg_after="filled")
    assert run(b)["result"] == "stop_filled_during_exit"
    assert not any(c[0] == "submit" for c in b.calls)


def test_failed_sell_reprotects_with_gtc_stop_at_durable_price():
    b = Broker(sell_fails=True)
    with pytest.raises(TradeBlocked, match="re-protected"):
        run(b)
    reprotect = [c[1] for c in b.calls if c[0] == "submit"][-1]
    assert reprotect.client_order_id == reprotect_client_order_id(CID)
    assert float(reprotect.stop_price) == 121.67 and str(reprotect.time_in_force.value) == "gtc"


def test_changed_position_reprotects_instead_of_selling():
    b = Broker(position_qty="2")
    with pytest.raises(TradeBlocked, match="re-protected"):
        run(b)
    submits = [c[1] for c in b.calls if c[0] == "submit"]
    assert len(submits) == 1 and submits[0].client_order_id.endswith("-s")


def test_existing_exit_order_is_never_duplicated():
    b = Broker()
    b.exit = SimpleNamespace(status="filled")
    assert run(b)["result"] == "exit_already_exists"
    assert not any(c[0] in ("cancel", "submit") for c in b.calls)
