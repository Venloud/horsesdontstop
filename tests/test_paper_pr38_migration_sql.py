"""Offline regression checks for the review-only PR38 migration artifact.

These deliberately test complete lexical fragments, not isolated keywords,
because the previous committed migration split SQL regex string literals with
post-migration audit comments and omitted a CREATE FUNCTION terminator.
No database connection and no broker operations.
"""
from pathlib import Path
import re


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "paper_reservation_control_guard_review_only.sql"
)


def source():
    return MIGRATION.read_text(encoding="utf-8")


def test_complete_symbol_and_client_order_regex_strings():
    sql = source()
    assert "p_symbol !~ '^[A-Z]{1,5}$'" in sql
    assert (
        "p_client_order_id !~ ('^hds-' || to_char(p_trading_day,'YYYYMMDD')"
        " || '-'" not in sql
    )  # Preserve multiline layout; never rely on a one-line approximation.
    assert re.search(
        r"p_client_order_id\s+!~\s*\('\^hds-'\s*\|\|\s*to_char"
        r"\(p_trading_day,'YYYYMMDD'\)\s*\|\|\s*'-'\s*\|\|"
        r"\s*p_symbol\s*\|\|\s*'-\[0-9a-f\]\{12\}\$'\)",
        sql,
    )
    assert "p_symbol !~ '^[A-Z]{1,5}\n" not in sql
    assert "p_symbol || '-[0-9a-f]{12}\n" not in sql


def test_function_body_and_terminator_are_not_broken_by_audit_comments():
    sql = source()
    assert sql.count("AS $function$") == 1
    assert sql.count("$function$;") == 1
    body = sql.split("AS $function$", 1)[1].split("$function$;", 1)[0]
    assert "Post-migration read-only audit" not in body
    assert "-- production kill_switch_active" not in body
    assert body.count("'") % 2 == 0, "Unexpected unterminated SQL string"
    assert re.search(
        r"\$function\$;\s*ALTER FUNCTION public\."
        r"paper_reserve_intent_if_enabled\(",
        sql,
    )


def test_migration_retains_fail_closed_permissions_and_installation_boundary():
    sql = source()
    assert sql.lstrip().startswith("-- REVIEW ONLY, DO NOT APPLY TO PRODUCTION")
    assert sql.count("CREATE ROLE hds_paper_reservation_guard NOLOGIN NOINHERIT") == 1
    assert "SECURITY DEFINER" in sql
    assert "SET search_path TO 'pg_catalog'" in sql
    assert "FOR SHARE;" in sql
    assert "CREATE UNIQUE INDEX uq_paper_intents_trading_day" in sql
    assert "REVOKE ALL ON FUNCTION public.paper_reservation_control_guard() FROM PUBLIC" in sql
    assert "REVOKE INSERT ON TABLE public.paper_intents FROM hds_paper_executor;" in sql
    assert "REVOKE EXECUTE ON FUNCTION public.paper_reservation_control_guard() FROM hds_paper_executor;" in sql
    assert sql.startswith("-- REVIEW ONLY")
    assert sql.rstrip().endswith("both the pre-migration\n-- and the post-migration direct-intent-INSERT role grants to avoid outage.")
    assert not re.search(r"\bsubmit_order\s*\(", sql)
