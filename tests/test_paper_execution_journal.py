"""Offline tests of Neon writer safety. No real DB, no orders."""
from dataclasses import replace
from datetime import date

import pytest

from bot.paper_execution_journal import PaperExecutorJournal
from bot.paper_safety import TradeBlocked, plan_paper_entry


DAY = date(2026, 10, 12)


def sample():
    return plan_paper_entry("F", DAY, 25, 500, 500, .01)


class Cursor:
    def __init__(self, db):
        self.db = db
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.db["queries"].append((sql, params))
        if sql.startswith("SET LOCAL statement_timeout"):
            self.last = None
        elif "pg_advisory_xact_lock" in sql:
            self.last = (None,)
        elif "pg_has_role(current_user" in sql:
            self.last = self.db["roles"]
        elif "SELECT kill_switch_active" in sql:
            self.last = self.db["controls"]
        elif "SELECT COUNT(*) FROM public.paper_intents" in sql:
            self.last = (sum(1 for row in self.db["days"] if row == params[0]),)
        elif "INSERT INTO public.paper_intents" in sql:
            cid = params[0]
            if cid in self.db["keys"]:
                self.last = None
            else:
                self.db["keys"].add(cid)
                self.db["days"].append(params[3])
                self.db["intent_status"][cid] = "reserved"
                self.last = (cid,)
        elif "SELECT public.paper_mark_reserved_intent_unknown(" in sql:
            cid = params[0]
            if self.db["intent_status"].get(cid) == "reserved":
                self.db["intent_status"][cid] = "unknown"
                # Simulate single SQL function transaction, not two
                # independently granted direct status/audit writes.
                self.db["audits"].append(
                    (cid, "submission_outcome_unknown"))
                self.last = (True,)
            else:
                raise TradeBlocked("Cannot mark nonreserved intent unknown")
        else:
            raise AssertionError("Unexpected SQL: " + sql)

    def fetchone(self):
        return self.last


class Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return Cursor(self.db)


def journal(monkeypatch, *, allow=False, permissions=None):
    db = {
        "roles": permissions or (False, False, False, True, True, False, True, True),
        "controls": (False, True) if allow else (True, False),
        "queries": [], "keys": set(), "days": [], "intent_status": {}, "audits": [],
    }
    j = PaperExecutorJournal("postgresql://fake.invalid/neondb")
    monkeypatch.setattr(j, "_connect", lambda: Conn(db))
    return j, db


def test_production_kill_switch_blocks_reservation(monkeypatch):
    j, db = journal(monkeypatch, allow=False)
    with pytest.raises(TradeBlocked, match="kill switch"):
        j.reserve_if_enabled(sample())
    assert not any("INSERT INTO public.paper_intents" in sql
                   for sql, _ in db["queries"])
    assert db["keys"] == set()


def test_atomic_unique_reservation_stops_duplicate_order_id(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    plan = sample()
    assert j.reserve_if_enabled(plan) is True
    with pytest.raises(TradeBlocked, match="Only one"):
        j.reserve_if_enabled(plan)
    assert db["keys"] == {plan.client_order_id}
    assert any("FOR SHARE" in sql for sql, _ in db["queries"])


@pytest.mark.parametrize("permissions", [
    (True, False, False, True, True, False, True, True),
    (False, True, False, True, True, False, True, True),
    (False, False, True, True, True, False, True, True),
    (False, False, False, True, True, True, True, True),
    (False, False, False, False, True, False, True, True),
])
def test_privileged_or_incomplete_writer_role_refused(monkeypatch, permissions):
    j, db = journal(monkeypatch, allow=True, permissions=permissions)
    with pytest.raises(TradeBlocked, match="role"):
        j.reserve_if_enabled(sample())
    assert not db["keys"]


def test_order_intent_tampering_rejected_before_database_access(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    p = sample()
    for candidate in (
        replace(p, client_order_id="wrong"),
        replace(p, order_class="simple"),
        replace(p, estimated_risk_usd=100),
        replace(p, protective_stop=p.estimated_entry),
        replace(p, whole_shares=0),
    ):
        with pytest.raises(TradeBlocked):
            j.reserve_if_enabled(candidate)
    assert not db["queries"]


def test_unknown_order_submission_records_durable_no_retry(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    plan = sample()
    assert j.reserve_if_enabled(plan)
    j.mark_uncertain(plan.client_order_id)
    assert db["intent_status"][plan.client_order_id] == "unknown"
    assert len(db["audits"]) == 1
    sql_calls = [sql for sql, _params in db["queries"]]
    assert any("paper_mark_reserved_intent_unknown" in sql for sql in sql_calls)
    assert not any("UPDATE public.paper_intents" in sql
                   or "INSERT INTO public.paper_audit_events" in sql
                   for sql in sql_calls)
    with pytest.raises(TradeBlocked):
        j.mark_uncertain(plan.client_order_id)
    with pytest.raises(TradeBlocked, match="Only one"):
        j.reserve_if_enabled(plan)


def test_readonly_monitor_dsn_cannot_reserve(monkeypatch):
    j, db = journal(monkeypatch, allow=True,
                    permissions=(False, False, False, False,
                                 False, False, False, False))
    with pytest.raises(TradeBlocked, match="role"):
        j.reserve_if_enabled(sample())
    assert not db["keys"]


def test_second_symbol_same_day_rejected_by_account_lock(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    first = sample()
    other = plan_paper_entry("BAC", DAY, 25, 500, 500, .01)
    assert j.reserve_if_enabled(first)
    with pytest.raises(TradeBlocked, match="one protected PAPER order"):
        j.reserve_if_enabled(other)
    assert db["keys"] == {first.client_order_id}
    assert any("pg_advisory_xact_lock" in sql for sql, _ in db["queries"])
