"""Inspect a PAPER executor connection role without exposing its DSN.

NO broker client, no order writes. Every SQL statement is SELECT except
the explicit SET TRANSACTION READ ONLY protection. Never log connection
or exception strings; they may include passwords, hostnames, or tokens.
"""
import os
import sys

from .paper_execution_journal import PaperExecutorJournal
from .paper_executor_preflight import EXPECTED_DATABASE, EXPECTED_ROLE


def inspect_identity(journal):
    with journal._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_user, current_database()")
            return cursor.fetchone()


def main():
    dsn = os.getenv("PAPER_EXECUTOR_DSN")
    if not dsn:
        print("BLOCKED: PAPER_EXECUTOR_DSN is missing.", file=sys.stderr)
        return 2
    try:
        identity = inspect_identity(PaperExecutorJournal(dsn))
    except Exception:
        # Never include exception contents, PostgreSQL DSN or password.
        print(
            "BLOCKED: executor database login failed or connection unavailable. "
            "Verify the full Neon URI and privately reset the restricted "
            "role password if needed.",
            file=sys.stderr,
        )
        return 2

    if not isinstance(identity, (tuple, list)) or len(identity) != 2:
        print("BLOCKED: invalid database identity response.", file=sys.stderr)
        return 2

    user, database = identity
    if user != EXPECTED_ROLE:
        print(
            "BLOCKED: executor GitHub secret uses the wrong Neon role. "
            "In Neon Connect, choose hds_paper_executor, NOT neondb_owner "
            "or hds_paper_monitor; replace PAPER_EXECUTOR_DSN privately.",
            file=sys.stderr,
        )
        return 2
    if database != EXPECTED_DATABASE:
        print(
            "BLOCKED: executor GitHub secret uses the wrong Neon database. "
            "Choose production > neondb and replace PAPER_EXECUTOR_DSN.",
            file=sys.stderr,
        )
        return 2

    print(
        "PAPER EXECUTOR IDENTITY CHECK PASSED: role=hds_paper_executor, "
        "database=neondb; ZERO database writes, ZERO broker orders."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
