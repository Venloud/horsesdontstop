"""Check journal queries without network, secrets or production mutations."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.paper_safety import TradeBlocked
from bot.paper_store import PostgresPaperJournal


class ReadCursor:
    def __init__(self, statements):
        self.statements = statements
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        assert sql.lstrip().upper().startswith("SELECT")
        self.statements.append((sql, params))
        if "bool_and(has_table_privilege" in sql:
            self.result = [(True, False, False, False, False, False)]
        elif "SELECT kill_switch_active" in sql:
            self.result = [(True, False)]
        elif "FROM paper_intents" in sql:
            self.result = [("hds-stale", "unknown", "F", 1)]
        elif "MAX(equity)" in sql:
            self.result = [(Decimal("521.25"),)]
        elif "FROM paper_equity_snapshots" in sql:
            self.result = [(Decimal("500.00"),)]
        else:
            raise AssertionError("Unexpected database SELECT")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return list(self.result)


class ReadConnection:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return ReadCursor(self.statements)


def test_neon_readers_issue_only_selects(monkeypatch):
    journal = PostgresPaperJournal("postgresql://host.invalid/neondb")
    statements = []
    monkeypatch.setattr(journal, "_connect", lambda: ReadConnection(statements))
    journal.assert_monitor_only()
    control = journal.read_control()
    assert control.kill_switch_active and not control.submissions_enabled
    assert journal.read_unresolved_intents(501) == [
        ("hds-stale", "unknown", "F", 1)]
    now = datetime(2026, 10, 12, 14, 31, tzinfo=timezone.utc)
    opening = datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc)
    start, peak = journal.read_equity_references(opening, now)
    assert start == Decimal("500.00")
    assert peak == Decimal("521.25")
    assert len(statements) == 5
    opening_statement = statements[3]
    assert "observed_at < %s" in opening_statement[0]
    assert opening_statement[1][1].minute == 35
    assert all(x[0].lstrip().startswith("SELECT") for x in statements)


def test_missing_or_oversized_intent_window_fails_without_query(monkeypatch):
    journal = PostgresPaperJournal("postgresql://host.invalid/neondb")
    def fail_connection():
        raise AssertionError("Must fail before connecting")
    monkeypatch.setattr(journal, "_connect", fail_connection)
    for limit in (0, 1001, -1):
        with pytest.raises(TradeBlocked):
            journal.read_unresolved_intents(limit)


def test_naive_day_open_rejected_even_in_preview(monkeypatch):
    journal = PostgresPaperJournal("postgresql://host.invalid/neondb")
    monkeypatch.setattr(journal, "_connect",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("No DB I/O expected")))
    with pytest.raises(TradeBlocked):
        journal.read_equity_references(
            datetime(2026, 10, 12, 9, 30),
            datetime(2026, 10, 12, 14, 31, tzinfo=timezone.utc))


def test_neon_admin_role_is_blocked_on_permission_preflight(monkeypatch):
    journal = PostgresPaperJournal("postgresql://host.invalid/neondb")
    statements = []
    class AdminCursor(ReadCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "bool_and(has_table_privilege" in sql:
                self.result = [(True, True, True, True, True, True)]
    class AdminConnection(ReadConnection):
        def cursor(self):
            return AdminCursor(self.statements)

    monkeypatch.setattr(journal, "_connect",
                        lambda: AdminConnection(statements))
    with pytest.raises(TradeBlocked, match="only SELECT"):
        journal.assert_monitor_only()
    assert len(statements) == 1
