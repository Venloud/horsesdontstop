"""Autonomous PAPER portfolio manager (owner directive 2026-11-26).

One run (every few minutes, Task Scheduler) does, on the Alpaca PAPER account:
  1. verify PAPER endpoint + pinned account (genuine-failure guard);
  2. sync the local journal with broker positions (adopts unknown positions);
  3. manage every position: raise the trailing stop, exit on stop / time
     limit, keep a broker stop order on every position (re-placed daily for
     fractional shares, which Alpaca only allows as DAY stops);
  4. open new positions in the morning entry window: market filter, strategy
     signals, rank, fill free slots with fractional limit buys sized from
     current equity (profits are reinvested automatically).

No per-trade approval, no Neon, no kill switch. Guards only against genuine
failures: wrong account/endpoint, overlapping runs, duplicate orders (fixed
daily client order IDs), runaway order counts, bad prices, unprotected
positions, selling shares we no longer hold. The owner can pause NEW entries
by creating the file PAUSE in the runtime folder; exits always continue.

Defaults are the backtested rule (2026-11-25): trend_following entries only
while SPY closes above its 50-day average; 3% initial stop, 5% trailing stop,
no take-profit, at most 20 sessions. Settings can be overridden by
autopilot-settings.json in the runtime folder (the strategy engine writes it).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .autopilot_journal import Journal
from .paper_exit_manager import time_exit_cutoff
from .paper_safety import PAPER_ENDPOINT, TradeBlocked
from .strategies import DailyBar, signal_score

NY = ZoneInfo("America/New_York")
RUNTIME = Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop"
UNIVERSE = ("AAPL", "AMD", "BAC", "CVX", "DIS", "F", "INTC", "JPM", "KO", "MSFT", "NVDA",
            "PFE", "TSLA", "WMT", "XOM", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP",
            "XLRE", "XLU", "XLV", "XLY")
ACTIVE = {"new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled",
          "pending_replace", "replaced", "held", "calculated", "pending_cancel"}


@dataclass(frozen=True)
class Settings:
    strategy: str = "trend_following"
    market_filter_ma: int = 50          # entries only while SPY > its N-day average (0 = off)
    max_positions: int = 5
    cash_reserve: float = 0.02          # fraction of equity kept as cash
    min_order_usd: float = 5.0
    initial_stop: float = 0.03
    trail: float = 0.05
    take_profit: float | None = None
    max_sessions: int = 20
    entry_start: str = "09:35"
    entry_end: str = "10:30"
    buy_order_minutes: int = 20         # cancel unfilled entry limits after this
    limit_buffer: float = 0.005
    max_orders_per_day: int = 40        # runaway guard, not a strategy limit
    max_price_jump: float = 0.15        # live price vs last close sanity check
    universe: tuple = UNIVERSE


def load_settings(folder: Path = RUNTIME) -> Settings:
    path = folder / "autopilot-settings.json"
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
    if "universe" in known:
        known["universe"] = tuple(known["universe"])
    return replace(Settings(), **known)


def v(x):
    """Enum or plain value -> lowercase string."""
    return str(getattr(x, "value", x)).lower() if x is not None else ""


def cents_down(x) -> float:
    return float(Decimal(str(round(float(x), 6))).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def qty_down(x, places=6) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


def is_whole(qty) -> bool:
    q = Decimal(str(qty))
    return q == q.to_integral_value()


def ny_date(ts) -> date:
    if isinstance(ts, datetime):
        return (ts.astimezone(NY) if ts.tzinfo else ts).date()
    return date.fromisoformat(str(ts)[:10])


def hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


# ---------------------------------------------------------------- planning (pure)

def market_filter_ok(spy: list[DailyBar], n: int) -> bool:
    if n <= 0:
        return True
    if len(spy) < n:
        return False
    return spy[-1].close > sum(b.close for b in spy[-n:]) / n


def rank_candidates(bars: dict[str, list[DailyBar]], strategy: str, exclude: set) -> list:
    out = []
    for sym, hist in bars.items():
        if sym in exclude or sym == "SPY" or not hist:
            continue
        try:
            score = signal_score(strategy, hist)
        except Exception:
            score = None
        if score is not None and score > 0:
            out.append((score, sym))
    return [s for _, s in sorted(out, reverse=True)]


def slot_budget(*, equity: float, cash: float, pending_buys: float, s: Settings) -> float:
    """Dollars for the next new position: an equal slot of current equity,
    limited by spendable cash (no margin)."""
    slot = equity * (1 - s.cash_reserve) / s.max_positions
    spendable = cash - pending_buys - equity * s.cash_reserve
    return max(0.0, min(slot, spendable))


def manage_levels(pos: dict, price: float, s: Settings) -> dict:
    """Raise high water and trailing stop; never lowers the stop."""
    high = max(float(pos["high_water"]), price)
    stop = max(float(pos["stop"]), cents_down(high * (1 - s.trail)))
    return {**pos, "high_water": high, "stop": stop}


def exit_reason(pos: dict, *, price: float, sessions: int, local_time: dtime,
                session_close, s: Settings) -> str | None:
    if price <= float(pos["stop"]):
        return f"stop {pos['stop']}"
    if s.take_profit and price >= float(pos["entry_price"]) * (1 + s.take_profit):
        return f"take-profit {s.take_profit:.0%}"
    if sessions > s.max_sessions:
        return f"time limit (overdue, {sessions} sessions)"
    if sessions == s.max_sessions and local_time >= time_exit_cutoff(session_close):
        return f"time limit ({sessions} sessions)"
    return None


def client_id(day: date, symbol: str, purpose: str, n: int = 0) -> str:
    suffix = f"-{n}" if n else ""
    return f"hds-auto-{day:%Y%m%d}-{symbol}-{purpose}{suffix}"[:48]


# ---------------------------------------------------------------- broker helpers

class Broker:
    """Thin wrapper so the run logic is testable with a fake."""

    def __init__(self, trading, market_data):
        self.t, self.md = trading, market_data

    def account(self):
        return self.t.get_account()

    def clock(self):
        return self.t.get_clock()

    def positions(self):
        return self.t.get_all_positions()

    def open_orders(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        return self.t.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=500, nested=False))

    def closed_orders(self, symbol, side, after=None):
        from alpaca.trading.enums import OrderSide, QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        return self.t.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=100,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL, after=after,
            nested=False))

    def calendar(self, start: date, end: date):
        from alpaca.trading.requests import GetCalendarRequest
        return self.t.get_calendar(GetCalendarRequest(start=start, end=end))

    def daily_bars(self, symbols, start: date) -> dict[str, list[DailyBar]]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        res = self.md.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day, start=start,
            feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)).data
        out = {}
        for sym, rows in res.items():
            bars = []
            for b in rows:
                try:
                    bars.append(DailyBar(ny_date(b.timestamp), float(b.open), float(b.high),
                                         float(b.low), float(b.close), float(b.volume)))
                except ValueError:
                    continue
            out[sym] = bars
        return out

    def last_price(self, symbol) -> float:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest
        trade = self.md.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))[symbol]
        return float(trade.price)

    def buy_limit(self, symbol, qty, limit, cid):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
        return self.t.submit_order(LimitOrderRequest(
            symbol=symbol, qty=float(qty), side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            limit_price=limit, client_order_id=cid))

    def sell_market(self, symbol, qty, cid):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        return self.t.submit_order(MarketOrderRequest(
            symbol=symbol, qty=float(qty), side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
            client_order_id=cid))

    def sell_stop(self, symbol, qty, stop, cid):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest
        tif = TimeInForce.GTC if is_whole(qty) else TimeInForce.DAY
        return self.t.submit_order(StopOrderRequest(
            symbol=symbol, qty=float(qty), side=OrderSide.SELL, time_in_force=tif,
            stop_price=stop, client_order_id=cid))

    def replace_stop(self, order_id, stop):
        from alpaca.trading.requests import ReplaceOrderRequest
        return self.t.replace_order_by_id(order_id, ReplaceOrderRequest(stop_price=stop))

    def cancel(self, order_id):
        self.t.cancel_order_by_id(order_id)

    def order(self, order_id):
        return self.t.get_order_by_id(order_id)


# ---------------------------------------------------------------- the run

@dataclass
class Run:
    broker: Broker
    journal: Journal
    settings: Settings
    now: datetime
    trade: bool                      # False = dry run (decide + log, no orders)
    notify: object = None
    folder: Path = RUNTIME
    record: dict = field(default_factory=dict)
    sleep: object = time.sleep

    def log(self, level, kind, detail=None):
        self.journal.event(level, kind, detail)
        self.record.setdefault("events", []).append({"level": level, "kind": kind, "detail": detail})
        if self.notify and level in ("alert", "trade"):
            try:
                self.notify(kind, str(detail)[:300], "high" if level == "alert" else "default")
            except Exception:
                pass

    # -- orders today, runaway guard
    def orders_today(self) -> int:
        return len(self.journal.orders_on(self.today.isoformat()))

    @property
    def today(self) -> date:
        return self.now.astimezone(NY).date()

    def sessions_held(self, opened_on: str, cal) -> int:
        start = date.fromisoformat(opened_on)
        return sum(1 for d in cal if start <= ny_date(d.date) <= self.today)

    # ------------------------------------------------------------------
    def execute(self, expected_account_sha256: str) -> dict:
        s, b = self.settings, self.broker
        self.record = {"schema": 1, "at": self.now.isoformat(), "trade": self.trade,
                       "settings": {k: v_ for k, v_ in asdict(s).items() if k != "universe"}}
        acct = b.account()
        if _account_hash(acct) != expected_account_sha256:
            raise TradeBlocked("Account does not match pinned PAPER identity")
        equity, cash = float(acct.equity), float(acct.cash)
        self.journal.record_equity(equity, cash)
        clock = b.clock()
        market_open = getattr(clock, "is_open", None) is True
        self.record.update(equity=equity, cash=cash, market_open=market_open)
        cal = b.calendar(self.today - timedelta(days=120), self.today)
        today_cal = next((d for d in cal if ny_date(d.date) == self.today), None)
        session_close = getattr(today_cal, "close", None)
        open_orders = b.open_orders()
        positions = {p.symbol: p for p in b.positions()}
        bars = None

        # 1. journal <-> broker
        journal_pos = self.journal.positions()
        for sym, meta in journal_pos.items():
            if sym not in positions:
                self._record_closed(sym, meta)
        for sym, p in positions.items():
            if float(p.qty) < 0:
                self.log("alert", "short position found", {"symbol": sym, "qty": p.qty})
                continue
            if sym not in journal_pos:
                if bars is None:
                    bars = self._bars(s)
                self._adopt(p, open_orders, bars.get(sym, []))
        journal_pos = self.journal.positions()

        # 2. manage positions
        out = []
        for sym, p in positions.items():
            meta = journal_pos.get(sym)
            if meta is None:
                continue
            price = float(p.current_price or 0)
            row = {"symbol": sym, "qty": str(p.qty), "entry": meta["entry_price"],
                   "price": price, "pnl_usd": round(float(p.unrealized_pl or 0), 2)}
            if price <= 0:
                row["problem"] = "no price"
                out.append(row)
                self._protect(sym, p, meta, open_orders)
                continue
            if market_open:
                meta = manage_levels(meta, price, s)
                if meta["qty"] != str(p.qty):
                    meta["qty"] = str(p.qty)
                self.journal.upsert_position(meta)
            held = self.sessions_held(meta["opened_on"], cal)
            row.update(stop=meta["stop"], high_water=meta["high_water"], sessions=held,
                       pnl_pct=round((price / meta["entry_price"] - 1) * 100, 2))
            reason = exit_reason(meta, price=price, sessions=held,
                                 local_time=self.now.astimezone(NY).time(),
                                 session_close=session_close, s=s) if market_open else None
            if reason:
                row["exit"] = reason
                self._exit(sym, p, meta, open_orders, reason)
            else:
                self._protect(sym, p, meta, open_orders)
            out.append(row)
        self.record["positions"] = out

        # 3. stale entry orders
        self._cancel_stale_buys(open_orders)

        # 4. entries
        self.record["entries"] = self._entries(market_open, equity, cash, positions,
                                               open_orders, bars)
        self.record["status"] = "ok"
        return self.record

    # ------------------------------------------------------------------
    def _bars(self, s: Settings):
        cache = self.folder / "autopilot-bars" / f"{self.today.isoformat()}.json"
        if cache.exists():
            raw = json.loads(cache.read_text(encoding="utf-8"))
            return {k: [DailyBar(date.fromisoformat(r[0]), *r[1:]) for r in rows]
                    for k, rows in raw.items()}
        bars = self.broker.daily_bars(tuple(s.universe) + ("SPY",),
                                      self.today - timedelta(days=160))
        bars = {k: [x for x in rows if x.day < self.today] for k, rows in bars.items()}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({k: [[x.day.isoformat(), x.open, x.high, x.low, x.close,
                                              x.volume] for x in rows]
                                         for k, rows in bars.items()}), encoding="utf-8")
        except OSError:
            pass
        return bars

    def _adopt(self, p, open_orders, hist):
        sym = p.symbol
        entry = float(p.avg_entry_price)
        opened = self.today
        try:
            fills = [o for o in self.broker.closed_orders(sym, "buy")
                     if getattr(o, "filled_at", None) is not None]
            if fills:
                opened = ny_date(max(o.filled_at for o in fills))
        except Exception:
            pass
        stops = [float(o.stop_price) for o in open_orders
                 if o.symbol == sym and v(o.side) == "sell" and v(o.type) == "stop"
                 and getattr(o, "stop_price", None)]
        high = max([entry, float(p.current_price or 0)]
                   + [x.high for x in hist if x.day >= opened])
        stop = max(stops) if stops else cents_down(entry * (1 - self.settings.initial_stop))
        meta = {"symbol": sym, "opened_on": opened.isoformat(), "entry_price": entry,
                "qty": str(p.qty), "strategy": "adopted", "stop": stop, "high_water": high,
                "adopted": 1}
        # Entries the autopilot itself sent keep their strategy name.
        mine = [o for o in self.journal.orders_on(opened.isoformat(), "entry") if o["symbol"] == sym]
        if mine:
            meta.update(strategy=self.settings.strategy, adopted=0,
                        stop=max(stop, cents_down(entry * (1 - self.settings.initial_stop))))
        self.journal.upsert_position(meta)
        self.log("info", "position tracked", {"symbol": sym, "entry": entry, "stop": meta["stop"],
                                              "opened_on": meta["opened_on"]})

    def _record_closed(self, sym, meta):
        exit_price, reason = None, "closed at broker"
        try:
            after = datetime.fromisoformat(meta["opened_on"]).replace(tzinfo=NY)
            sells = [o for o in self.broker.closed_orders(sym, "sell", after=after)
                     if getattr(o, "filled_at", None) is not None]
            if sells:
                last = max(sells, key=lambda o: o.filled_at)
                exit_price = float(last.filled_avg_price)
                reason = "stop order filled" if v(last.type) == "stop" else "sold"
        except Exception:
            pass
        if exit_price is not None:
            self.journal.record_trade(symbol=sym, strategy=meta["strategy"],
                                      opened_on=meta["opened_on"], entry_price=meta["entry_price"],
                                      exit_price=exit_price, qty=meta["qty"], reason=reason)
        self.journal.remove_position(sym)
        self.log("trade", "position closed", {"symbol": sym, "exit": exit_price, "reason": reason})

    def _stops_for(self, sym, open_orders):
        return [o for o in open_orders if o.symbol == sym and v(o.side) == "sell"
                and v(o.type) in ("stop", "stop_limit", "trailing_stop") and v(o.status) in ACTIVE]

    def _protect(self, sym, p, meta, open_orders):
        """Exactly one broker stop for the full position at >= the journal stop."""
        stops = self._stops_for(sym, open_orders)
        qty = str(p.qty)
        want = cents_down(meta["stop"])
        if len(stops) == 1 and Decimal(str(stops[0].qty)) == Decimal(qty):
            have = float(stops[0].stop_price or 0)
            if have + 0.005 >= want:
                return
            if not self.trade:
                self.log("info", "dry run: would raise stop", {"symbol": sym, "from": have, "to": want})
                return
            try:
                self.broker.replace_stop(stops[0].id, want)
                self.log("info", "stop raised", {"symbol": sym, "from": have, "to": want})
                return
            except Exception as exc:
                self.log("info", "stop replace failed, re-placing",
                         {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"[:200]})
        if not self.trade:
            self.log("info", "dry run: would place stop", {"symbol": sym, "stop": want, "qty": qty})
            return
        for o in stops:                      # wrong qty or duplicates: start clean
            if not self._cancel_confirmed(o.id):
                self.log("alert", "stop cancel not confirmed", {"symbol": sym})
                return
        n = sum(1 for o in self.journal.orders_on(self.today.isoformat()) if o["symbol"] == sym
                and o["purpose"] == "stop")
        cid = client_id(self.today, sym, "stop", n)
        try:
            o = self.broker.sell_stop(sym, qty, want, cid)
            self.journal.record_order(client_order_id=cid, day=self.today.isoformat(), symbol=sym,
                                      side="sell", kind="stop", qty=qty, price=want,
                                      purpose="stop", broker_id=str(o.id), status=v(o.status))
            self.log("info", "stop placed", {"symbol": sym, "stop": want, "qty": qty})
        except Exception as exc:
            self.log("alert", "UNPROTECTED: stop order failed",
                     {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"[:200]})

    def _cancel_confirmed(self, order_id, polls=30) -> bool:
        try:
            self.broker.cancel(order_id)
        except Exception:
            pass
        for _ in range(polls):
            st = v(getattr(self.broker.order(order_id), "status", ""))
            if st in ("canceled", "expired", "rejected"):
                return True
            if st == "filled":
                return False
            self.sleep(1)
        return False

    def _exit(self, sym, p, meta, open_orders, reason):
        if not self.trade:
            self.log("info", "dry run: would sell", {"symbol": sym, "reason": reason})
            return
        for o in self._stops_for(sym, open_orders):
            if not self._cancel_confirmed(o.id):
                # The stop may have filled; never sell shares we may not hold.
                self.log("info", "exit deferred: stop not cancelled yet", {"symbol": sym})
                return
        live = {x.symbol: x for x in self.broker.positions()}.get(sym)
        if live is None or float(live.qty) <= 0:
            return
        qty = str(live.qty)
        cid = client_id(self.today, sym, "exit")
        try:
            o = self.broker.sell_market(sym, qty, cid)
            self.journal.record_order(client_order_id=cid, day=self.today.isoformat(), symbol=sym,
                                      side="sell", kind="market", qty=qty, price=None,
                                      purpose="exit", broker_id=str(o.id), status=v(o.status),
                                      detail=reason)
            self.log("trade", "SELL", {"symbol": sym, "qty": qty, "reason": reason,
                                       "entry": meta["entry_price"]})
        except Exception as exc:
            self.log("alert", "exit order failed", {"symbol": sym, "reason": reason,
                                                    "error": f"{type(exc).__name__}: {exc}"[:200]})
            self._protect(sym, live, meta, [])

    def _cancel_stale_buys(self, open_orders):
        limit = timedelta(minutes=self.settings.buy_order_minutes)
        for o in open_orders:
            cid = str(getattr(o, "client_order_id", "") or "")
            if not cid.startswith("hds-auto-") or v(o.side) != "buy":
                continue
            sub = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
            if sub is not None and self.now - sub > limit and self.trade:
                try:
                    self.broker.cancel(o.id)
                    self.log("info", "unfilled entry cancelled", {"symbol": o.symbol})
                except Exception:
                    pass

    def _entries(self, market_open, equity, cash, positions, open_orders, bars):
        s = self.settings
        local = self.now.astimezone(NY).time()
        info = {"considered": False}
        if not market_open or not hhmm(s.entry_start) <= local <= hhmm(s.entry_end):
            info["skip"] = "outside entry window"
            return info
        if (self.folder / "PAUSE").exists():
            info["skip"] = "owner pause file present"
            return info
        if self.orders_today() >= s.max_orders_per_day:
            self.log("alert", "runaway guard: daily order ceiling reached", s.max_orders_per_day)
            info["skip"] = "order ceiling"
            return info
        bars = bars or self._bars(s)
        spy = bars.get("SPY", [])
        if not spy or (self.today - spy[-1].day).days > 5:
            self.log("alert", "market data stale or missing", spy[-1].day.isoformat() if spy else None)
            info["skip"] = "stale data"
            return info
        info["considered"] = True
        if not market_filter_ok(spy, s.market_filter_ma):
            info["skip"] = f"SPY below {s.market_filter_ma}-day average"
            return info
        pending = [o for o in open_orders if v(o.side) == "buy" and v(o.status) in ACTIVE]
        pending_syms = {o.symbol for o in pending}
        pending_usd = sum(float(o.qty or 0) * float(o.limit_price or 0) for o in pending)
        today = self.today.isoformat()
        touched = {o["symbol"] for o in self.journal.orders_on(today)
                   if o["purpose"] in ("entry", "exit")}
        free = s.max_positions - len(positions) - len(pending_syms - set(positions))
        candidates = rank_candidates(bars, s.strategy, set(positions) | pending_syms | touched)
        info.update(free_slots=max(free, 0), candidates=candidates[:10], bought=[])
        for sym in candidates:
            if free <= 0:
                break
            budget = slot_budget(equity=equity, cash=cash, pending_buys=pending_usd, s=s)
            if budget < s.min_order_usd:
                info["stopped"] = f"cash: ${budget:.2f} left for a new position"
                break
            hist = bars.get(sym, [])
            try:
                price = self.broker.last_price(sym)
            except Exception:
                continue
            if not hist or price <= 0 or abs(price / hist[-1].close - 1) > s.max_price_jump:
                self.log("info", "price failed sanity check", {"symbol": sym, "price": price})
                continue
            limit = round(price * (1 + s.limit_buffer), 2)
            qty = qty_down(budget / limit)
            if qty <= 0:
                continue
            cid = client_id(self.today, sym, "entry")
            if not self.trade:
                self.log("info", "dry run: would buy", {"symbol": sym, "qty": str(qty), "limit": limit,
                                                         "usd": round(float(qty) * limit, 2)})
            else:
                try:
                    o = self.broker.buy_limit(sym, qty, limit, cid)
                except Exception as exc:
                    self.log("alert", "buy failed", {"symbol": sym,
                                                     "error": f"{type(exc).__name__}: {exc}"[:200]})
                    continue
                self.journal.record_order(client_order_id=cid, day=today, symbol=sym, side="buy",
                                          kind="limit", qty=str(qty), price=limit, purpose="entry",
                                          broker_id=str(o.id), status=v(o.status))
                self.log("trade", "BUY", {"symbol": sym, "qty": str(qty), "limit": limit,
                                          "usd": round(float(qty) * limit, 2),
                                          "strategy": s.strategy})
            info["bought"].append(sym)
            pending_usd += float(qty) * limit
            free -= 1
        return info


def _account_hash(account) -> str:
    import hashlib
    from uuid import UUID
    try:
        normalized = str(UUID(str(account.id))).lower()
    except (ValueError, TypeError, AttributeError):
        raise TradeBlocked("Authenticated PAPER account identity unavailable") from None
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


class SingleRun:
    """Cross-process lock so two runs can never overlap (duplicate orders)."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            import msvcrt
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise TradeBlocked("Another autopilot run is in progress")
        return self

    def __exit__(self, *exc):
        try:
            import msvcrt
            self.fh.seek(0)
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        self.fh.close()


