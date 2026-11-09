"""Operator controls through restricted logins.

Actions (PAPER_OPERATOR_ACTION):
  stop    - paper_engage_kill_switch via the executor login (any bot role may pause)
  arm     - paper_arm_autonomous_trading via hds_paper_operator_login (human password)
  status  - read controls and policy (monitor login)
  verify  - log in as the operator and prove its privileges WITHOUT changing state
  verifier- print CREATE ROLE SQL with a SCRAM-SHA-256 verifier (no DB connection)
"""
import base64
import hashlib
import hmac
import os
import secrets
import sys
import unicodedata

OPERATOR_LOGIN = "hds_paper_operator_login"


def scram_sha256_verifier(password: str, *, salt: bytes | None = None,
                          iterations: int = 4096) -> str:
    """PostgreSQL-compatible SCRAM-SHA-256 verifier (RFC 5802/7677).

    Only the verifier leaves this machine; PostgreSQL stores it as given, so the
    plaintext password never passes through Neon, chat or the trading host.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password required")
    normalized = unicodedata.normalize("NFKC", password)
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def operator_login_sql(verifier: str) -> str:
    if not verifier.startswith("SCRAM-SHA-256$") or "'" in verifier:
        raise ValueError("invalid verifier")
    return (
        "-- Creates the human-only operator login. Contains a SCRAM verifier, not the password.\n"
        "-- It inherits ONLY hds_paper_operator (arm, pause, read controls/policy).\n"
        "DO $op$\nBEGIN\n"
        f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{OPERATOR_LOGIN}') THEN\n"
        f"    ALTER ROLE {OPERATOR_LOGIN} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE\n"
        f"      NOREPLICATION NOBYPASSRLS PASSWORD '{verifier}';\n"
        "  ELSE\n"
        f"    CREATE ROLE {OPERATOR_LOGIN} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE\n"
        f"      NOREPLICATION NOBYPASSRLS PASSWORD '{verifier}';\n"
        "  END IF;\nEND;\n$op$;\n"
        f"GRANT hds_paper_operator TO {OPERATOR_LOGIN};\n"
    )


VERIFY_SQL = """
SELECT current_user,
  has_function_privilege(current_user, to_regprocedure('public.paper_arm_autonomous_trading(text)'), 'EXECUTE'),
  has_function_privilege(current_user, to_regprocedure('public.paper_engage_kill_switch(text)'), 'EXECUTE'),
  (SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
     FROM pg_roles WHERE rolname = current_user),
  pg_has_role(current_user, 'neon_superuser', 'MEMBER') OR pg_has_role(current_user, 'neondb_owner', 'MEMBER')
    OR pg_has_role(current_user, 'hds_paper_executor', 'MEMBER')
    OR pg_has_role(current_user, 'hds_paper_reconciler', 'MEMBER'),
  has_schema_privilege(current_user, 'public', 'CREATE'),
  EXISTS (SELECT 1 FROM (VALUES ('public.paper_control'), ('public.paper_intents'),
            ('public.paper_trading_policy'), ('public.paper_audit_events'),
            ('public.paper_pilot_approvals')) v(t)
          WHERE has_table_privilege(current_user, t, 'INSERT,UPDATE,DELETE,TRUNCATE')),
  EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE n.nspname = 'public' AND p.prosecdef
            AND has_function_privilege(current_user, p.oid, 'EXECUTE')
            AND p.proname NOT IN ('paper_arm_autonomous_trading', 'paper_engage_kill_switch'))
"""
EXPECTED_VERIFY = (OPERATOR_LOGIN, True, True, False, False, False, False, False)


def main():
    action = os.environ.get("PAPER_OPERATOR_ACTION")
    if action == "verifier":
        password = os.environ.get("PAPER_OPERATOR_NEW_PASSWORD", "")
        if len(password) < 20:
            print("BLOCKED: choose an operator password of at least 20 characters")
            return 2
        print(operator_login_sql(scram_sha256_verifier(password)))
        return 0
    import psycopg
    reason = "operator: " + (os.environ.get("PAPER_OPERATOR_REASON") or "unspecified")
    with psycopg.connect(os.environ["PAPER_OPERATOR_DSN"], sslmode="require",
                         connect_timeout=10) as conn:
        if action == "verify":
            row = conn.execute(VERIFY_SQL).fetchone()
            ok = tuple(row) == EXPECTED_VERIFY
            print("Operator login privileges:", "VERIFIED" if ok else f"UNEXPECTED {tuple(row)}")
            print("  can arm, can pause, no role/table/schema privileges, no bot-role membership"
                  if ok else "  do not use this login until fixed")
            if not ok:
                return 2
        elif action == "stop":
            conn.execute("SELECT public.paper_engage_kill_switch(%s)", (reason,))
        elif action == "arm":
            conn.execute("SELECT public.paper_arm_autonomous_trading(%s)", (reason,))
        elif action != "status":
            print("BLOCKED: unknown operator action")
            return 2
        conn.commit()
        kill, enabled = conn.execute(
            "SELECT kill_switch_active, submissions_enabled FROM public.paper_control").fetchone()
        policy = conn.execute(
            "SELECT autonomous_entries_enabled, max_trade_risk_fraction, "
            "max_position_fraction, max_entries_per_day, entry_window_start, "
            "entry_window_end FROM public.paper_trading_policy").fetchone()
    state = ("PAUSED" if (kill, enabled) == (True, False) else
             "ARMED" if (kill, enabled) == (False, True) else "INCOHERENT")
    print(f"PAPER controls: {state} (kill={kill}, submissions={enabled})")
    print("Policy: autonomous_entries=%s risk<=%s of opening equity, position<=%s, "
          "entries/day<=%s, window %s-%s NY" % policy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
