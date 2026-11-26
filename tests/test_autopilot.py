"""Autopilot: autonomous multi-position PAPER manager (2026-11-26)."""
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from bot.autopilot import (
    Run, Settings, client_id, exit_reason, manage_levels, market_filter_ok, qty_down,
    rank_candidates, slot_budget,
)
from bot.autopilot_journal import Journal
from bot.paper_safety import TradeBlocked
from bot.strategies import DailyBar

NY = ZoneInfo("America/New_York")
ACCT = "00000000-0000-0000-0000-000000000001"
SHA = hashlib.sha256(ACCT.encode()).hexdigest()
TODAY = date(2026, 11, 30)


def bars(n=120, start=100.0, step=0.3, end=TODAY):
    out, d, px = [], end - timedelta(days=1), start + step * n
    days = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    for i, day in enumerate(reversed(days)):
        c = start + step * i
        out.append(DailyBar(day, c, c * 1.01, c * 0.99, c, 1e6))
    return out


class FakeBroker:
    def __init__(self, *, equity=500.0, cash=500.0, positions=(), orders=(), market_open=True,
                 bars_=None, price=None):
        self.acct = NS(id=ACCT, equity=str(equity), cash=str(cash))
        self.pos = list(positions)
        self.orders = list(orders)
        self.is_open = market_open
        self.bars_ = bars_ or {}
        self.price = price or {}
        self.sent = []
        self.cancelled = []
        self.replaced = []

    def account(self): return self.acct
    def clock(self): return NS(is_open=self.is_open)
    def positions(self): return list(self.pos)
    def open_orders(self): return [o for o in self.orders if o.status == "new"]
    def closed_orders(self, symbol, side, after=None): return []

    def calendar(self, start, end):
        out, d = [], start
        while d <= end:
            if d.weekday() < 5:
                out.append(NS(date=d, close=datetime(d.year, d.month, d.day, 16, 0)))
            d += timedelta(days=1)
        return out

    def daily_bars(self, symbols, start): return {s: self.bars_.get(s, []) for s in symbols}
    def last_price(self, s): return self.price.get(s, self.bars_[s][-1].close)

    def _order(self, **kw):
        o = NS(id=f"o{len(self.sent)}", status="new", **kw)
        self.sent.append(o)
        self.orders.append(o)
        return o

    def buy_limit(self, symbol, qty, limit, cid):
        return self._order(symbol=symbol, qty=qty, limit_price=limit, side="buy", type="limit",
                           client_order_id=cid)

    def sell_market(self, symbol, qty, cid):
        return self._order(symbol=symbol, qty=qty, side="sell", type="market", client_order_id=cid)

    def sell_stop(self, symbol, qty, stop, cid):
        return self._order(symbol=symbol, qty=qty, stop_price=stop, side="sell", type="stop",
                           client_order_id=cid)

    def replace_stop(self, oid, stop):
        self.replaced.append((oid, stop))
        for o in self.orders:
            if o.id == oid:
                o.stop_price = stop

    def cancel(self, oid):
        self.cancelled.append(oid)
        for o in self.orders:
            if o.id == oid:
                o.status = "canceled"

    def order(self, oid): return next(o for o in self.orders if o.id == oid)


def at(h, m, d=TODAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY).astimezone(timezone.utc)


def make_run(broker, tmp_path, now=None, trade=True, settings=Settings()):
    return Run(broker=broker, journal=Journal(":memory:"), settings=settings,
               now=now or at(9, 45), trade=trade, folder=tmp_path, sleep=lambda s: None)


def universe_bars(up=("AAPL", "MSFT", "JPM", "KO", "XLK", "XLF", "NVDA"), spy_up=True):
    data = {s: bars() for s in up}
    data["SPY"] = bars(step=0.3 if spy_up else -0.3, start=100 if spy_up else 200)
    return data


# ------------------------------------------------------------------ pure

def test_market_filter():
    assert market_filter_ok(bars(step=0.3), 50)
    assert not market_filter_ok(bars(step=-0.3, start=200), 50)
    assert market_filter_ok([], 0)


def test_slot_budget_uses_equity_slots_and_cash_without_margin():
    s = Settings(max_positions=5, cash_reserve=0.02)
    assert slot_budget(equity=500, cash=500, pending_buys=0, s=s) == pytest.approx(98.0)
    assert slot_budget(equity=500, cash=60, pending_buys=0, s=s) == pytest.approx(50.0)
    assert slot_budget(equity=500, cash=60, pending_buys=60, s=s) == 0.0
    half = Settings(max_positions=5, cash_reserve=0.02, size_scale=0.5)
    assert slot_budget(equity=500, cash=500, pending_buys=0, s=half) == pytest.approx(49.0)


