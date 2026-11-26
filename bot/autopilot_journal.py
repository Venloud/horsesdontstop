"""Local SQLite journal for the autopilot (replaces Neon, 2026-11-26).

Lives in %LOCALAPPDATA%\\horsesdontstop\\autopilot.sqlite. Stdlib only, no
server, no network. Holds open-position metadata, every order the autopilot
sent, closed trades (for the scoreboard), equity snapshots and events.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY, opened_on TEXT NOT NULL, entry_price REAL NOT NULL,
  qty TEXT NOT NULL, strategy TEXT NOT NULL, stop REAL NOT NULL,
  high_water REAL NOT NULL, adopted INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL,
  symbol TEXT NOT NULL, side TEXT NOT NULL, kind TEXT NOT NULL, qty TEXT,
  price REAL, purpose TEXT NOT NULL, broker_id TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, strategy TEXT,
  opened_on TEXT, closed_at TEXT NOT NULL, entry_price REAL, exit_price REAL,
  qty TEXT, pnl REAL, return_pct REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS equity (
  at TEXT PRIMARY KEY, equity REAL NOT NULL, cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, level TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def _exec(self, sql, args=()):
        with self._conn:
            return self._conn.execute(sql, args)

    # positions ---------------------------------------------------------
    def positions(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return {r["symbol"]: dict(r) for r in rows}

    def upsert_position(self, p: dict):
        self._exec(
            "INSERT INTO positions (symbol, opened_on, entry_price, qty, strategy, stop, "
            "high_water, adopted, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET opened_on=excluded.opened_on, "
            "entry_price=excluded.entry_price, qty=excluded.qty, strategy=excluded.strategy, "
            "stop=excluded.stop, high_water=excluded.high_water, adopted=excluded.adopted, "
            "updated_at=excluded.updated_at",
            (p["symbol"], p["opened_on"], float(p["entry_price"]), str(p["qty"]),
             p["strategy"], float(p["stop"]), float(p["high_water"]),
             int(bool(p.get("adopted"))), _now()))

    def remove_position(self, symbol: str):
        self._exec("DELETE FROM positions WHERE symbol=?", (symbol,))

    # orders ------------------------------------------------------------
    def record_order(self, *, client_order_id, day, symbol, side, kind, qty, price, purpose,
                     broker_id=None, status=None, detail=None):
        self._exec(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (client_order_id, _now(), day, symbol, side, kind,
             None if qty is None else str(qty), price, purpose, broker_id, status,
             None if detail is None else str(detail)[:500]))

    def orders_on(self, day: str, purpose: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM orders WHERE day=?", [day]
        if purpose:
            sql += " AND purpose=?"
            args.append(purpose)
        return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # trades / equity / events -------------------------------------------
    def record_trade(self, *, symbol, strategy, opened_on, entry_price, exit_price, qty, reason):
        q = float(qty)
        pnl = (float(exit_price) - float(entry_price)) * q
        ret = float(exit_price) / float(entry_price) - 1 if entry_price else None
        self._exec(
            "INSERT INTO trades (symbol, strategy, opened_on, closed_at, entry_price, exit_price, "
            "qty, pnl, return_pct, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol, strategy, opened_on, _now(), float(entry_price), float(exit_price),
             str(qty), round(pnl, 4), ret, reason))

    def trades(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]

    def record_equity(self, equity: float, cash: float):
        self._exec("INSERT OR REPLACE INTO equity VALUES (?,?,?)", (_now(), equity, cash))

    def equity_series(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM equity ORDER BY at").fetchall()]

    def event(self, level: str, kind: str, detail=None):
        if not isinstance(detail, str) and detail is not None:
            detail = json.dumps(detail, default=str)
        self._exec("INSERT INTO events (at, level, kind, detail) VALUES (?,?,?,?)",
                   (_now(), level, kind, detail))

    def events(self, limit=50) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def open_journal(path: Path | str) -> Journal:
    return Journal(path)


__all__ = ["Journal", "open_journal", "closing"]