def _notify(title, message, priority="default"):
    topic = os.environ.get("PAPER_MONITOR_NTFY_TOPIC")
    if not topic:
        return False
    try:
        from .monitor_notify import send_activity_ntfy
        return send_activity_ntfy(f"HDS {title}", message, topic=topic, priority=priority)
    except Exception:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trade", action="store_true", help="send orders (default: dry run)")
    ap.add_argument("--folder", type=Path, default=RUNTIME)
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    receipt = a.folder / "autopilot-runtime.json"
    record = {"schema": 1, "at": now.isoformat(), "status": "error"}
    try:
        for name in ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY", "PAPER_ACCOUNT_SHA256"):
            if not os.environ.get(name):
                raise TradeBlocked("Required PAPER runtime configuration missing")
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient
        key, secret = os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
        trading = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
        if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
            raise TradeBlocked("Client is not Alpaca PAPER")
        with SingleRun(a.folder / "autopilot.lock"):
            journal = Journal(a.folder / "autopilot.sqlite")
            try:
                run = Run(broker=Broker(trading, StockHistoricalDataClient(key, secret)),
                          journal=journal, settings=load_settings(a.folder), now=now,
                          trade=a.trade, notify=_notify, folder=a.folder)
                record = run.execute(os.environ["PAPER_ACCOUNT_SHA256"])
            finally:
                journal.close()
    except Exception as exc:
        record.update(error_type=type(exc).__name__, error=str(exc)[:300])
        _notify("autopilot error", f"{type(exc).__name__}: {str(exc)[:200]}", "high")
    tmp = receipt.with_suffix(".tmp")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, receipt)
    for r in record.get("positions", []):
        print(f"{r['symbol']}: qty {r['qty']} entry {r['entry']} now {r['price']} "
              f"P/L {r.get('pnl_pct')}% stop {r.get('stop')} session {r.get('sessions')}"
              + (f" EXIT {r['exit']}" if r.get("exit") else ""))
    print("entries:", json.dumps(record.get("entries"), default=str))
    for e in record.get("events", []):
        print(f"[{e['level']}] {e['kind']}: {e['detail']}")
    print(f"AUTOPILOT: {record.get('status')} trade={a.trade} "
          f"market_open={record.get('market_open')} equity={record.get('equity')}"
          + (f" ERROR {record.get('error_type')}: {record.get('error')}" if record.get("error_type") else ""))
    return 0 if record.get("status") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
