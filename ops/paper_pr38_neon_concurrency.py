"""Manual, isolated Neon PR38 acceptance harness. NEVER run against production.

Requires distinct direct PostgreSQL DSNs in:
 HDS_PR38_TEST_OWNER_DSN and HDS_PR38_TEST_EXECUTOR_DSN
These must point to an existing branch-specific marker, not production.
No Alpaca APIs or production DB writes. Never print DSNs or server errors.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
import os
from threading import Barrier, Event
from time import monotonic
from urllib.parse import urlsplit

TARGET = "br-rapid-glitter-b53xh7rb"
OWNER_ROLE = "neondb_owner"
EXECUTOR_ROLE = "hds_paper_executor"
DSN_OWNER = "HDS_PR38_TEST_OWNER_DSN"
DSN_EXEC = "HDS_PR38_TEST_EXECUTOR_DSN"
FUNCTION = (
    "SELECT public.paper_reserve_intent_if_enabled("
    "%s,%s,%s,%s,%s,%s,%s)"
)
BASE = date(2099, 11, 10)


class SafetyBlock(Exception):
    pass


def connect(dsn, expected_role):
    # The Neon acceptance test must use DIRECT endpoints, never pooler DSNs.
    # Parse only in memory; URLs can embed passwords and are never logged.
    expected_branch = os.environ.get("HDS_PR38_EXPECTED_BRANCH_ID", TARGET)
    if (not expected_branch.startswith("br-")
            or expected_branch == "br-restless-sunset-b5s18rj3"):
        raise SafetyBlock("Explicit nonproduction branch identity required")
    hostname = urlsplit(dsn).hostname if isinstance(dsn, str) else None
    if not hostname or "-pooler" in hostname.lower():
        raise SafetyBlock("A verified direct test-branch PostgreSQL DSN is required")
    # Import delayed until after explicit operator configuration checks.
    import psycopg
    c = psycopg.connect(
        dsn, sslmode="require", connect_timeout=5,
        options="-c statement_timeout=7000 -c lock_timeout=5000",
    )
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT current_user,current_database(),"
                "(SELECT test_branch FROM public.hds_pr38_test_marker)"
            )
            if cur.fetchone() != (expected_role, "neondb", expected_branch):
                raise SafetyBlock("Isolated branch identity or role mismatch")
            cur.execute("SELECT pg_backend_pid()")
            pid = cur.fetchone()[0]
        c.commit()
        return c, pid
    except Exception:
        c.close()
        raise


def key(day, symbol):
    plain = f"horsesdontstop|{day.isoformat()}|{symbol}|momentum_volume|v1"
    return f"hds-{day:%Y%m%d}-{symbol}-" + sha256(plain.encode()).hexdigest()[:12]


def params(day, symbol):
    return (key(day, symbol), "momentum_volume", symbol, day, 1, 100, 95)


def controls(owner_dsn, *, enabled):
    c, _ = connect(owner_dsn, OWNER_ROLE)
    try:
        with c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE public.paper_control "
                    "SET kill_switch_active=%s,submissions_enabled=%s "
                    "WHERE singleton=TRUE RETURNING singleton",
                    (not enabled, enabled),
                )
                if cur.fetchone() != (True,):
                    raise SafetyBlock("Control singleton not present")
    finally:
        c.close()


def verify_safe_controls(owner_dsn):
    # A separate SELECT must confirm the control state after every run.
    c, _ = connect(owner_dsn, OWNER_ROLE)
    try:
        with c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT kill_switch_active, submissions_enabled "
                    "FROM public.paper_control WHERE singleton=TRUE"
                )
                if cur.fetchone() != (True, False):
                    raise SafetyBlock("Test branch control state was not restored")
    finally:
        c.close()


def count(owner_dsn, day):
    c, _ = connect(owner_dsn, OWNER_ROLE)
    try:
        with c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM public.paper_intents WHERE trading_day=%s",
                    (day,),
                )
                return cur.fetchone()[0]
    finally:
        c.close()


def attempt(executor_dsn, day, symbol, barrier=None):
    c, pid = connect(executor_dsn, EXECUTOR_ROLE)
    t0 = monotonic()
    try:
        with c:
            with c.cursor() as cur:
                if barrier is not None:
                    barrier.wait(timeout=12)
                t0 = monotonic()
                try:
                    cur.execute(FUNCTION, params(day, symbol))
                    result = cur.fetchone()
                    if result != (True,):
                        return {"pid": pid, "accepted": False,
                                "state": "no_new_intent", "seconds": round(monotonic()-t0, 3)}
                    return {"pid": pid, "accepted": True, "state": "reserved",
                            "seconds": round(monotonic()-t0, 3)}
                except Exception as exc:
                    # Only the two EXPECTED SQL denial codes count as safe
                    # reservation refusals. Missing functions, network errors,
                    # lock timeouts and permission drift are test failures.
                    # Never echo the exception: a driver error may contain a DSN.
                    state = getattr(exc, "sqlstate", None)
                    if state not in ("42501", "23505"):
                        raise SafetyBlock("Unexpected reservation failure") from None
                    return {"pid": pid, "accepted": False, "state": state,
                            "seconds": round(monotonic()-t0, 3)}
    finally:
        c.close()


def parallel(executor_dsn, day, symbols):
    barrier = Barrier(len(symbols))
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futs = [pool.submit(attempt, executor_dsn, day, sym, barrier) for sym in symbols]
        rows = [f.result(timeout=20) for f in futs]
    # A backend must be distinct per concurrent transaction.
    if len({r["pid"] for r in rows}) != len(rows):
        raise SafetyBlock("Not independently identified PostgreSQL backends")
    return rows


def assert_count(owner_dsn, day, expected):
    real = count(owner_dsn, day)
    if real != expected:
        raise SafetyBlock(f"Unexpected intent count (expected {expected}, observed {real})")


def inspect_role(executor_dsn, owner_dsn):
    c, pid = connect(executor_dsn, EXECUTOR_ROLE)
    try:
        with c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT has_table_privilege(current_user,'public.paper_intents','INSERT'),"
                    "has_table_privilege(current_user,'public.paper_control','UPDATE'),"
                    "has_table_privilege(current_user,'public.paper_equity_snapshots','INSERT'),"
                    "has_function_privilege(current_user,"
                    "'public.paper_reserve_intent_if_enabled("
                    "text,text,text,date,integer,numeric,numeric)','EXECUTE')"
                )
                if cur.fetchone() != (False, False, True, True):
                    raise SafetyBlock("Executor grants bypass hardened boundary")
                cur.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename='paper_intents'"
                )
                if "uq_paper_intents_trading_day" not in [r[0] for r in cur.fetchall()]:
                    raise SafetyBlock("Required unique-day database index missing")
    finally:
        c.close()
    # Both separate credentials must prove the SAME branch marker.
    owner, owner_pid = connect(owner_dsn, OWNER_ROLE)
    owner.close()
    return pid, owner_pid


def verify_empty_fixtures(owner_dsn, days):
    for day in days:
        assert_count(owner_dsn, day, 0)


def test_guard_race(executor_dsn, owner_dsn, day):
    """Reservation first, competing kill-toggle times out on FOR SHARE.

    A subsequent control flip must finish after the reservation commits.
    """
    held = Event()
    release = Event()
    answer = {}
    def reserve_and_hold():
        c, pid = connect(executor_dsn, EXECUTOR_ROLE)
        try:
            with c:
                with c.cursor() as cur:
                    cur.execute(FUNCTION, params(day, "F"))
                    answer["reservation"] = cur.fetchone() == (True,)
                    answer["pid"] = pid
                    held.set()
                    if not release.wait(timeout=5):
                        raise SafetyBlock("Holder release event timeout")
        finally:
            c.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(reserve_and_hold)
        if not held.wait(timeout=8):
            release.set()
            fut.result(timeout=10)
            raise SafetyBlock("Reservation did not reach held lock")
        c, owner_pid = connect(owner_dsn, OWNER_ROLE)
        try:
            with c:
                with c.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '250ms'")
                    try:
                        cur.execute(
                            "UPDATE public.paper_control SET kill_switch_active=TRUE,"
                            "submissions_enabled=FALSE WHERE singleton=TRUE"
                        )
                        raise SafetyBlock("Kill-toggle bypassed held control lock")
                    except Exception as exc:
                        if getattr(exc, "sqlstate", None) != "55P03":
                            raise
        finally:
            c.close()
            release.set()
        fut.result(timeout=10)
    if not answer.get("reservation") or owner_pid == answer.get("pid"):
        raise SafetyBlock("Reservation holder/lock-wait evidence missing")
    controls(owner_dsn, enabled=False)
    assert_count(owner_dsn, day, 1)
    denied = attempt(executor_dsn, day+timedelta(days=1), "F")
    if denied["accepted"] or denied["state"] != "42501":
        raise SafetyBlock("Post-kill reservation was not explicitly denied")
    return {"reservation_pid": answer["pid"], "control_pid": owner_pid,
            "lock_timeout_sqlstate": "55P03", "blocked_after_kill": True}


def test_switch_first(executor_dsn, owner_dsn, day):
    controls(owner_dsn, enabled=False)
    r = attempt(executor_dsn, day, "F")
    assert_count(owner_dsn, day, 0)
    if r["accepted"] or r["state"] != "42501":
        raise SafetyBlock("Switch-first reservation was not explicitly denied")
    return r


def test_raw_insert_denied(executor_dsn, owner_dsn, day):
    """Exercise the actual restricted-role table grant, not only pg_acl views.

    Roll back regardless of outcome. A surprising successful INSERT must
    never leave a synthetic intent behind.
    """
    c, pid = connect(executor_dsn, EXECUTOR_ROLE)
    denied = False
    try:
        with c.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO public.paper_intents "
                    "(client_order_id,strategy,symbol,trading_day,whole_shares,"
                    "expected_entry,protective_stop) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    params(day, "F"),
                )
            except Exception as exc:
                if getattr(exc, "sqlstate", None) != "42501":
                    raise SafetyBlock("Unexpected raw-intent INSERT failure") from None
                denied = True
    finally:
        try:
            c.rollback()
        finally:
            c.close()
    if not denied:
        raise SafetyBlock("Raw intent INSERT unexpectedly accepted")
    assert_count(owner_dsn, day, 0)
    return {"pid": pid, "permission_denied": True}


def test_rollback(executor_dsn, owner_dsn, day):
    c, _ = connect(executor_dsn, EXECUTOR_ROLE)
    try:
        try:
            with c:
                with c.cursor() as cur:
                    cur.execute(FUNCTION, params(day, "F"))
                    if cur.fetchone() != (True,):
                        raise SafetyBlock("Rollback setup did not reserve")
                    raise SafetyBlock("Force transaction rollback")
        except SafetyBlock as exc:
            if str(exc) != "Force transaction rollback":
                raise
    finally:
        c.close()
    assert_count(owner_dsn, day, 0)


def cleanup(owner_dsn, keys):
    c, _ = connect(owner_dsn, OWNER_ROLE)
    try:
        with c:
            with c.cursor() as cur:
                # Delete only synthetic IDs created by THIS harness, not
                # previous test runs, production entries or historical data.
                cur.execute("DELETE FROM public.paper_intents WHERE client_order_id=ANY(%s)",
                            (list(keys),))
    finally:
        c.close()


def main():
    owner_dsn = os.environ.get(DSN_OWNER)
    exec_dsn = os.environ.get(DSN_EXEC)
    if not owner_dsn or not exec_dsn:
        print("BLOCKED: supply isolated Neon owner and executor DSNs privately.")
        return 2
    days = [BASE+timedelta(days=n) for n in (0, 1, 2, 3, 4, 5)]
    keys = {key(d, s) for d in days for s in ("F", "BAC", "AAPL", "MSFT")}
    verified = False
    fixtures_verified_empty = False
    passed = False
    cleanup_ok = True
    try:
        inspect_role(exec_dsn, owner_dsn)
        verified = True
        controls(owner_dsn, enabled=False)
        verify_empty_fixtures(owner_dsn, days)
        # NEVER clean up any fixture if it existed before this harness run.
        # If verification fails, only restore the test control switches.
        fixtures_verified_empty = True
        print("PASS: both credentials verified against isolated branch marker")
        print("PASS: restricted role lacks direct intent INSERT and control UPDATE")
        print("PASS: equity INSERT remains available; unique-day index exists")
        assert_count(owner_dsn, days[0], 0)
        blocked = parallel(exec_dsn, days[0], ["F", "BAC"])
        if any(r["accepted"] or r["state"] != "42501" for r in blocked):
            raise SafetyBlock("Disabled control did not explicitly deny every reservation")
        print("PASS: disabled-control two-session attempts")
        test_raw_insert_denied(exec_dsn, owner_dsn, days[0])
        print("PASS: restricted executor direct intent INSERT denied by PostgreSQL")
        controls(owner_dsn, enabled=True)
        four = parallel(exec_dsn, days[1], ["F", "BAC", "AAPL", "MSFT"])
        if (sum(bool(r["accepted"]) for r in four) != 1
                or sum(r["state"] == "23505" for r in four) != 3):
            raise SafetyBlock("Four-way reservation not exactly one commit and three unique-day denials")
        assert_count(owner_dsn, days[1], 1)
        print("PASS: 4 distinct PostgreSQL backends, 1 commit / 3 rejections")
        duplicate = parallel(exec_dsn, days[2], ["F", "F"])
        if (sum(bool(r["accepted"]) for r in duplicate) != 1
                or sum(r["state"] == "23505" for r in duplicate) != 1):
            raise SafetyBlock("Duplicate client ID was not deterministically refused")
        assert_count(owner_dsn, days[2], 1)
        print("PASS: duplicate ID protected by database uniqueness")
        test_rollback(exec_dsn, owner_dsn, days[3])
        print("PASS: uncommitted reservation transaction rolled back")
        race = test_guard_race(exec_dsn, owner_dsn, days[4])
        print("PASS: control-row lock held across real competing connections",
              race["lock_timeout_sqlstate"])
        test_switch_first(exec_dsn, owner_dsn, days[5])
        print("PASS: switch-first and post-switch reservations rejected")
        passed = True
    except Exception as exc:
        print("BLOCKED: acceptance failed safely:", type(exc).__name__)
    finally:
        if verified:
            try:
                controls(owner_dsn, enabled=False)
                verify_safe_controls(owner_dsn)
            except Exception:
                cleanup_ok = False
                print("BLOCKED: could not confirm safe test-branch switch restoration")
            if fixtures_verified_empty:
                try:
                    cleanup(owner_dsn, keys)
                    verify_empty_fixtures(owner_dsn, days)
                except Exception:
                    cleanup_ok = False
                    print("BLOCKED: could not verify synthetic fixture cleanup")
        if not cleanup_ok:
            passed = False
    if passed:
        print("PASS: real Neon concurrency tests and cleanup completed; no broker operations")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
