"""Offline checks of TEMP-only Python psycopg integration entry point.

No network, external credentials, brokers, or database connections.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.paper_temp_lease_smoke as mod
from bot.paper_safety import TradeBlocked


def dsn(role="hds_paper_heartbeat", host=mod.TEMP_HOST,
        database="neondb", password="synthetic_password"):
    return f"postgresql://{role}:{password}@{host}/{database}?sslmode=require"


class Writer:
    def __init__(self, *, success_on_first=False, reject_duplicate=True,
                 reject_competitor=True, role_ok=True):
        self.calls = []
        self.attested = 0
        self.success_on_first = success_on_first
        self.reject_duplicate = reject_duplicate
        self.reject_competitor = reject_competitor
        self.role_ok = role_ok

    def assert_heartbeat_writer_only(self):
        self.attested += 1
        if not self.role_ok:
            raise TradeBlocked("overprivileged temporary writer")

    def pulse(self, *, worker_id, sequence, poll_succeeded):
        self.calls.append((worker_id, sequence, poll_succeeded))
        if self.success_on_first and len(self.calls) == 1:
            return True
        if sequence == 2 and len(self.calls) > 2:
            if self.reject_duplicate:
                raise TradeBlocked("duplicate sequence")
        if worker_id.startswith("paper-test-competitor-"):
            if self.reject_competitor:
                raise TradeBlocked("competing worker")
        return False


class Monitor:
    def __init__(self, *, role_ok=True, forged_healthy=False,
                 wrong_identity=False, stale_age=False, wrong_blockers=False):
        self.attested = 0
        self.reads = []
        self.role_ok = role_ok
        self.forged_healthy = forged_healthy
        self.wrong_identity = wrong_identity
        self.stale_age = stale_age
        self.wrong_blockers = wrong_blockers

    def assert_heartbeat_monitor_only(self):
        self.attested += 1
        if not self.role_ok:
            raise TradeBlocked("bad monitor role")

    def inspect(self, *, expected_worker_id):
        self.reads.append(expected_worker_id)
        return SimpleNamespace(
            healthy=self.forged_healthy,
            poll_succeeded=False,
            worker_id=("other-test-worker" if self.wrong_identity
                       else expected_worker_id),
            sequence=min(len(self.reads), 2),
            age_seconds=121.0 if self.stale_age else 0.0,
            blockers=("persistent_supervisor_heartbeat_stale_or_future",)
            if self.wrong_blockers else
            ("persistent_supervisor_broker_poll_not_proven",),
        )


def test_valid_temp_roles_match_preexisting_temp_endpoint_only():
    for role in ("hds_paper_monitor", "hds_paper_heartbeat"):
        raw = dsn(role=role)
        assert mod.validate_temp_role_dsn(raw, role) == raw
    assert mod.validate_temp_role_dsn(
        dsn(host="ep-holy-moon-b5trovxf-pooler.c-7.us-east-2.aws.neon.tech"),
        "hds_paper_heartbeat")
    assert mod.validate_temp_role_dsn(
        dsn() + "&channel_binding=require&connect_timeout=5",
        "hds_paper_heartbeat")


@pytest.mark.parametrize("bad", [
    None, "", "postgress://hds_paper_heartbeat:x@invalid/neondb",
    "https://example.org/neondb",
    dsn(role="neondb_owner"),
    dsn(role="hds_paper_monitor"),
    dsn(host="ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech"),
    dsn(host="localhost"),
    dsn(database="production"),
    dsn(password=""),
    dsn() + "#production",
    dsn().replace("postgresql://", "postgresql://hds_paper_executor@"),
    dsn().replace("/neondb", ":1234/neondb"),
    dsn() + "&host=ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech",
    dsn() + "&hostaddr=127.0.0.1",
    dsn() + "&dbname=production",
    dsn() + "&user=neondb_owner",
    dsn() + "&password=admin",
    dsn() + "&service=production",
    dsn() + "&options=-c%20search_path%3Dpublic",
    dsn() + "&sslmode=disable",
    dsn() + "&sslmode=require",
    dsn() + "&channel_binding=disable",
    dsn() + "&connect_timeout=9999",
    dsn() + "&unknownarg=1",
])
def test_rejects_production_admin_incomplete_and_wrong_branch_credentials(bad):
    with pytest.raises(TradeBlocked) as exc:
        mod.validate_temp_role_dsn(bad, "hds_paper_heartbeat")
    assert "synthetic_password" not in str(exc.value)


def test_temp_smoke_uses_both_roles_and_leaves_no_health_claim():
    writer, reader = Writer(), Monitor()
    report = mod.run_temp_lease_smoke(
        writer, reader, worker_id="paper-temp-smoke-1234")
    assert writer.attested >= 1
    assert reader.attested >= 1
    assert writer.calls[0] == ("paper-temp-smoke-1234", 1, False)
    assert writer.calls[1] == ("paper-temp-smoke-1234", 2, False)
    assert writer.calls[2] == ("paper-temp-smoke-1234", 2, False)
    assert writer.calls[3][0].startswith("paper-test-competitor-")
    assert all(poll is False for _, _, poll in writer.calls)
    assert reader.reads == ["paper-temp-smoke-1234"] * 3
    assert report["broker_orders"] == 0
    assert report["final_health"] == "unhealthy"


@pytest.mark.parametrize("writer_kwargs,reader_kwargs", [
    ({"success_on_first": True}, {}),
    ({"reject_duplicate": False}, {}),
    ({"reject_competitor": False}, {}),
    ({}, {"forged_healthy": True}),
    ({}, {"wrong_identity": True}),
    ({}, {"stale_age": True}),
    ({}, {"wrong_blockers": True}),
    ({"role_ok": False}, {}),
    ({}, {"role_ok": False}),
])
def test_missing_safety_guarantees_fail_closed(writer_kwargs,reader_kwargs):
    writer, reader = Writer(**writer_kwargs), Monitor(**reader_kwargs)
    with pytest.raises(TradeBlocked):
        mod.run_temp_lease_smoke(
            writer, reader, worker_id="paper-temp-smoke-1234")
    if not writer.role_ok or not reader.role_ok:
        assert writer.calls == []


def test_main_requires_acknowledgement_before_instantiating_clients(
        monkeypatch, capsys):
    class MustNotCreate:
        def __init__(self, *_):
            raise AssertionError("No clients without explicit consent")
    monkeypatch.setattr(mod, "PaperWorkerHeartbeat", MustNotCreate)
    assert mod.main([]) == 2
    assert "acknowledgement missing" in capsys.readouterr().err


def test_main_requires_both_temp_dsns_and_does_not_log_secrets(
        monkeypatch, capsys):
    monkeypatch.setenv("PAPER_TEMP_HEARTBEAT_DSN", dsn())
    monkeypatch.delenv("PAPER_TEMP_MONITOR_DSN", raising=False)
    assert mod.main(["--acknowledge-temp-only-writes"]) == 2
    output = capsys.readouterr()
    assert "synthetic_password" not in output.err + output.out
    assert "BLOCKED" in output.err


def test_main_with_fake_clients_succeeds_without_alpaca_or_real_database(
        monkeypatch, capsys):
    monkeypatch.setenv("PAPER_TEMP_HEARTBEAT_DSN", dsn())
    monkeypatch.setenv("PAPER_TEMP_MONITOR_DSN", dsn(
        role="hds_paper_monitor", password="synthetic_monitor"))
    clients = []

    def build_client(_dsn):
        client = Writer() if len(clients) == 0 else Monitor()
        clients.append(client)
        return client

    monkeypatch.setattr(mod, "PaperWorkerHeartbeat", build_client)
    assert mod.main(["--acknowledge-temp-only-writes"]) == 0
    output = capsys.readouterr()
    assert "UNHEALTHY" in output.out
    assert "ZERO broker orders" in output.out
    assert "synthetic_password" not in output.out
    assert "synthetic_monitor" not in output.out
    assert len(clients) == 2


def test_errors_are_sanitized_and_do_not_claim_success(monkeypatch,capsys):
    monkeypatch.setenv("PAPER_TEMP_HEARTBEAT_DSN", dsn())
    monkeypatch.setenv("PAPER_TEMP_MONITOR_DSN", dsn(role="hds_paper_monitor"))
    def fail(_dsn):
        raise RuntimeError("postgresql://SENSITIVE_SECRET_NEVER_SHOW")
    monkeypatch.setattr(mod, "PaperWorkerHeartbeat", fail)
    assert mod.main(["--acknowledge-temp-only-writes"]) == 2
    stdout = capsys.readouterr()
    assert "SENSITIVE_SECRET_NEVER_SHOW" not in stdout.err + stdout.out


def test_smoke_source_has_no_broker_or_live_trading_write_calls():
    source = Path(mod.__file__).read_text()
    assert "TradingClient(" not in source
    assert "submit_order(" not in source
    assert "cancel_order(" not in source
    assert "paper_control SET" not in source
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = True" not in source