def test_trailing_stop_only_rises():
    s = Settings(trail=0.05)
    pos = {"stop": 117.14, "high_water": 120.0, "entry_price": 120.81}
    up = manage_levels(pos, 130.0, s)
    assert up["high_water"] == 130.0 and up["stop"] == 123.5
    assert manage_levels(up, 125.0, s)["stop"] == 123.5


def test_exit_rules():
    s = Settings(max_sessions=20)
    pos = {"stop": 100.0, "entry_price": 105.0}
    kw = dict(local_time=time(10, 0), session_close=None, s=s)
    assert exit_reason(pos, price=99.9, sessions=3, **kw).startswith("stop")
    assert exit_reason(pos, price=110, sessions=3, **kw) is None
    assert exit_reason(pos, price=110, sessions=21, **kw).startswith("time limit (overdue")
    assert exit_reason(pos, price=110, sessions=20, local_time=time(12, 50),
                       session_close=time(13, 0), s=s).startswith("time limit")
    assert exit_reason(pos, price=110, sessions=20, local_time=time(12, 40),
                       session_close=time(13, 0), s=s) is None
    assert exit_reason(pos, price=111.4, sessions=1, local_time=time(10), session_close=None,
                       s=Settings(take_profit=0.06)).startswith("take-profit")


def test_rank_excludes_held_and_respects_symbol_lists():
    data = universe_bars()
    ranked = rank_candidates(data, "trend_following", {"AAPL"})
    assert "AAPL" not in ranked and "MSFT" in ranked
    only = rank_candidates(data, "ma_trend", set(), {"ma": 50, "symbols": "SPY+XLK"})
    assert set(only) == {"SPY", "XLK"}


def test_cash_strategy_opens_nothing(tmp_path):
    b = FakeBroker(bars_=universe_bars())
    rec = make_run(b, tmp_path, settings=Settings(strategy="cash")).execute(SHA)
    assert not b.sent and "cash" in rec["entries"]["skip"]


def test_engine_settings_file_is_loaded(tmp_path):
    from bot.autopilot import load_settings
    (tmp_path / "autopilot-settings.json").write_text(
        '{"strategy": "momentum", "params": {"lookback": 126}, "system_id": "x", '
        '"max_positions": 10, "trail": 0.1, "universe": ["SPY", "QQQ"], "unknown": 1}')
    s = load_settings(tmp_path)
    assert (s.strategy, s.params, s.max_positions, s.trail, s.universe) == (
        "momentum", {"lookback": 126}, 10, 0.1, ("SPY", "QQQ"))


def test_ids_are_deterministic_and_short():
    cid = client_id(TODAY, "XLRE", "stop", 3)
    assert cid == "hds-auto-20261130-XLRE-stop-3" and len(cid) <= 48
    assert qty_down(98.0 / 33.333) == qty_down("2.940029")


# ------------------------------------------------------------------ runs

def test_fills_free_slots_with_fractional_buys(tmp_path):
    b = FakeBroker(bars_=universe_bars())
    rec = make_run(b, tmp_path).execute(SHA)
    buys = [o for o in b.sent if o.side == "buy"]
    assert len(buys) == 5                                  # max_positions
    assert all(o.client_order_id.endswith("-entry") for o in buys)
    total = sum(float(o.qty) * o.limit_price for o in buys)
    assert 480 <= total <= 491                              # ~98% invested, no margin
    assert rec["entries"]["bought"] and rec["status"] == "ok"


def test_no_entries_when_market_filter_fails(tmp_path):
    b = FakeBroker(bars_=universe_bars(spy_up=False))
    rec = make_run(b, tmp_path).execute(SHA)
    assert not b.sent and "SPY below" in rec["entries"]["skip"]


def test_no_entries_outside_window_or_when_paused(tmp_path):
    b = FakeBroker(bars_=universe_bars())
    assert "window" in make_run(b, tmp_path, now=at(14, 0)).execute(SHA)["entries"]["skip"]
    (tmp_path / "PAUSE").write_text("")
    assert "pause" in make_run(b, tmp_path).execute(SHA)["entries"]["skip"]
    assert not b.sent


def test_account_safety_net_halves_entries(tmp_path):
    b = FakeBroker(equity=440, cash=440, bars_=universe_bars())
    run = make_run(b, tmp_path)
    run.journal.record_equity(500.0, 500.0)               # peak 500 -> now -12%
    rec = run.execute(SHA)
    buys = [o for o in b.sent if o.side == "buy"]
    assert buys and rec["entries"]["account_drawdown"] == pytest.approx(0.12)
    assert all(float(o.qty) * o.limit_price < 45 for o in buys)   # ~half of a $86 slot


def test_dry_run_sends_nothing(tmp_path):
    b = FakeBroker(bars_=universe_bars())
    rec = make_run(b, tmp_path, trade=False).execute(SHA)
    assert not b.sent and len(rec["entries"]["bought"]) == 5


