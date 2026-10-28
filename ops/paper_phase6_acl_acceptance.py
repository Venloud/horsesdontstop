"""Manual post-migration acceptance, ONLY after exact isolated owner approval.

No migration installer, credential provisioner or broker client. Requires
three independently authenticated direct branch DSNs and a matching branch
marker. Temporarily changes TEST controls for synthetic reservation, restores
them and removes only fixtures proven absent before this run. Never production.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.paper_safety import make_client_order_id

PRODUCTION = "br-restless-sunset-b5s18rj3"


class AcceptanceBlocked(Exception):
    pass


def connect(dsn, role, branch):
    parsed = urlsplit(dsn)
    if (not branch.startswith("br-") or branch == PRODUCTION
            or not parsed.hostname or "-pooler" in parsed.hostname
            or not parsed.hostname.endswith(".neon.tech") or parsed.username != role):
        raise AcceptanceBlocked("Direct isolated role/branch configuration required")
    import psycopg
    conn = psycopg.connect(dsn, sslmode="require", connect_timeout=5,
                           options="-c statement_timeout=5000 -c lock_timeout=3000")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT session_user,current_user,current_database(),"
                        "(SELECT test_branch FROM public.hds_pr38_test_marker)")
            if cur.fetchone() != (role, role, "neondb", branch):
                raise AcceptanceBlocked("Independent login or isolated marker mismatch")
        conn.commit()
        if role != "neondb_owner":
            from bot.paper_role_boundary import assert_journal_acl_boundary
            with conn.cursor() as cur:
                assert_journal_acl_boundary(cur, reconciler=role == "hds_paper_reconciler_service")
            conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def scalar(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


def denied(conn, sql, args=(), *, state="42501"):
    try:
        scalar(conn, sql, args)
    except Exception as exc:
        conn.rollback()
        if getattr(exc, "sqlstate", None) == state:
            return
        raise AcceptanceBlocked("Unexpected database error, not an ACL denial") from None
    conn.rollback()
    raise AcceptanceBlocked("Unauthorized operation unexpectedly succeeded")


def controls(conn, kill, enabled):
    scalar(conn, "UPDATE public.paper_control SET kill_switch_active=%s,"
           "submissions_enabled=%s WHERE singleton=TRUE RETURNING singleton", (kill, enabled))
    conn.commit()


def run(owner, executor, reconciler):
    day = scalar(owner, "SELECT (clock_timestamp() AT TIME ZONE 'America/New_York')::date")
    cid = make_client_order_id(day, "F")
    safe = scalar(owner, "SELECT kill_switch_active AND NOT submissions_enabled "
                  "FROM public.paper_control WHERE singleton=TRUE")
    if safe is not True:
        raise AcceptanceBlocked("Initial TEST controls are not safe")
    empty = scalar(owner, "SELECT NOT EXISTS(SELECT 1 FROM public.paper_intents WHERE trading_day=%s) "
                   "AND NOT EXISTS(SELECT 1 FROM public.paper_audit_events WHERE client_order_id=%s)", (day, cid))
    owner.commit()
    if empty is not True:
        raise AcceptanceBlocked("Pre-existing fixtures must not be touched")
    created = False
    result = {}
    reserve = "SELECT public.paper_reserve_intent_if_enabled(%s,%s,%s,%s,%s,%s,%s)"
    terms = (cid, "momentum_volume", "F", day, 1, 25, 24)
    transition = "SELECT public.paper_record_reconciled_intent_status(%s,%s,%s,%s)"
    try:
        denied(executor, reserve, terms)
        controls(owner, False, True)
        if scalar(executor, reserve, terms) is not True:
            raise AcceptanceBlocked("Legitimate reservation failed")
        executor.commit()
        created = True
        denied(executor, reserve, terms, state="23505")
        controls(owner, True, False)
        result["atomic_reservation_and_uniqueness"] = True
        attacks = [
            ("INSERT INTO public.paper_intents(client_order_id,strategy,symbol,trading_day,"
             "whole_shares,expected_entry,protective_stop,status,broker_order_id) "
             "VALUES('forged','momentum_volume','F','2099-12-02',1,25,24,'filled','forged') RETURNING true", ()),
            ("UPDATE public.paper_intents SET status='filled',broker_order_id='forged' "
             "WHERE client_order_id=%s RETURNING true", (cid,)),
            ("UPDATE public.paper_intents SET expected_entry=999 WHERE client_order_id=%s RETURNING true", (cid,)),
            ("INSERT INTO public.paper_audit_events(client_order_id,kind,summary) "
             "VALUES(%s,'forged','filled') RETURNING true", (cid,)),
            (transition, (cid, "reserved", "submitted", "synthetic-phase6")),
        ]
        for sql, args in attacks:
            denied(executor, sql, args)
        result["executor_forgery_attacks_denied_42501"] = len(attacks)
        scalar(executor, "SELECT public.paper_mark_reserved_intent_unknown(%s)", (cid,))
        executor.commit()
        authorize = "SELECT public.paper_authorize_unknown_submission(%s,%s,%s,%s,%s,%s,%s)"
        denied(executor, authorize, terms)
        controls(owner, False, True)
        if scalar(executor, authorize, terms) is not True:
            raise AcceptanceBlocked("Final authorization failed")
        # Executor retains the control FOR SHARE until rollback. This is a
        # genuine competing owner connection, not serialized connector calls.
        denied(owner, "UPDATE public.paper_control SET kill_switch_active=TRUE "
               "WHERE singleton=TRUE RETURNING singleton", state="55P03")
        executor.rollback()
        controls(owner, True, False)
        result["final_control_recheck_and_cross_connection_lock"] = True
        denied(executor, "SELECT public.paper_mark_reserved_intent_unknown(%s)", (cid,))
        denied(reconciler, attacks[1][0], (cid,))
        denied(reconciler, attacks[3][0], (cid,))
        scalar(reconciler, transition, (cid, "unknown", "submitted", "synthetic-phase6"))
        reconciler.commit()
        denied(reconciler, transition, (cid, "unknown", "accepted", "synthetic-phase6"))
        denied(reconciler, transition, (cid, "submitted", "accepted", "wrong-id"))
        scalar(reconciler, transition, (cid, "submitted", "accepted", "synthetic-phase6"))
        reconciler.rollback()
        if scalar(owner, "SELECT status FROM public.paper_intents WHERE client_order_id=%s", (cid,)) != "submitted":
            raise AcceptanceBlocked("Forced rollback changed status")
        if scalar(owner, "SELECT count(*) FROM public.paper_audit_events WHERE client_order_id=%s", (cid,)) != 2:
            raise AcceptanceBlocked("Forced rollback leaked audit")
        owner.commit()
        scalar(reconciler, transition, (cid, "submitted", "accepted", "synthetic-phase6"))
        reconciler.commit()
        scalar(reconciler, transition, (cid, "accepted", "filled", "synthetic-phase6"))
        reconciler.commit()
        denied(reconciler, transition, (cid, "filled", "canceled", "synthetic-phase6"))
        if scalar(owner, "SELECT count(*) FROM public.paper_audit_events WHERE client_order_id=%s", (cid,)) != 4:
            raise AcceptanceBlocked("Transition and audit counts disagree")
        owner.commit()
        scalar(executor, "INSERT INTO public.paper_equity_snapshots(observed_at,equity,cash) "
               "VALUES('2099-12-01T14:30:00Z',500,500) RETURNING snapshot_id")
        executor.rollback()
        result.update(synthetic_status_audit_rollback=True, restricted_equity_insert_rollback=True)
    finally:
        executor.rollback()
        reconciler.rollback()
        owner.rollback()
        controls(owner, True, False)
        if created:
            with owner.cursor() as cur:
                cur.execute("DELETE FROM public.paper_audit_events WHERE client_order_id=%s", (cid,))
                cur.execute("DELETE FROM public.paper_intents WHERE client_order_id=%s", (cid,))
            owner.commit()
        if scalar(owner, "SELECT kill_switch_active AND NOT submissions_enabled "
                  "FROM public.paper_control WHERE singleton=TRUE") is not True:
            raise AcceptanceBlocked("Final control restoration unverified")
        if created and scalar(owner, "SELECT count(*) FROM public.paper_intents WHERE client_order_id=%s", (cid,)) != 0:
            raise AcceptanceBlocked("Synthetic cleanup failed")
        owner.commit()
    return result


def main():
    names = ("HDS_PHASE6_TEST_OWNER_DSN", "HDS_PHASE6_TEST_EXECUTOR_DSN", "HDS_PHASE6_TEST_RECONCILER_DSN")
    branch = os.environ.get("HDS_PHASE6_TEST_BRANCH_ID", "")
    if "--execute-isolated" not in sys.argv or not branch or any(not os.environ.get(n) for n in names):
        print("BLOCKED: explicit isolated authorization flag, branch ID and three private DSNs required")
        return 2
    connections = []
    try:
        if len({urlsplit(os.environ[n]).hostname for n in names}) != 1:
            raise AcceptanceBlocked("All credentials must target one isolated direct endpoint")
        for name, role in zip(names, ("neondb_owner", "hds_paper_executor", "hds_paper_reconciler_service")):
            connections.append(connect(os.environ[name], role, branch))
        pids = [scalar(conn, "SELECT pg_backend_pid()") for conn in connections]
        if len(set(pids)) != 3:
            raise AcceptanceBlocked("Three distinct authenticated PostgreSQL backends required")
        for conn in connections:
            conn.commit()
        result = run(*connections)
        print(json.dumps({"isolated_acl_acceptance": result, "broker_operations": 0,
                          "real_broker_evidence": False}, sort_keys=True))
        return 0
    except Exception:
        print("BLOCKED: isolated acceptance failed; inspect safe state privately; no broker operations")
        return 1
    finally:
        for conn in connections:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
