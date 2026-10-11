"""SQLite research journal. No account secrets or broker orders stored."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  close REAL NOT NULL,
  change_pct REAL NOT NULL,
  relative_volume REAL NOT NULL,
  score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_time ON observations(observed_at);
"""

def record_scan(path: str, observed_at: str, candidates) -> int:
    db = Path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO observations(observed_at,symbol,close,change_pct,relative_volume,score) VALUES (?,?,?,?,?,?)",
            [(observed_at, c.symbol, c.close, c.change_pct, c.relative_volume, c.score) for c in candidates]
        )
    return len(candidates)