def test_wrong_account_is_refused(tmp_path):
    with pytest.raises(TradeBlocked, match="pinned"):
        make_run(FakeBroker(), tmp_path).execute("0" * 64)


def test_bad_price_is_skipped(tmp_path):
    data = universe_bars(up=("AAPL",))
    b = FakeBroker(bars_=data, price={"AAPL": data["AAPL"][-1].close * 2})
    make_run(b, tmp_path).execute(SHA)
    assert not [o for o in b.sent if o.side == "buy" and o.symbol == "AAPL"]


def dis(qty="1", price=125.6):
    return NS(symbol="DIS", qty=qty, avg_entry_price="120.81", current_price=str(price),
              unrealized_pl=str((price - 120.81) * float(qty)))


def test_adopts_existing_position_and_raises_its_stop(tmp_path):
    leg = NS(id="leg1", symbol="DIS", side="sell", type="stop", status="new", qty="1",
             stop_price="117.14", client_order_id="hds-20261120-DIS-x")
    b = FakeBroker(equity=507, cash=382, positions=[dis()], orders=[leg],
                   bars_=universe_bars(up=()), market_open=True)
    run = make_run(b, tmp_path, now=at(14, 0))
    rec = run.execute(SHA)
    meta = run.journal.positions()["DIS"]
    assert meta["adopted"] == 1 and meta["stop"] >= 117.14
    assert b.replaced and b.replaced[0][1] == pytest.approx(119.32)   # 5% under 125.6
    assert rec["positions"][0]["symbol"] == "DIS"


def test_fractional_position_without_stop_gets_one(tmp_path):
    p = NS(symbol="KO", qty="1.4", avg_entry_price="70", current_price="71",
           unrealized_pl="1.4")
    b = FakeBroker(positions=[p], bars_=universe_bars(up=()), market_open=False)
    make_run(b, tmp_path, now=at(9, 20)).execute(SHA)
    stops = [o for o in b.sent if o.type == "stop"]
    assert len(stops) == 1 and stops[0].qty == "1.4" and stops[0].stop_price == 67.9


def test_stop_hit_cancels_broker_stop_then_sells(tmp_path):
    leg = NS(id="s1", symbol="DIS", side="sell", type="stop", status="new", qty="1",
             stop_price="117.14")
    b = FakeBroker(positions=[dis(price=117.0)], orders=[leg], bars_=universe_bars(up=()))
    make_run(b, tmp_path, now=at(11, 0)).execute(SHA)
    assert "s1" in b.cancelled
    sells = [o for o in b.sent if o.type == "market"]
    assert len(sells) == 1 and sells[0].client_order_id.endswith("-exit")


def test_never_sells_if_stop_already_filled(tmp_path):
    leg = NS(id="s1", symbol="DIS", side="sell", type="stop", status="new", qty="1",
             stop_price="117.14")
    b = FakeBroker(positions=[dis(price=117.0)], orders=[leg], bars_=universe_bars(up=()))
    b.cancel = lambda oid: setattr(leg, "status", "filled")
    make_run(b, tmp_path, now=at(11, 0)).execute(SHA)
    assert not [o for o in b.sent if o.type == "market"]


def test_closed_position_leaves_journal(tmp_path):
    b = FakeBroker(bars_=universe_bars(up=()), market_open=False)
    run = make_run(b, tmp_path, now=at(17, 0))
    run.journal.upsert_position({"symbol": "KO", "opened_on": "2026-11-20", "entry_price": 70,
                                 "qty": "1", "strategy": "trend_following", "stop": 68,
                                 "high_water": 71})
    run.execute(SHA)
    assert "KO" not in run.journal.positions()


def test_runaway_guard_blocks_new_entries(tmp_path):
    b = FakeBroker(bars_=universe_bars())
    run = make_run(b, tmp_path, settings=Settings(max_orders_per_day=2))
    for i in range(2):
        run.journal.record_order(client_order_id=f"x{i}", day=TODAY.isoformat(), symbol="A",
                                 side="buy", kind="limit", qty="1", price=1, purpose="entry")
    rec = run.execute(SHA)
    assert rec["entries"]["skip"] == "order ceiling" and not b.sent


def test_does_not_rebuy_symbol_traded_today(tmp_path):
    b = FakeBroker(bars_=universe_bars(up=("AAPL",)))
    run = make_run(b, tmp_path)
    run.journal.record_order(client_order_id="e", day=TODAY.isoformat(), symbol="AAPL",
                             side="sell", kind="market", qty="1", price=None, purpose="exit")
    run.execute(SHA)
    assert not [o for o in b.sent if o.side == "buy" and o.symbol == "AAPL"]
