"""Exit-only PAPER position manager. It can SELL bot positions; it can never BUY.

Runs every few minutes from its own Windows task (ops/windows/
install-paper-exit-task.ps1), independent of the entry executor, so open
positions are managed even while entries are paused or the executor task is
disabled (incident 2026-11-16, WMT held past its time limit).

Each run: verify the pinned PAPER account; read clock, positions and open
orders; assess every bot position (entry, bid, gain/loss %, high water,
take-profit / trailing / stop levels, sessions held); apply at most ONE exit
via ``paper_exit_manager.execute_exit``; write an atomic receipt that the
release preflight reads.

Default is DRY RUN (decides and logs, no broker writes). Real SELLs need
``--release`` on the command line; the task installer adds it only when the
owner asks for it.

Exit codes: 0 ok (all positions managed), 3 a position is not managed (no
journal intent or no live stop), 2 blocked/error.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from .paper_safety import PAPER_ENDPOINT, TradeBlocked

SCHEMA = 1


def _atomic_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                            suffix=".tmp") as f:
        json.dump(record, f, indent=2, default=str)
        temp = Path(f.name)
    os.replace(temp, path)


def _read_approved(monitor, cache_path):
    """Approved stop intents from the Neon journal, cached locally after every
    good read. If Neon is unreachable (outage, quota), fall back to the last
    good cache so exits keep running. Returns (rows, source)."""
    if monitor is not None:
        try:
            rows = monitor.read_approved_stop_intents(limit=501)
            if cache_path is not None:
                _atomic_json(cache_path, {"saved_at_utc": datetime.now(timezone.utc).isoformat(),
                                          "rows": [[str(v) for v in r] for r in rows]})
            return rows, "journal"
        except Exception:
            pass
    if cache_path is not None and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return [tuple(r) for r in data.get("rows", [])], "cache"
    return [], "none"


def _broker_rows(orders, approved):
    """Filled bot entries (client id 'hds-') seen at the broker but missing from
    the approved rows: their own live OTO stop leg is the durable stop. Used
    only when the journal is unreachable."""
    from .paper_exit_manager import value
    known = {str(r[0]) for r in approved}
    rows = []
    for o in orders:
        cid = str(getattr(o, "client_order_id", "") or "")
        if (not cid.startswith("hds-") or cid in known or value(getattr(o, "side", None)) != "buy"
                or value(getattr(o, "status", None)) != "filled"):
            continue
        stops = [l for l in (getattr(o, "legs", None) or [])
                 if value(getattr(l, "side", None)) == "sell"
                 and value(getattr(l, "type", None)) == "stop" and getattr(l, "stop_price", None)]
        if len(stops) == 1:
            rows.append((cid, "filled", getattr(o, "symbol", None), str(getattr(o, "filled_qty", "")),
                         str(stops[0].stop_price), str(getattr(o, "id", "")), "gtc"))
    return rows


def manage_once(*, trading, market_data, monitor, now, released, notify=None, cache_path=None):
    """One pass over open positions. Pure orchestration over injected clients."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from .paper_exit_manager import (
        ExitAction, _ensure_protected, bot_positions, execute_exit, market_snapshot, money,
        plan_exits, position_assessment, value,
    )
    from .paper_reconcile import orders_with_recent_parents

    clock = trading.get_clock()
    market_open = getattr(clock, "is_open", None) is True
    positions = trading.get_all_positions()
    orders = trading.get_orders(filter=GetOrdersRequest(
        status=QueryOrderStatus.OPEN, nested=True, limit=500))
    record = {"schema": SCHEMA, "observed_at_utc": now.isoformat(), "market_open": market_open,
              "released": bool(released), "positions": [], "unmanaged_symbols": [],
              "exits": [], "status": "ok"}
    if not positions:
        record["status"] = "flat"
        return record
    with_parents, _ = orders_with_recent_parents(trading, orders, now=now)
    approved, source = _read_approved(monitor, cache_path)
    if source != "journal":
        approved = list(approved) + _broker_rows(with_parents, approved)
        record["journal"] = f"unavailable; using {source} + broker orders"
    market, managed, unprotected = {}, set(), {}
    for position, cid, stop, parent, leg in bot_positions(
            positions=positions, orders=with_parents, approved_stops=approved):
        managed.add(position.symbol)
        filled_at = getattr(parent, "filled_at", None)
        entry = getattr(parent, "filled_avg_price", None)
        row = {"symbol": position.symbol, "shares": str(position.qty), "client_order_id": cid,
               "stop_order_id": str(leg.id)}
        if value(getattr(leg, "status", None)) == "canceled":
            row["protected"] = False
            unprotected[position.symbol] = ExitAction(
                client_order_id=cid, symbol=position.symbol, shares=money(position.qty),
                stop_leg_id=str(leg.id), durable_stop=stop, reason="reprotect")
        if filled_at is None or market_data is None:
            row["assessment_error"] = "entry fill time unavailable"
        else:
            try:
                snap = market_snapshot(market_data=market_data, trading=trading,
                                       symbol=position.symbol, filled_at=filled_at, now=now,
                                       position_price=getattr(position, "current_price", None))
                if entry:
                    snap["entry"] = entry
                market[position.symbol] = snap
                row.update(position_assessment(
                    entry=entry or 0, bid=snap["bid"], high_water=snap["high_water"],
                    durable_stop=stop, sessions_held=snap["sessions_held"]))
                row["price_ok"] = snap["price_ok"]
                for k in ("price_problem", "quote_note"):
                    if snap.get(k):
                        row[k] = snap[k]
                if not snap["price_ok"]:
                    row["pnl_pct"] = None   # never report P/L from a bad price
            except Exception as exc:
                row["assessment_error"] = type(exc).__name__
        record["positions"].append(row)
    record["unmanaged_symbols"] = sorted(
        {p.symbol for p in positions} - managed)
    actions = plan_exits(positions=positions, orders=with_parents, approved_stops=approved,
                         now=now, market=market, market_open=market_open)
    for action in actions[:1]:     # one exit per run; the next run re-reads state
        try:
            result = execute_exit(trading, action, released=released)
        except TradeBlocked as exc:
            result = {"result": "blocked", "reason": action.reason, "symbol": action.symbol,
                      "error": str(exc)}
            record["status"] = "blocked"
            if notify:
                notify("exit blocked", f"{action.symbol} {action.reason}: {exc}", "urgent")
        result.setdefault("symbol", action.symbol)
        result.setdefault("reason", action.reason)
        result["detail"] = action.detail
        record["exits"].append(result)
        if notify and result.get("result") in ("exit_filled", "exit_submitted"):
            notify("position closed", f"{action.reason}: SELL {result.get('shares')} "
                   f"{action.symbol} fill {result.get('fill_price')} ({action.detail})", "high")
        if result.get("result") in ("exit_filled", "exit_submitted", "exit_pending",
                                    "exit_already_exists", "already_flat",
                                    "stop_filled_during_exit"):
            unprotected.pop(action.symbol, None)
    # A held position whose stop was canceled and that was not sold this run
    # gets a fresh GTC stop at its durable level (never left unprotected).
    for symbol, guard in sorted(unprotected.items()):
        if not released:
            record["exits"].append({"result": "dry_run", "reason": "reprotect", "symbol": symbol})
            record["status"] = "blocked"
            continue
        try:
            _ensure_protected(trading, guard)
            record["exits"].append({"result": "reprotected", "reason": "reprotect",
                                    "symbol": symbol, "stop": str(guard.durable_stop)})
        except Exception as exc:
            record["exits"].append({"result": "blocked", "reason": "reprotect", "symbol": symbol,
                                    "error": f"{type(exc).__name__}: {exc}"[:200]})
            if notify:
                notify("UNPROTECTED position", f"{symbol}: re-protect failed", "urgent")
        record["status"] = "blocked"
    if record["unmanaged_symbols"]:
        record["status"] = "unmanaged"
        if notify:
            notify("unmanaged position", "No live stop or journal intent: "
                   + ",".join(record["unmanaged_symbols"]), "urgent")
    return record


