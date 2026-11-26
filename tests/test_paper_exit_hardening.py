"""Exit hardening: take profit, market-hours gate, overdue time limit, real
calendar counting, pending/partial exits, ambiguous SELL, re-protect stops.
Fakes only; no broker or network."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.paper_exit_manager import (
    ExitAction, execute_exit, market_snapshot, plan_exits, position_assessment,
    reprotect_client_order_id,
)
from bot.paper_safety import TradeBlocked

CID = "hds-20261110-WMT-e9ac0958d6bb"
PID = "93adfdcc-832e-4246-b1d3-62698514aa0c"
LEG = "42ea2d95-c582-477f-a566-47d5bc8e72b4"
STOP = Decimal("122.23")
APPROVED = [(CID, "filled", "WMT", 1, STOP, PID, "gtc")]
POS = [SimpleNamespace(symbol="WMT", qty="1")]
FILLED_AT = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)   # 10:00 NY Tue


def nf():
    return APIError("nf", http_error=SimpleNamespace(response=SimpleNamespace(status_code=404)))


def err(code):
    return APIError("x", http_error=SimpleNamespace(response=SimpleNamespace(status_code=code)))


def leg(status="new"):
    return SimpleNamespace(id=LEG, side="sell", type="stop", status=status, symbol="WMT",
                           qty="1", legs=[], client_order_id=None)


def parent(stop_status="new", entry="126.20"):
    return SimpleNamespace(id=PID, client_order_id=CID, symbol="WMT", side="buy",
                           type="limit", order_class="oto", status="filled", qty="1",
                           filled_qty="1", legs=[leg(stop_status)], filled_at=FILLED_AT,
                           filled_avg_price=entry)


def plan(market, now, market_open=True, orders=None):
    return plan_exits(positions=POS, orders=orders or [parent()], approved_stops=APPROVED,
                      now=now, market=market, market_open=market_open)


MIDDAY = datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)     # Tue 12:00 NY


# ---- take profit -----------------------------------------------------------

def test_take_profit_fires_at_6pct_above_entry():
    # 126.20 * 1.06 = 133.772 -> target 133.78 (rounded up)
    acts = plan({"WMT": {"bid": "133.78", "high_water": "134", "sessions_held": 2}}, MIDDAY)
    assert [a.reason for a in acts] == ["take_profit"]


def test_take_profit_not_yet_one_cent_below_target():
    assert plan({"WMT": {"bid": "133.77", "high_water": "133.77", "sessions_held": 2}}, MIDDAY) == ()


def test_assessment_reports_gain_loss_and_every_rule_level():
    a = position_assessment(entry="126.20", bid="128.34", high_water="130",
                            durable_stop=STOP, sessions_held=5)
    assert a["pnl_pct"] == "1.70" and a["take_profit_at"] == "133.78"
    assert a["trail_at"] == "123.50" and a["trail_active"] is True
    assert a["stop_at"] == "122.23"


# ---- market hours gate -----------------------------------------------------

def test_nothing_planned_while_market_closed_even_if_overdue():
    evening = datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)
    m = {"WMT": {"bid": "140", "high_water": "140", "sessions_held": 6}}
    assert plan(m, evening, market_open=False) == ()


def test_unknown_market_state_blocks_exits():
    m = {"WMT": {"bid": "140", "high_water": "140", "sessions_held": 6}}
    assert plan(m, MIDDAY, market_open=None) == ()


# ---- time limit: real Nov 10-16 calendar and overdue -----------------------

class CalendarBroker:
    """Alpaca calendar returns trading days only (no weekends/holidays)."""
    def __init__(self, days):
        self.days = days

    def get_calendar(self, req):
        return [d for d in self.days if req.start <= d <= req.end]


class Data:
    def get_stock_latest_trade(self, req):
        return {"WMT": SimpleNamespace(price="128.34")}

    def get_stock_latest_quote(self, req):
        return {"WMT": SimpleNamespace(bid_price="128.34")}

    def get_stock_bars(self, req):
        return SimpleNamespace(data={"WMT": [SimpleNamespace(high="130.00")]})


NOV = [date(2026, 11, d) for d in (10, 11, 12, 13, 16, 17, 18)]


def snapshot(now):
    return market_snapshot(market_data=Data(), trading=CalendarBroker(NOV), symbol="WMT",
                           filled_at=FILLED_AT, now=now)


def test_nov_16_is_the_fifth_session_skipping_the_weekend():
    mon = datetime(2026, 11, 16, 20, 46, tzinfo=timezone.utc)   # 15:46 NY Mon
    snap = snapshot(mon)
    assert snap["sessions_held"] == 5
    assert [a.reason for a in plan({"WMT": snap}, mon)] == ["time_limit"]


def test_fifth_session_before_1545_holds():
    mon_noon = datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc)
    assert plan({"WMT": snapshot(mon_noon)}, mon_noon) == ()


def test_missed_deadline_exits_at_next_session_open():
    tue_open = datetime(2026, 11, 17, 14, 36, tzinfo=timezone.utc)   # 09:36 NY
    snap = snapshot(tue_open)
    assert snap["sessions_held"] == 6
    acts = plan({"WMT": snap}, tue_open)
    assert [a.reason for a in acts] == ["time_limit"] and "overdue" in acts[0].detail


def test_holiday_is_not_counted():
    # Thanksgiving 2026-11-26 closed: fill Mon 23rd, Fri 27th is session 4.
    days = [date(2026, 11, d) for d in (23, 24, 25, 27)]
    fill = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    snap = market_snapshot(market_data=Data(), trading=CalendarBroker(days), symbol="WMT",
                           filled_at=fill, now=datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc))
    assert snap["sessions_held"] == 4


# ---- re-protect stop keeps the position managed -----------------------------

def test_position_protected_by_standalone_reprotect_stop_is_still_managed():
    reprotect = SimpleNamespace(id="rp-1", client_order_id=reprotect_client_order_id(CID),
                                side="sell", type="stop", status="new", symbol="WMT", legs=[])
    acts = plan({"WMT": {"bid": "140", "high_water": "140", "sessions_held": 2}}, MIDDAY,
                orders=[parent("canceled"), reprotect])
    assert [(a.reason, a.stop_leg_id) for a in acts] == [("take_profit", "rp-1")]


# ---- execution lifecycle ---------------------------------------------------

ACTION = ExitAction(client_order_id=CID, symbol="WMT", shares=Decimal("1"),
                    stop_leg_id=LEG, durable_stop=STOP, reason="time_limit")


class Broker:
    def __init__(self, *, exit_order=None, sell_raises=None, after_sell=None,
                 lookup_after_sell_raises=None, leg_status_after_cancel="canceled",
                 held=True, reprotect_exists=False):
        self.calls, self.exit = [], exit_order
        self.sell_raises, self.after_sell = sell_raises, after_sell
        self.lookup_after_sell_raises = lookup_after_sell_raises
        self.leg_after, self.held, self.reprotect_exists = leg_status_after_cancel, held, reprotect_exists
        self.sold = False

    def get_order_by_client_id(self, cid):
        self.calls.append(("lookup", cid))
        if cid.endswith("-s"):
            if self.reprotect_exists:
                return SimpleNamespace(status="new")
            raise nf()
        if self.sold and self.lookup_after_sell_raises:
            raise self.lookup_after_sell_raises
        if self.exit is None:
            raise nf()
        return self.exit

    def get_order_by_id(self, oid):
        canceled = any(c[0] == "cancel" for c in self.calls)
        return SimpleNamespace(id=oid, status=self.leg_after if canceled else "new")

    def cancel_order_by_id(self, oid):
        self.calls.append(("cancel", oid))

    def get_all_positions(self):
        return [SimpleNamespace(symbol="WMT", qty="1")] if self.held else []

    def submit_order(self, *, order_data):
        self.calls.append(("submit", order_data.client_order_id))
        if order_data.client_order_id.endswith("-x"):
            self.sold = True
            if self.sell_raises:
                self.exit = self.after_sell
                raise self.sell_raises
            self.exit = SimpleNamespace(status="filled", filled_avg_price="128.30")


def run(b):
    return execute_exit(b, ACTION, released=True, sleeper=lambda _: None, polls=2)


def submits(b):
    return [c[1] for c in b.calls if c[0] == "submit"]


@pytest.mark.parametrize("status", ["new", "accepted", "partially_filled", "pending_new"])
def test_pending_or_partial_exit_is_left_alone(status):
    b = Broker(exit_order=SimpleNamespace(status=status, filled_qty="0"))
    out = run(b)
    assert out["result"] == "exit_pending" and out["status"] == status
    assert not any(c[0] in ("cancel", "submit") for c in b.calls)


def test_dead_earlier_exit_with_live_stop_does_not_resell_or_restop():
    b = Broker(exit_order=SimpleNamespace(status="canceled"))
    with pytest.raises(TradeBlocked, match="operator review"):
        run(b)
    assert submits(b) == []


def test_dead_earlier_exit_without_stop_places_one_reprotect():
    b = Broker(exit_order=SimpleNamespace(status="expired"), leg_status_after_cancel="canceled")
    b.calls.append(("cancel", LEG))          # stop leg already gone from an earlier run
    with pytest.raises(TradeBlocked):
        run(b)
    assert submits(b) == [CID + "-s"]


def test_dead_earlier_exit_with_existing_reprotect_is_not_duplicated():
    b = Broker(exit_order=SimpleNamespace(status="expired"), reprotect_exists=True)
    b.calls.append(("cancel", LEG))
    with pytest.raises(TradeBlocked):
        run(b)
    assert submits(b) == []


def test_ambiguous_sell_that_reached_broker_is_not_reprotected():
    b = Broker(sell_raises=TimeoutError("read timeout"),
               after_sell=SimpleNamespace(status="accepted"))
    out = run(b)
    assert out["result"] == "exit_submitted" and "ambiguous" in out["note"]
    assert submits(b) == [CID + "-x"]


def test_ambiguous_sell_that_never_arrived_is_reprotected():
    b = Broker(sell_raises=TimeoutError("read timeout"), after_sell=None)
    with pytest.raises(TradeBlocked, match="re-protected"):
        run(b)
    assert submits(b) == [CID + "-x", CID + "-s"]


def test_ambiguous_sell_with_unreadable_outcome_fails_closed():
    b = Broker(sell_raises=TimeoutError("read timeout"), lookup_after_sell_raises=err(503))
    with pytest.raises(TradeBlocked, match="NOT re-protected"):
        run(b)
    assert submits(b) == [CID + "-x"]


def test_restart_after_filled_exit_does_nothing():
    b = Broker(exit_order=SimpleNamespace(status="filled"))
    assert run(b)["result"] == "exit_already_exists"
    assert submits(b) == []


def test_exit_lookup_outage_blocks_before_touching_stop():
    class Down(Broker):
        def get_order_by_client_id(self, cid):
            raise err(503)
    b = Down()
    with pytest.raises(TradeBlocked, match="lookup unavailable"):
        run(b)
    assert not any(c[0] in ("cancel", "submit") for c in b.calls)


# ---- bad quotes (incident 2026-11-18: IEX bid 119.62, WMT traded 125.10-127.70) ----

class QuoteData:
    def __init__(self, trade="127.94", bid="119.62", high="129.81"):
        self.trade, self.bid, self.high = trade, bid, high

    def get_stock_latest_trade(self, req):
        return {"WMT": SimpleNamespace(price=self.trade)}

    def get_stock_latest_quote(self, req):
        return {"WMT": SimpleNamespace(bid_price=self.bid)}

    def get_stock_bars(self, req):
        return SimpleNamespace(data={"WMT": [SimpleNamespace(high=self.high)]})


WED = datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)


def quote_snap(data, mark="127.94"):
    return market_snapshot(market_data=data, trading=CalendarBroker(NOV), symbol="WMT",
                           filled_at=FILLED_AT, now=WED, position_price=mark)


def test_bad_quote_bid_is_ignored_in_favour_of_last_trade():
    snap = quote_snap(QuoteData())
    assert snap["bid"] == Decimal("127.94") and snap["price_ok"] is True
    assert "ignored" in snap["quote_note"]


def test_bad_quote_never_triggers_trailing_stop():
    snap = quote_snap(QuoteData())
    snap["sessions_held"] = 2          # isolate the price rules from the time limit
    assert plan({"WMT": snap}, WED) == ()


def test_bad_quote_does_not_raise_high_water():
    snap = quote_snap(QuoteData(bid="150.00"))
    assert snap["high_water"] == Decimal("129.81")


def test_trade_disagreeing_with_broker_mark_blocks_price_exits():
    snap = quote_snap(QuoteData(trade="119.62", bid="119.60"), mark="127.94")
    assert snap["price_ok"] is False and "position mark" in snap["price_problem"]
    snap["sessions_held"] = 2
    assert plan({"WMT": snap}, WED) == ()      # 119.62 would otherwise hit the 123.31 trail


def test_bad_price_still_allows_time_limit_exit():
    snap = quote_snap(QuoteData(trade="119.62", bid="119.60"), mark="127.94")
    assert [a.reason for a in plan({"WMT": snap}, WED)] == ["time_limit"]


def test_real_drop_confirmed_by_broker_mark_still_trails_out():
    snap = quote_snap(QuoteData(trade="123.00", bid="122.98"), mark="123.05")
    snap["sessions_held"] = 2
    assert [a.reason for a in plan({"WMT": snap}, WED)] == ["trailing_stop"]


def test_missing_trade_blocks_price_exits():
    snap = quote_snap(QuoteData(trade="0", bid="140"))
    assert snap["price_ok"] is False and "no last trade" in snap["price_problem"]
