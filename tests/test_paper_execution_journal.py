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
        if sql.startswith("SET LOCAL"):
            self.last = None
        elif "paper_acl_boundary" in sql:
            self.last = (True,)
        elif "pg_advisory_xact_lock" in sql:
            self.last = (None,)
        elif "pg_has_role(current_user" in sql:
            self.last = self.db["roles"]
        elif "SELECT kill_switch_active" in sql:
            self.last = self.db["controls"]
        elif "SELECT COUNT(*) FROM public.paper_intents" in sql:
            self.last = (sum(1 for row in self.db["days"] if row == params[0]),)
        elif "SELECT public.paper_reserve_intent_if_enabled" in sql:
            cid = params[0]
            if self.db["controls"] != (False, True):
                raise TradeBlocked("Paper kill switch active or not approved")
            if params[3] in self.db["days"]:
                raise TradeBlocked("Only one protected PAPER order intent per NY trading day")
            if cid in self.db["keys"]:
                self.last = (False,)
            else:
                self.db["keys"].add(cid)
                self.db["days"].append(params[3])
                self.db["intent_status"][cid] = "reserved"
                self.last = (True,)
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
        elif "SELECT public.paper_authorize_unknown_submission(" in sql:
            if self.db["controls"] != (False, True):
                raise TradeBlocked("Final control check blocked")
            assert self.db["intent_status"][params[0]] == "unknown"
            self.last = (True,)
        elif "SELECT public.paper_consume_submission_authorization(" in sql:
            cid = params[0]
            if any(a == (cid, "submission_authorization_consumed") for a in self.db["audits"]):
                raise TradeBlocked("Authorization already consumed")
            self.db["audits"].append((cid, "submission_authorization_consumed"))
            self.last = (True,)
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
        "roles": (permissions if permissions is not None else
                  (False, False, False, False, False, False, False, False,
                   False, True, True, True, False, False, False, True,
                   True, False, False, False, False, False, False, False)),
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
    assert any("paper_reserve_intent_if_enabled" in sql for sql, _ in db["queries"])
    assert not any("INSERT INTO public.paper_intents" in sql
                   for sql, _ in db["queries"])


@pytest.mark.parametrize("blocked_index", list(range(24)))
def test_privileged_or_incomplete_writer_role_refused(monkeypatch, blocked_index):
    baseline = (False, False, False, False, False, False, False, False,
                False, True, True, True, False, False, False, True,
                True, False, False, False, False, False, False, False)
    changed = list(baseline)
    changed[blocked_index] = not baseline[blocked_index]
    j, db = journal(monkeypatch, allow=True, permissions=tuple(changed))
    with pytest.raises(TradeBlocked, match="role"):
        j.reserve_if_enabled(sample())
    assert db["keys"] == set()


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
                    permissions=(False,) * 24)
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
    assert sum("SELECT public.paper_reserve_intent_if_enabled(" in sql
               for sql, _ in db["queries"]) == 2


