"""No-network tests for the production executor authentication preflight."""
import pytest

from bot.paper_executor_preflight import inspect_executor, main
from bot.paper_safety import TradeBlocked


class ReadOnlyCursor:
    def __init__(self, sample, statements):
        self.sample, self.statements = sample, statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        assert not params
        assert sql.startswith("SELECT ") or sql == "SET TRANSACTION READ ONLY"
        if sql.startswith("SELECT "):
            assert self.statements[-2][0] == "SET TRANSACTION READ ONLY"

    def fetchone(self):
        return self.sample


class ReadOnlyConn:
    def __init__(self, sample, statements):
        self.sample, self.statements = sample, statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return ReadOnlyCursor(self.sample, self.statements)


class FakeJournal:
    def __init__(self, sample=None, *, fail_permissions=False):
        self.statements = []
        self.auth_checked = False
        self.fail_permissions = fail_permissions
        self.sample = sample or (
            "hds_paper_executor", "neondb", True, False, 0, 0, 0
        )

    def assert_executor_permissions(self):
        self.auth_checked = True
        if self.fail_permissions:
            raise TradeBlocked("Wrong role or privilege set")

    def _connect(self):
        if not self.auth_checked:
            raise AssertionError("Must prove permissions first")
        return ReadOnlyConn(self.sample, self.statements)

    def reserve_if_enabled(self, *_args, **_kwargs):
        raise AssertionError("Must never reserve an order in preflight")

    def mark_uncertain(self, *_args, **_kwargs):
        raise AssertionError("Must never write to journal in preflight")


def test_writer_connected_check_has_only_readonly_sql():
    journal = FakeJournal()
    report = inspect_executor(journal)
    assert report["database_connection"] == "verified"
    assert report["trading_enabled"] is False
    assert report["kill_switch_active"] is True
    assert report["submissions_enabled"] is False
    assert report["broker_order_submissions"] == 0
    assert report["database_writes"] == 0
    assert len(journal.statements) == 2
    assert journal.statements[0][0] == "SET TRANSACTION READ ONLY"
    assert journal.statements[1][0].startswith("SELECT ")


@pytest.mark.parametrize("sample", [
    ("neondb_owner", "neondb", True, False, 0, 0, 0),
    ("hds_paper_monitor", "neondb", True, False, 0, 0, 0),
    ("hds_paper_executor", "otherdb", True, False, 0, 0, 0),
    ("hds_paper_executor", "neondb", False, True, 0, 0, 0),
    ("hds_paper_executor", "neondb", True, True, 0, 0, 0),
    ("hds_paper_executor", "neondb", False, False, 0, 0, 0),
    ("hds_paper_executor", "neondb", None, False, 0, 0, 0),
    ("hds_paper_executor", "neondb", True, False, -1, 0, 0),
    ("hds_paper_executor", "neondb", True, False, "0", 0, 0),
])
def test_invalid_role_database_switch_or_counts_are_blocked(sample):
    with pytest.raises(TradeBlocked):
        inspect_executor(FakeJournal(sample))


def test_permission_failure_blocks_before_database_count_query():
    journal = FakeJournal(fail_permissions=True)
    with pytest.raises(TradeBlocked):
        inspect_executor(journal)
    assert journal.statements == []


def test_main_rejects_missing_secret(monkeypatch, capsys):
    monkeypatch.delenv("PAPER_EXECUTOR_DSN", raising=False)
    assert main() == 2
    assert "missing" in capsys.readouterr().err.lower()


def test_main_redacts_bad_dsn_and_errors(monkeypatch, capsys):
    """Never echo a Neon password even inside underlying exception text."""
    monkeypatch.setenv(
        "PAPER_EXECUTOR_DSN",
        "postgresql://bad_user:SUPER_SECRET_VALUE@neon.example/neondb",
    )
    def fail(*_args, **_kwargs):
        raise ValueError("SUPER_SECRET_VALUE")
    monkeypatch.setattr(
        "bot.paper_executor_preflight.inspect_executor", fail,
    )
    assert main() == 2
    logs = capsys.readouterr()
    assert "SUPER_SECRET_VALUE" not in logs.err
    assert "postgresql://" not in logs.err
    assert "broker trades" in logs.err
