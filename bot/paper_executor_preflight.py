"""Verify Neon PAPER executor secret and permissions WITHOUT WRITING ANYTHING.

The executor role can INSERT future PAPER journal intents, but this
program NEVER invokes its write methods or Alpaca order APIs.
In addition to a separate PostgreSQL role-privilege attestation,
this connection explicitly sets its transaction READ ONLY before
querying the production journal.
"""
import os
import sys

from .paper_execution_journal import PaperExecutorJournal
from .paper_safety import TradeBlocked


EXPECTED_ROLE = "hds_paper_executor"
EXPECTED_DATABASE = "neondb"


def inspect_executor(journal: PaperExecutorJournal) -> dict:
    """Run only read queries and reject misdirected/overprivileged secrets."""
    journal.assert_executor_permissions()
    with journal._connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT current_user, current_database(), "
                "(SELECT kill_switch_active FROM public.paper_control "
                " WHERE singleton=TRUE), "
                "(SELECT submissions_enabled FROM public.paper_control "
                " WHERE singleton=TRUE), "
                "(SELECT COUNT(*) FROM public.paper_intents), "
                "(SELECT COUNT(*) FROM public.paper_audit_events), "
                "(SELECT COUNT(*) FROM public.paper_equity_snapshots)"
            )
            result = cursor.fetchone()

    if not result or len(result) != 7:
        raise TradeBlocked("Missing expected Neon production journal state")
    role, database, kill, enabled, intents, events, snapshots = result
    if role != EXPECTED_ROLE or database != EXPECTED_DATABASE:
        raise TradeBlocked("Executor connection points to wrong role or database")
    if type(kill) is not bool or type(enabled) is not bool:
        raise TradeBlocked("Missing or invalid database safety controls")
    if kill is not True or enabled is not False:
        raise TradeBlocked("Trading safety switches not in disabled state")
    if any(type(n) is not int or n < 0 for n in (intents, events, snapshots)):
        raise TradeBlocked("Invalid journal row counts")
    return {
        "database_connection": "verified",
        "role": role,
        "database": database,
        "verified_role_permissions": True,
        "transaction_mode": "READ ONLY",
        "kill_switch_active": kill,
        "submissions_enabled": enabled,
        "paper_intent_rows": intents,
        "paper_audit_rows": events,
        "paper_equity_rows": snapshots,
        "broker_order_submissions": 0,
        "database_writes": 0,
        "trading_enabled": False,
    }


def main() -> int:
    dsn = os.getenv("PAPER_EXECUTOR_DSN")
    if not dsn:
        print(
            "BLOCKED: PAPER_EXECUTOR_DSN GitHub secret missing. "
            "No database writes or broker trades.",
            file=sys.stderr,
        )
        return 2

    try:
        report = inspect_executor(PaperExecutorJournal(dsn))
    except Exception:
        # psycopg errors can embed connection strings and passwords.
        # Do not print exception objects, parameters, tracebacks, or DSN.
        print(
            "BLOCKED: executor authentication/permissions/controls failed. "
            "No database writes or broker trades.",
            file=sys.stderr,
        )
        return 2

    print(
        "PAPER EXECUTOR READ-ONLY CHECK PASSED: "
        f"role={report['role']}, database={report['database']}, "
        f"kill_switch_active={report['kill_switch_active']}, "
        f"submissions_enabled={report['submissions_enabled']}, "
        f"intents={report['paper_intent_rows']}, "
        f"audits={report['paper_audit_rows']}, "
        f"equity_snapshots={report['paper_equity_rows']}. "
        "ZERO database writes, ZERO broker orders."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