def test_final_authorization_consumes_reservation_before_yield_and_never_reuses(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    plan = sample()
    j.reserve_if_enabled(plan)
    with j.authorize_reserved_submission(plan):
        assert db["intent_status"][plan.client_order_id] == "unknown"
        assert len(db["audits"]) == 2
        with pytest.raises(TradeBlocked, match="already consumed"):
            j.consume_submission_authorization(plan.client_order_id)
    with pytest.raises(TradeBlocked):
        with j.authorize_reserved_submission(plan):
            pytest.fail("Reservation reused after attempted submission")


def test_kill_change_after_reservation_blocks_final_authorization(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    plan = sample()
    j.reserve_if_enabled(plan)
    db["controls"] = (True, False)
    with pytest.raises(TradeBlocked, match="Final control"):
        with j.authorize_reserved_submission(plan):
            pytest.fail("Disabled control permitted POST")
    assert db["intent_status"][plan.client_order_id] == "unknown"


def test_restricted_reservation_requires_privileged_row_lock_guard(monkeypatch):
    from pathlib import Path
    j, db = journal(monkeypatch, allow=False)
    with pytest.raises(TradeBlocked, match="kill switch"):
        j.reserve_if_enabled(sample())
    checks = [sql for sql, _ in db["queries"]
              if "SELECT public.paper_reserve_intent_if_enabled(" in sql]
    assert len(checks) == 1
    assert "INSERT INTO public.paper_intents" not in checks[0]
    assert "FOR SHARE" not in checks[0]
    assert not any("UPDATE public.paper_control" in sql for sql, _ in db["queries"])
    sqlfile = (Path(__file__).resolve().parents[1] / "sql" /
               "paper_reservation_control_guard_review_only.sql")
    migration = sqlfile.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in migration
    assert "CREATE ROLE hds_paper_reservation_guard NOLOGIN NOINHERIT" in migration
    assert "GRANT SELECT, UPDATE ON TABLE public.paper_control" in migration
    assert "TO hds_paper_reservation_guard" in migration
    assert "OWNER TO hds_paper_reservation_guard" in migration
    assert "REVOKE CREATE ON SCHEMA public FROM hds_paper_reservation_guard" in migration
    assert "GRANT hds_paper_reservation_guard TO hds_paper_executor" not in migration
    assert "CREATE UNIQUE INDEX uq_paper_intents_trading_day" in migration
    assert "REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor" in migration
    assert "REVOKE EXECUTE ON FUNCTION public.paper_reservation_control_guard()" in migration
    assert "paper_reserve_intent_if_enabled" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "SET search_path = pg_catalog" in migration
    assert "FOR SHARE" in migration
    assert "REVOKE ALL ON FUNCTION public.paper_reservation_control_guard() FROM PUBLIC" in migration
    assert "TO hds_paper_executor" in migration
    assert "UPDATE public.paper_control" not in migration
    assert "GRANT UPDATE ON public.paper_control" not in migration


@pytest.mark.parametrize("controls", [
    None, (None, None), (True, False), (False, False), (True, True),
])
def test_guard_missing_or_inconsistent_control_row_fails_closed(monkeypatch, controls):
    j, db = journal(monkeypatch, allow=True)
    db["controls"] = controls
    with pytest.raises(TradeBlocked, match="kill switch"):
        j.reserve_if_enabled(sample())
    assert db["keys"] == set()
    assert not any("INSERT INTO public.paper_intents" in statement
                   for statement, _ in db["queries"])


def test_guard_permission_loss_blocks_intent_creation(monkeypatch):
    j, db = journal(monkeypatch, allow=True)

    class DeniedGuardCursor(Cursor):
        def execute(self, sql, params=None):
            if "SELECT public.paper_reserve_intent_if_enabled(" in sql:
                raise PermissionError("guard execution permission revoked")
            return super().execute(sql, params)

    class DeniedGuardConn(Conn):
        def cursor(self):
            return DeniedGuardCursor(db)

    monkeypatch.setattr(j, "_connect", lambda: DeniedGuardConn(db))
    with pytest.raises(PermissionError, match="permission revoked"):
        j.reserve_if_enabled(sample())
    assert db["keys"] == set()
    assert not any("INSERT INTO public.paper_intents" in statement
                   for statement, _ in db["queries"])


def test_review_only_definer_rejects_nonfinite_prices_and_raw_insert():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "sql" /
           "paper_reservation_control_guard_review_only.sql").read_text()
    assert "p_expected_entry::text IN ('NaN','Infinity','-Infinity')" in sql
    assert "p_protective_stop::text IN ('NaN','Infinity','-Infinity')" in sql
    assert "REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor" in sql
    assert "CREATE UNIQUE INDEX uq_paper_intents_trading_day" in sql
    assert "SET search_path TO 'pg_catalog'" in sql or "SET search_path = pg_catalog" in sql


def test_duplicate_day_sqlstate_23505_is_not_a_false_or_success(monkeypatch):
    """Live SQL RAISE 23505 must fail closed, not masquerade as False."""
    j, db = journal(monkeypatch, allow=True)

    class DuplicateDayViolation(Exception):
        sqlstate = "23505"

    class DuplicateCursor(Cursor):
        def execute(self, sql, params=None):
            if "SELECT public.paper_reserve_intent_if_enabled(" in sql:
                self.db["queries"].append((sql, params))
                raise DuplicateDayViolation("synthetic unique-day violation")
            return super().execute(sql, params)

    class DuplicateConn(Conn):
        def cursor(self):
            return DuplicateCursor(db)

    monkeypatch.setattr(j, "_connect", lambda: DuplicateConn(db))
    with pytest.raises(DuplicateDayViolation) as exc:
        j.reserve_if_enabled(sample())
    assert exc.value.sqlstate == "23505"
    assert not db["keys"]
    assert not db["days"]
    assert not db["audits"]


def test_same_authorization_connection_loss_is_detected_before_send(monkeypatch):
    j, db = journal(monkeypatch, allow=True)
    plan = sample(); j.reserve_if_enabled(plan)
    original = Cursor.execute
    def interrupted(self, sql, params=None):
        if db.get("backend_dead") and "SELECT kill_switch_active" in sql:
            raise ConnectionError("lost authorization backend")
        return original(self, sql, params)
    monkeypatch.setattr(Cursor, "execute", interrupted)
    with j.authorize_reserved_submission(plan) as guard:
        guard()
        db["backend_dead"] = True
        with pytest.raises(ConnectionError):
            guard()
    assert db["intent_status"][plan.client_order_id] == "unknown"
    assert (plan.client_order_id,"submission_authorization_consumed") in db["audits"]
