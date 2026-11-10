"""Autonomous PAPER operation: monitoring, supervision, controls, recovery.

Synthetic inputs only; no broker POST, no live Neon.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.monitor_policy import assess_read_only_report
from bot.paper_reconcile import reconcile_observation
from bot.paper_safety import TradeBlocked

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def order(order_id, symbol="F", *, side="buy", kind="limit", qty=2, status="new",
          cid=None, legs=(), parent=None, tif="gtc", stop_price=None, filled=0,
          klass="simple"):
    return SimpleNamespace(
        id=order_id, symbol=symbol, side=side, type=kind, qty=str(qty),
        filled_qty=str(filled), status=status, time_in_force=tif,
        stop_price=stop_price, order_class=klass, client_order_id=cid,
        legs=list(legs), parent_order_id=parent)


def protected_position(stop_status="new", stop_price=24):
    stop = order("stop-leg", side="sell", kind="stop", status=stop_status,
                 parent="parent-1", stop_price=stop_price)
    parent = order("parent-1", status="filled", filled=2, cid="hds-c",
                   legs=(stop,), klass="oto")
    return reconcile_observation(
        [SimpleNamespace(symbol="F", qty="2")], [parent], [],
        order_fetch_complete=True,
        approved_stops=[("hds-c", "filled", "F", 2, 24, "parent-1", "gtc")])


# ---- reconciliation verdict ----

def test_verified_gtc_oto_stop_is_managed_exposure():
    rec = protected_position()
    assert rec.consistent is False           # exposure still blocks NEW entries
    assert rec.managed_exposure is True and rec.uncovered_symbols == ()


def test_wrong_stop_price_or_missing_stop_is_not_managed():
    assert protected_position(stop_price=23).managed_exposure is False
    assert protected_position(stop_status="canceled").managed_exposure is False


def test_accepted_entry_with_matching_open_parent_is_managed():
    stop = order("stop-leg", side="sell", kind="stop", status="held",
                 parent="parent-1", stop_price=24)
    parent = order("parent-1", status="accepted", cid="hds-c", legs=(stop,), klass="oto")
    rec = reconcile_observation([], [parent], [("hds-c", "accepted", "F", 2)],
                                order_fetch_complete=True, approved_stops=[])
    assert rec.managed_exposure is True


def test_unknown_or_unjournaled_orders_are_never_managed():
    parent = order("parent-1", status="accepted", cid="hds-c", klass="oto")
    unknown = reconcile_observation([], [parent], [("hds-c", "unknown", "F", 2)],
                                    order_fetch_complete=True, approved_stops=[])
    stranger = reconcile_observation([], [order("x", cid="manual")], [],
                                     order_fetch_complete=True, approved_stops=[])
    assert unknown.managed_exposure is False and stranger.managed_exposure is False


# ---- monitor classification ----

def report(rec=None, *, kill=True, enabled=False, blockers=()):
    rec = rec or reconcile_observation([], [], [], order_fetch_complete=True)
    return {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": NOW.isoformat(),
        "broker_paper_url": "https://paper-api.alpaca.markets",
        "market_open": True,
        "journal_kill_switch_active": kill, "journal_submissions_enabled": enabled,
        "code_submissions_enabled": False, "can_submit_orders": False,
        "broker_write_operations": 0,
        "safety_blockers": list(blockers) or ["paper_order_submission_disabled"],
        "reconciliation": rec.to_dict(),
    }


def test_armed_controls_are_a_healthy_state():
    a = assess_read_only_report(report(kill=False, enabled=True), now=NOW)
    assert (a.level, a.code) == ("idle", "idle")


@pytest.mark.parametrize("kill,enabled", [(False, False), (True, True), ("false", True)])
def test_incoherent_controls_are_critical(kill, enabled):
    a = assess_read_only_report(report(kill=kill, enabled=enabled), now=NOW)
    assert (a.level, a.code) == ("critical", "security_switch")


def test_protected_position_is_managed_not_critical():
    a = assess_read_only_report(report(
        protected_position(), kill=False, enabled=True,
        blockers=("existing_position_or_open_order", "broker_orders_not_reconciled")),
        now=NOW)
    assert (a.level, a.code) == ("idle", "managed_exposure")


def test_unprotected_position_still_pages_critical():
    a = assess_read_only_report(report(
        protected_position(stop_status="canceled"), kill=False, enabled=True,
        blockers=("existing_position_or_open_order",)), now=NOW)
    assert (a.level, a.code) == ("critical", "unsafe_exposure")


def test_loss_cutoff_with_protected_position_pages_critical():
    a = assess_read_only_report(report(
        protected_position(), kill=False, enabled=True,
        blockers=("existing_position_or_open_order", "daily_three_percent_loss_cutoff")),
        now=NOW)
    assert (a.level, a.code) == ("critical", "loss_cutoff")


# ---- supervisor lease ----

def test_supervisor_accepts_managed_exposure_only_when_rederived(monkeypatch):
    from bot import paper_supervisor_loop as loop
    r = report(protected_position(), kill=False, enabled=True,
               blockers=("existing_position_or_open_order",))
    r["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
    r["monitor_incident"] = assess_read_only_report(r).as_dict()
    assert loop.valid_observation(r) is True
    forged = deepcopy(r)
    forged["reconciliation"]["managed_exposure"] = False
    assert loop.valid_observation(forged) is False
    forged = deepcopy(r)
    forged["monitor_incident"]["code"] = "idle"
    assert loop.valid_observation(forged) is False


# ---- continuous reconciler recovery ----

def test_continuous_reconciler_alerts_once_and_keeps_running(monkeypatch):
    from bot import paper_reconciler_service as service
    calls, alerts, sleeps = [], [], []
    def flaky(**kw):
        calls.append(kw)
        if len(calls) < 3:
            raise RuntimeError("transient")
        if len(calls) == 4:
            raise KeyboardInterrupt  # end the synthetic infinite loop
    monkeypatch.setattr(service, "reconcile_pending_once", flaky)
    with pytest.raises(KeyboardInterrupt):
        service.run_service(trading=object(), journal=object(), max_ticks=0,
                            alert=lambda: alerts.append(1) or True,
                            sleeper=sleeps.append, recover=True, clock=lambda: 0)
    assert len(calls) == 4 and alerts == [1] and len(sleeps) == 3
    assert all(kw["expire_unapproved"] is True for kw in calls)


def test_reconciler_identity_change_still_stops_in_recover_mode(monkeypatch):
    import hashlib
    from bot import paper_reconciler_service as service
    broker = SimpleNamespace(get_account=lambda: SimpleNamespace(id="other"))
    monkeypatch.setattr(service, "reconcile_pending_once", lambda **kw: None)
    with pytest.raises(TradeBlocked):
        service.run_service(trading=broker, journal=object(), max_ticks=0,
                            alert=lambda: True, sleeper=lambda _: None, recover=True,
                            expected_account_hash=hashlib.sha256(b"pinned").hexdigest())


# ---- executor modes ----

def test_autonomous_executor_paused_still_maintains_but_never_enters(monkeypatch):
    from bot import paper_executor_service as service
    from bot.paper_safety import PAPER_ENDPOINT
    from hashlib import sha256
    account_id = "00000000-0000-0000-0000-000000000001"
    monitor = SimpleNamespace(
        assert_monitor_only=lambda: None,
        read_control=lambda: SimpleNamespace(kill_switch_active=True, submissions_enabled=False),
        read_unresolved_intents=lambda limit: [],
        read_approved_stop_intents=lambda limit: [])
    trading = SimpleNamespace(
        _base_url=PAPER_ENDPOINT,
        get_account=lambda: SimpleNamespace(id=account_id, trading_blocked=False),
        get_clock=lambda: SimpleNamespace(is_open=True),
        get_all_positions=lambda: [], get_orders=lambda filter: [],
        submit_order=lambda **k: pytest.fail("no entry while paused"))
    result = service.prepare_live_candidate(components={
        "trading": trading, "market_data": None, "monitor": monitor,
        "executor": SimpleNamespace(assert_executor_permissions=lambda **k: None),
        "reconciler": SimpleNamespace(assert_reconciler_permissions=lambda: None,
                                      read_pending_plans=lambda: []),
        "reconciler_trading": trading,
        "expected_account_hash": sha256(account_id.encode()).hexdigest()},
        now=NOW, autonomous=True)
    assert result["mode"] == "PAUSED" and result["broker_orders"] == 0
    assert result["maintenance"]["broker_writes"] == 0


def test_managed_position_report_requires_verified_stop():
    from bot.paper_executor_service import _managed_position_report
    stop = order("stop-leg", side="sell", kind="stop", parent="parent-1", stop_price=24)
    parent = order("parent-1", status="filled", filled=2, cid="hds-c", legs=(stop,), klass="oto")
    good = SimpleNamespace(read_approved_stop_intents=lambda limit: [
        ("hds-c", "filled", "F", 2, 24, "parent-1", "gtc")])
    out = _managed_position_report(good, [SimpleNamespace(symbol="F", qty="2")], [parent], [])
    assert out["mode"] == "POSITION_MANAGED"
    bad = SimpleNamespace(read_approved_stop_intents=lambda limit: [])
    with pytest.raises(TradeBlocked, match="not verified"):
        _managed_position_report(bad, [SimpleNamespace(symbol="F", qty="2")], [parent], [])


# ---- migrations and privilege boundaries ----

def test_policy_migration_keeps_independent_limits_and_role_separation():
    sql = (ROOT / "sql" / "migrations" / "003_policy_authorization_and_controls.sql").read_text()
    for guard in (
            "v_kill IS DISTINCT FROM FALSE OR v_enabled IS DISTINCT FROM TRUE",
            "autonomous_entries_enabled IS DISTINCT FROM TRUE",
            "max_trade_risk_fraction", "max_position_fraction",
            "max_entries_per_day", "Verified opening equity missing",
            "interval '120 seconds'", "'policy_authorized'",
            "horsesdontstop-paper-exclusive-intent",
            "GRANT EXECUTE ON FUNCTION public.paper_arm_autonomous_trading(text) TO hds_paper_operator;"):
        assert guard in sql
    # The bot's roles can pause but never arm; nobody gets table writes.
    assert "paper_arm_autonomous_trading(text)\n    TO hds_paper_executor" not in sql
    assert "TO hds_paper_executor, hds_paper_reconciler, hds_paper_operator" in sql
    assert "GRANT UPDATE" not in sql.replace(
        "GRANT SELECT, UPDATE (kill_switch_active, submissions_enabled, updated_at)\n"
        "    ON TABLE public.paper_control TO hds_paper_control_owner", "")


def test_acl_boundary_never_allows_bot_roles_to_arm():
    from bot.paper_role_boundary import BOUNDARY_SQL
    assert "paper_policy_authorize_entry(" in BOUNDARY_SQL
    assert "paper_engage_kill_switch(text)" in BOUNDARY_SQL
    assert "paper_arm_autonomous_trading" not in BOUNDARY_SQL


def test_owner_credential_is_never_persisted_by_ops_scripts():
    for script in (ROOT / "ops" / "windows").glob("*.ps1"):
        text = script.read_text(encoding="utf-8", errors="ignore")
        assert "neon-owner-dsn" not in text, script.name
    deploy = (ROOT / "ops" / "windows" / "deploy-paper-migrations.ps1").read_text()
    assert "Read-Host -AsSecureString" in deploy


def test_migration_ledger_refuses_edited_applied_files(tmp_path):
    from bot.paper_migrations import migration_files, pending
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    files = migration_files(tmp_path)
    import hashlib
    applied = {"001_a.sql": hashlib.sha256(b"SELECT 1;").hexdigest()}
    assert [p.name for p, _ in pending(applied, files)] == ["002_b.sql"]
    with pytest.raises(SystemExit):
        pending({"001_a.sql": "0" * 64}, files)


def test_policy_installs_disabled_and_enable_is_a_separate_release_step():
    sql = (ROOT / "sql" / "migrations" / "003_policy_authorization_and_controls.sql").read_text()
    assert "VALUES (TRUE, FALSE, 0.01, 1.0, 1, '09:35', '15:30')" in sql
    assert not list((ROOT / "sql" / "migrations").glob("*enable*"))
    assert (ROOT / "sql" / "release" / "enable_autonomous_entries.sql").exists()


def test_arm_credential_is_never_stored_or_readable_by_unattended_services():
    ops = ROOT / "ops" / "windows"
    for script in ops.glob("*.ps1"):
        text = script.read_text(encoding="utf-8", errors="ignore")
        assert "neon-operator-dsn" not in text, script.name
        assert "neon-owner-dsn" not in text, script.name
        if script.name != "set-paper-trading.ps1":
            assert "hds_paper_operator_login" not in text, script.name
    arm = (ops / "set-paper-trading.ps1").read_text()
    assert "Read-Hidden 'hds_paper_operator_login password (not stored)'" in arm
    assert "Read-Host -AsSecureString" in arm
    from bot.paper_role_boundary import BOUNDARY_SQL
    assert "'hds_paper_operator','hds_paper_control_owner'" in BOUNDARY_SQL


def test_orphan_open_sell_on_flat_symbol_is_never_managed():
    sell = order("stop-x", side="sell", kind="stop", status="new", cid="hds-c-x")
    rec = reconcile_observation([], [sell], [], order_fetch_complete=True,
                                approved_stops=[("hds-c-x", "filled", "F", 2, 24, "p", "gtc")])
    assert rec.managed_exposure is False


def test_executor_status_writes_follow_the_reconciler_release_flag():
    src = (ROOT / "bot" / "paper_executor_service.py").read_text()
    assert "PAPER_RECONCILIATION_SERVICE_RELEASED is True" in src
    assert "record=autonomous is True," not in src
    # Maintenance must run after the executor's own reconciliation.
    assert src.index("reconcile_pending_once(") < src.index('stage("risk_reducing_maintenance")')


def test_unsent_dispatch_is_retired_only_after_broker_404():
    from bot.paper_status_reconciler import reconcile_verified_paper_order
    from bot.paper_safety import plan_paper_entry
    from datetime import date
    from alpaca.common.exceptions import APIError
    plan = plan_paper_entry("F", date(2026, 11, 10), 25, 500, 500, .01)
    class Journal:
        calls = []
        def __init__(self, ready): self.ready = ready
        def assert_reconciler_permissions(self): pass
        def get_durable_intent_terms(self, cid):
            return ("unknown", None, plan.strategy, plan.symbol, date(2026, 11, 10),
                    plan.whole_shares, plan.estimated_entry, plan.protective_stop,
                    plan.time_in_force)
        def retire_unsent_dispatch(self, cid):
            self.calls.append(cid); return self.ready
    class Broker:
        _base_url = "https://paper-api.alpaca.markets"
        def get_all_positions(self): return []
        def get_order_by_client_id(self, cid):
            raise APIError("nf", http_error=SimpleNamespace(response=SimpleNamespace(status_code=404)))
    assert reconcile_verified_paper_order(trading=Broker(), journal=Journal(True), plan=plan,
                                          record=False, expire_unapproved=True) == "canceled"
    assert reconcile_verified_paper_order(trading=Broker(), journal=Journal(False), plan=plan,
                                          record=False, expire_unapproved=True) == "unknown"
    with pytest.raises(TradeBlocked):   # observe-only callers never retire
        reconcile_verified_paper_order(trading=Broker(), journal=Journal(True), plan=plan)


def test_003_review_fixes_present():
    sql = (ROOT / "sql" / "migrations" / "003_policy_authorization_and_controls.sql").read_text()
    assert "CHECK (max_entries_per_day = 1)" in sql
    assert "SELECT min(s.equity) INTO v_open_equity" in sql
    assert sql.count("RAISE EXCEPTION 'PAPER control row missing'") == 2
    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hds_paper_operator')" in sql
    assert "interval '15 minutes'" in sql and "dispatch_never_reached_broker" in sql
    assert "paper_retire_unsent_dispatch(text, boolean) TO hds_paper_reconciler;" in sql


def test_migration_sql_has_no_code_hidden_behind_line_comments():
    """A separator inside a -- comment silently breaks the statement."""
    for path in sorted((ROOT / "sql" / "migrations").glob("*.sql")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "--" in line and line.strip() and not line.strip().startswith("--"):
                comment = line.split("--", 1)[1]
                assert not comment.rstrip().endswith((",", ";")), f"{path.name}:{n}"


def test_maintenance_still_runs_when_reconciliation_blocks():
    src = (ROOT / "bot" / "paper_executor_service.py").read_text()
    body = src[src.index("reconcile_error = None"):src.index("if paused:")]
    assert "_run_maintenance(" in body and "raise reconcile_error" in body
    assert body.index("_run_maintenance(") < body.index("raise reconcile_error")


def test_filled_parent_only_in_closed_list_still_proves_protection():
    """Live 2026-11-10 shape: OPEN returns only the stop leg; parent is CLOSED."""
    stop = order("stop-leg", side="sell", kind="stop", status="new",
                 parent="parent-1", stop_price=24)
    parent = order("parent-1", status="filled", filled=2, cid="hds-c",
                   legs=(stop,), klass="oto")
    approved = [("hds-c", "filled", "F", 2, 24, "parent-1", "gtc")]
    pos = [SimpleNamespace(symbol="F", qty="2")]
    open_only = reconcile_observation(pos, [stop], [], order_fetch_complete=True,
                                      approved_stops=approved)
    assert open_only.managed_exposure is False          # what the pilot saw
    with_parent = reconcile_observation(pos, [stop, parent], [], order_fetch_complete=True,
                                        approved_stops=approved)
    assert with_parent.managed_exposure is True and with_parent.uncovered_symbols == ()


def test_watchdog_merged_reconciliation_facts_do_not_degrade_managed_exposure():
    a = assess_read_only_report(report(
        protected_position(), kill=True, enabled=False,
        blockers=("existing_position_or_open_order", "broker_orders_not_reconciled",
                  "open_broker_positions", "open_broker_orders")), now=NOW)
    assert (a.level, a.code) == ("idle", "managed_exposure")


def test_equity_peak_sample_skips_opening_window_and_never_raises():
    from bot.paper_executor_service import _record_equity_peak_sample
    calls = []
    ok = SimpleNamespace(record_equity=lambda e, c: calls.append((e, c)))
    acct = SimpleNamespace(equity="500.14", cash="500.14")
    opening = datetime(2026, 11, 10, 14, 32, tzinfo=timezone.utc)     # 09:32 NY
    assert _record_equity_peak_sample(ok, acct, opening) is False and calls == []
    assert _record_equity_peak_sample(ok, acct, NOW) is True and calls == [(500.14, 500.14)]
    def boom(e, c): raise RuntimeError("db down")
    assert _record_equity_peak_sample(SimpleNamespace(record_equity=boom), acct, NOW) is False
