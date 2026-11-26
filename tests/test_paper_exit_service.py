"""Exit-only service orchestration and the preflight managed-positions gate. Fakes only."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import bot.paper_reconcile as reconcile
from bot.paper_exit_service import manage_once
from bot.paper_release_preflight import positions_managed

CID = "hds-20261110-WMT-e9ac0958d6bb"
PID = "93adfdcc-832e-4246-b1d3-62698514aa0c"
LEG = "42ea2d95-c582-477f-a566-47d5bc8e72b4"
FILLED_AT = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
TUE_OPEN = datetime(2026, 11, 17, 14, 36, tzinfo=timezone.utc)


def stop_leg():
    return SimpleNamespace(id=LEG, side="sell", type="stop", status="new", symbol="WMT",
                           qty="1", legs=[], client_order_id=None)


def parent():
    return SimpleNamespace(id=PID, client_order_id=CID, symbol="WMT", side="buy", type="limit",
                           order_class="oto", status="filled", qty="1", filled_qty="1",
                           legs=[stop_leg()], filled_at=FILLED_AT, filled_avg_price="126.20")


class Trading:
    def __init__(self, *, is_open=True, positions=None):
        self.is_open, self.writes = is_open, []
        self.positions = positions if positions is not None else [SimpleNamespace(symbol="WMT", qty="1")]

    def get_clock(self):
        return SimpleNamespace(is_open=self.is_open)

    def get_all_positions(self):
        return self.positions

    def get_orders(self, filter=None):
        return [parent()] if self.positions else []

    def get_calendar(self, req):
        from datetime import date
        days = [date(2026, 11, d) for d in (10, 11, 12, 13, 16, 17)]
        return [d for d in days if req.start <= d <= req.end]

    def submit_order(self, **kw):
        self.writes.append(kw)

    def cancel_order_by_id(self, oid):
        self.writes.append(oid)


class Data:
    def get_stock_latest_trade(self, req):
        return {"WMT": SimpleNamespace(price="128.34")}

    def get_stock_latest_quote(self, req):
        return {"WMT": SimpleNamespace(bid_price="128.34")}

    def get_stock_bars(self, req):
        return SimpleNamespace(data={"WMT": [SimpleNamespace(high="130.00")]})


class Monitor:
    def read_approved_stop_intents(self, limit):
        return [(CID, "filled", "WMT", 1, Decimal("122.23"), PID, "gtc")]


@pytest.fixture(autouse=True)
def no_parent_fetch(monkeypatch):
    monkeypatch.setattr(reconcile, "orders_with_recent_parents",
                        lambda trading, orders, now: (orders, None))


def run(trading, released=False):
    return manage_once(trading=trading, market_data=Data(), monitor=Monitor(),
                       now=TUE_OPEN, released=released)


def test_overdue_wmt_is_assessed_and_exit_planned_in_dry_run():
    t = Trading()
    rec = run(t)
    pos = rec["positions"][0]
    assert pos["pnl_pct"] == "1.70" and pos["sessions_held"] == 6
    assert pos["take_profit_at"] == "133.78" and pos["stop_at"] == "122.23"
    assert [(e["result"], e["reason"]) for e in rec["exits"]] == [("dry_run", "time_limit")]
    assert rec["status"] == "ok" and t.writes == []


def test_market_closed_assesses_but_never_exits():
    t = Trading(is_open=False)
    rec = run(t, released=True)
    assert rec["positions"] and rec["exits"] == [] and t.writes == []


def test_flat_account_is_flat():
    assert run(Trading(positions=[]))["status"] == "flat"


def test_position_without_journal_intent_is_reported_unmanaged():
    t = Trading(positions=[SimpleNamespace(symbol="WMT", qty="1"),
                           SimpleNamespace(symbol="XOM", qty="2")])
    rec = run(t)
    assert rec["status"] == "unmanaged" and rec["unmanaged_symbols"] == ["XOM"]


def test_service_module_never_builds_buy_orders():
    import inspect
    import bot.paper_exit_service as svc
    import bot.paper_exit_manager as mgr
    for mod in (svc, mgr):
        assert "OrderSide.BUY" not in inspect.getsource(mod)


# ---- preflight gate --------------------------------------------------------

POS = [SimpleNamespace(symbol="WMT", qty="1")]
ORDERS = [SimpleNamespace(symbol="WMT", side="buy", legs=[stop_leg()])]
GOOD = {"status": "ok", "released": True, "unmanaged_symbols": [],
        "positions": [{"symbol": "WMT", "pnl_pct": "1.70"}]}


def test_gate_green_when_flat():
    assert positions_managed([], [], None, None, market_open=True) == (True, "flat")


def test_gate_green_when_released_service_manages_every_position():
    ok, detail = positions_managed(POS, ORDERS, GOOD, 120, market_open=True)
    assert ok and "WMT 1.70%" in detail


@pytest.mark.parametrize("receipt,age,why", [
    (None, None, "missing"),
    (GOOD, 900, "stale"),
    ({**GOOD, "released": False}, 60, "DRY RUN"),
    ({**GOOD, "status": "blocked"}, 60, "status=blocked"),
    ({**GOOD, "positions": []}, 60, "managed=[]"),
])
def test_gate_red_when_not_actually_managed(receipt, age, why):
    ok, detail = positions_managed(POS, ORDERS, receipt, age, market_open=True)
    assert not ok and why in detail


def test_gate_allows_overnight_receipt_while_market_closed():
    assert positions_managed(POS, ORDERS, GOOD, 3 * 3600, market_open=False)[0]


def test_gate_red_on_stray_buy_order():
    stray = ORDERS + [SimpleNamespace(symbol="AAPL", side="buy", legs=[])]
    ok, detail = positions_managed(POS, stray, GOOD, 60, market_open=True)
    assert not ok and "non-exit" in detail