def _notify(title, message, priority="default"):
    topic = os.environ.get("PAPER_MONITOR_NTFY_TOPIC")
    if not topic:
        return False
    try:
        from .monitor_notify import send_activity_ntfy
        return send_activity_ntfy(title, message, topic=topic, priority=priority)
    except Exception:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--release", action="store_true",
                        help="actually submit exit orders (default: dry run)")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        for name in ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
                     "PAPER_JOURNAL_DSN", "PAPER_ACCOUNT_SHA256"):
            if not os.environ.get(name):
                raise TradeBlocked("Required PAPER runtime configuration missing")
        from alpaca.data.historical import StockHistoricalDataClient
        from .paper_broker_transport import make_supervised_paper_client
        from .paper_executor_service import _account_hash
        from .paper_store import PostgresPaperJournal
        key, secret = os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
        trading = make_supervised_paper_client(key, secret)
        if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
            raise TradeBlocked("Client is not Alpaca PAPER")
        if _account_hash(trading.get_account()) != os.environ["PAPER_ACCOUNT_SHA256"]:
            raise TradeBlocked("Account does not match pinned PAPER identity")
        try:
            monitor = PostgresPaperJournal(os.environ["PAPER_JOURNAL_DSN"])
            monitor.assert_monitor_only()
        except Exception:
            monitor = None   # Neon down: exits continue from cache + broker state
        record = manage_once(trading=trading, market_data=StockHistoricalDataClient(key, secret),
                             monitor=monitor, now=now, released=args.release, notify=_notify,
                             cache_path=args.receipt.parent / "approved-stops-cache.json")
    except Exception as exc:
        record = {"schema": SCHEMA, "observed_at_utc": now.isoformat(), "status": "error",
                  "error_type": type(exc).__name__,
                  "error": str(exc)[:200] if isinstance(exc, TradeBlocked) else None}
    _atomic_json(args.receipt, record)
    try:
        folder = args.receipt.parent
        with open(folder / "exits.jsonl", "a", encoding="utf-8") as f:
            for e in record.get("exits", []):
                f.write(json.dumps({"at": now.isoformat(), **e}, default=str) + "\n")
    except Exception:
        pass
    for p in record.get("positions", []):
        print(f"{p['symbol']}: entry {p.get('entry')} bid {p.get('bid')} "
              f"P/L {p.get('pnl_pct')}% | take-profit {p.get('take_profit_at')} "
              f"trail {p.get('trail_at')}{'' if p.get('trail_active') else ' (inactive)'} "
              f"stop {p.get('stop_at')} | session {p.get('sessions_held')}/{p.get('max_sessions')}")
        if p.get("price_problem"):
            print(f"  PRICE UNRELIABLE, price exits skipped: {p['price_problem']}")
    for e in record.get("exits", []):
        print(f"EXIT {e.get('result')}: {e.get('symbol')} {e.get('reason')} {e.get('detail')}")
    print(f"EXIT SERVICE: {record['status']} (market_open={record.get('market_open')}, "
          f"released={record.get('released')})")
    return {"ok": 0, "flat": 0, "unmanaged": 3}.get(record["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
