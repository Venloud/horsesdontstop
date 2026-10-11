from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from bot.scanner import rank_bars
from bot.journal import record_scan

def test_ranking_and_journal(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [SimpleNamespace(timestamp=start + timedelta(days=i), close=100.0,
                            volume=100) for i in range(11)]
    rows.append(SimpleNamespace(timestamp=start + timedelta(days=11), close=105.0, volume=200))
    candidate = rank_bars("TEST", rows)
    assert candidate.change_pct == 5.0
    assert candidate.relative_volume == 2.0
    assert candidate.score == 10.0
    path = tmp_path / "journal.sqlite3"
    assert record_scan(str(path), start.isoformat(), [candidate]) == 1

def test_insufficient_history():
    assert rank_bars("TEST", []) is None
