"""Test journal semantics with an in-memory DB stub, never real credentials.

Integration testing against a real TLS PostgreSQL deployment is still needed.
"""
import pytest

from bot.paper_safety import TradeBlocked, plan_paper_entry
from bot.paper_store import (
    PostgresPaperJournal, SCHEMA, transition_allowed, validate_postgres_url,
)
from datetime import date


def test_transition_table_blocks_final_state_mutation():
    assert transition_allowed("reserved", "submitted")
    assert transition_allowed("reserved", "unknown")
    assert transition_allowed("unknown", "filled")
    assert not transition_allowed("reserved", "filled")
    assert not transition_allowed("filled", "submitted")
    assert not transition_allowed("filled", "filled")
    assert not transition_allowed("rejected", "submitted")


def test_tls_postgres_only_and_not_connected_by_constructor():
    with pytest.raises(TradeBlocked):
        PostgresPaperJournal("sqlite:///foo")
    with pytest.raises(TradeBlocked):
        PostgresPaperJournal("")
    # Constructor accepts DSN but does not connect or submit anything.
    assert validate_postgres_url("postgresql://fake.invalid/research").startswith(
        "postgresql://")


def test_schema_defaults_to_kill_switch_on_with_unique_order_ids():
    assert "client_order_id TEXT PRIMARY KEY" in SCHEMA
    assert "ON CONFLICT (singleton) DO NOTHING" in SCHEMA
    assert "kill_switch_active BOOLEAN NOT NULL DEFAULT TRUE" in SCHEMA
    assert "submissions_enabled BOOLEAN NOT NULL DEFAULT FALSE" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS paper_equity_snapshots" in SCHEMA


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.reply = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None, **_):
        if "INSERT INTO paper_intents" in sql:
            key = params[0]
            if key in self.state["keys"]:
                self.reply = None
            else:
                self.state["keys"].add(key)
                self.reply = (key,)
        elif "SELECT kill_switch_active" in sql:
            self.reply = (True, False)
        elif "SELECT status FROM paper_intents" in sql:
            self.reply = ("reserved",)
        else:
            self.reply = None

    def fetchone(self):
        return self.reply


class FakeConnection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self.state)


def test_legacy_direct_intent_reservation_fails_before_any_sql(monkeypatch):
    db = PostgresPaperJournal("postgresql://fake.invalid/research")
    def no_connection():
        raise AssertionError("Legacy direct INSERT must never contact database")
    monkeypatch.setattr(db, "_connect", no_connection)
    plan = plan_paper_entry("F", date(2026, 10, 13), 25, 500, 500, .01)
    with pytest.raises(TradeBlocked, match="Direct PAPER intent INSERT forbidden"):
        db.reserve_intent(plan)


def test_legacy_status_observation_cannot_forge_fill(monkeypatch):
    db = PostgresPaperJournal("postgresql://fake.invalid/research")
    def no_connection():
        raise AssertionError("Legacy status writer must never contact database")
    monkeypatch.setattr(db, "_connect", no_connection)
    with pytest.raises(TradeBlocked, match="Direct PAPER status"):
        db.record_observed_state("fabricated", "filled", "fabricated-broker")
