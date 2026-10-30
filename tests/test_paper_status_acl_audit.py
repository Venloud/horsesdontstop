"""Static safety tests for the Phase 5.4 read-only ACL diagnostic.

These do NOT claim the underlying status integrity weakness has been fixed.
The actual executor grants must be checked on a precisely selected Neon branch.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / "ops" / "sql" / "paper_status_grants_audit_read_only.sql"
PLAN = ROOT / "docs" / "PAPER_STATUS_INTEGRITY_BOUNDARY_PHASE54_REVIEW.md"


def test_audit_query_is_single_read_only_select():
    sql = QUERY.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in sql.splitlines()
        if not line.lstrip().startswith("--")
    ).strip()
    assert re.match(r"^SELECT\s", executable, re.IGNORECASE)
    assert executable.endswith(";")
    assert executable.count(";") == 1
    # No statement line may start with a mutation keyword.
    assert not re.search(
        r"(?im)^\s*(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|"
        r"CREATE|GRANT|REVOKE|DO|CALL|COPY|EXECUTE|BEGIN|COMMIT)\b",
        executable,
    )


def test_audit_exposes_status_and_broker_id_write_privileges():
    sql = QUERY.read_text(encoding="utf-8")
    for token in (
        "'hds_paper_executor'",
        "'public.paper_intents'",
        "'status'",
        "'broker_order_id'",
        "'updated_at'",
        "'public.paper_audit_events'",
        "'public.paper_control'",
        "'public.paper_equity_snapshots'",
        "'UPDATE'",
        "'INSERT'",
        "intent_has_custom_triggers",
    ):
        assert token in sql


def test_plan_explicitly_not_deployed_or_security_approval():
    doc = PLAN.read_text(encoding="utf-8")
    assert "NOT a deployed hardening" in doc
    assert "predates PR #38" in doc
    assert "separate draft PR" in doc
    assert "NEEDS REVIEW" in doc
    assert "broker" in doc.lower()
