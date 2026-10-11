"""Network-free role identity checks; ensure no DB writes or leaked secrets."""
from bot import paper_executor_role_check as rolecheck


class FakeCursor:
    def __init__(self, identity):
        self.identity = identity
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        self.queries.append(sql)
        assert sql == "SET TRANSACTION READ ONLY" or sql == (
            "SELECT current_user, current_database()")

    def fetchone(self):
        assert self.queries == [
            "SET TRANSACTION READ ONLY", "SELECT current_user, current_database()"
        ]
        return self.identity


class FakeConnection:
    def __init__(self, identity):
        self.c = FakeCursor(identity)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.c


class FakeJournal:
    def __init__(self, identity):
        self.identity = identity
        self.conn = FakeConnection(identity)

    def _connect(self):
        return self.conn

    def reserve_if_enabled(self, *_args):
        raise AssertionError("Must not reserve PAPER order intent")


def test_role_identity_query_can_never_write():
    j = FakeJournal(("hds_paper_executor", "neondb"))
    assert rolecheck.inspect_identity(j) == ("hds_paper_executor", "neondb")
    assert j.conn.c.queries == [
        "SET TRANSACTION READ ONLY", "SELECT current_user, current_database()"
    ]


def test_wrong_role_is_rejected_without_exposing_secret(monkeypatch, capsys):
    monkeypatch.setenv(
        "PAPER_EXECUTOR_DSN",
        "postgresql://wrong:SECRET_PASSWORD@internal.neon/neondb",
    )
    monkeypatch.setattr(
        rolecheck, "PaperExecutorJournal",
        lambda dsn: FakeJournal(("neondb_owner", "neondb")),
    )
    assert rolecheck.main() == 2
    error = capsys.readouterr().err
    assert "wrong Neon role" in error
    assert "SECRET_PASSWORD" not in error
    assert "postgresql://" not in error


def test_wrong_database_rejected(monkeypatch, capsys):
    monkeypatch.setenv("PAPER_EXECUTOR_DSN", "postgresql://hidden")
    monkeypatch.setattr(
        rolecheck, "PaperExecutorJournal",
        lambda dsn: FakeJournal(("hds_paper_executor", "otherdb")),
    )
    assert rolecheck.main() == 2
    assert "wrong Neon database" in capsys.readouterr().err


def test_correct_identity_stays_readonly(monkeypatch, capsys):
    monkeypatch.setenv("PAPER_EXECUTOR_DSN", "postgresql://hidden")
    monkeypatch.setattr(
        rolecheck, "PaperExecutorJournal",
        lambda dsn: FakeJournal(("hds_paper_executor", "neondb")),
    )
    assert rolecheck.main() == 0
    output = capsys.readouterr().out
    assert "ZERO database writes" in output
    assert "ZERO broker orders" in output


def test_login_error_redacts_exception(monkeypatch, capsys):
    monkeypatch.setenv("PAPER_EXECUTOR_DSN", "postgresql://hidden")
    def fail(*_):
        raise RuntimeError("PRODUCTION_PRIVATE_PASSWORD")
    monkeypatch.setattr(rolecheck, "inspect_identity", fail)
    assert rolecheck.main() == 2
    assert "PRODUCTION_PRIVATE_PASSWORD" not in capsys.readouterr().err


def test_no_secret_causes_explicit_failure(monkeypatch, capsys):
    monkeypatch.delenv("PAPER_EXECUTOR_DSN", raising=False)
    assert rolecheck.main() == 2
    assert "missing" in capsys.readouterr().err
