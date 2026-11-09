"""Controlled, ledgered PAPER schema deployment (owner credential only).

Applies sql/migrations/NNN_*.sql in order, once each, recording name and
SHA-256 in public.hds_schema_migrations. A changed already-applied file is
refused. Each file manages its own BEGIN/COMMIT; a failure stops the run.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys

MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
LEDGER = """
CREATE TABLE IF NOT EXISTS public.hds_schema_migrations (
    name text PRIMARY KEY,
    sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp())
"""


def pending(applied, files):
    out = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in applied:
            if applied[path.name] != digest:
                raise SystemExit(f"Applied migration changed on disk: {path.name}")
            continue
        out.append((path, digest))
    return out


def migration_files(directory=MIGRATIONS):
    files = sorted(p for p in directory.glob("*.sql") if re.match(r"^\d{3}_", p.name))
    numbers = [p.name[:3] for p in files]
    if len(numbers) != len(set(numbers)):
        raise SystemExit("Duplicate migration number")
    return files


OPERATOR_LOGIN = "hds_paper_operator_login"


def provision_operator(dsn):
    """Create/rotate the restricted operator LOGIN; print its DSN to stdout only.

    Created by SQL (not the Neon console) so it never inherits neon_superuser.
    Its only privileges come from hds_paper_operator: pause/arm + status reads.
    """
    import secrets
    from urllib.parse import urlsplit, quote
    import psycopg
    from psycopg import sql
    password = secrets.token_urlsafe(32)
    with psycopg.connect(dsn, sslmode="require", connect_timeout=10, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                              (OPERATOR_LOGIN,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(verb + " ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                             "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
            sql.Identifier(OPERATOR_LOGIN), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT hds_paper_operator TO {}").format(
            sql.Identifier(OPERATOR_LOGIN)))
        bad = conn.execute(
            "SELECT pg_has_role(%s,'neon_superuser','MEMBER') OR "
            "pg_has_role(%s,'neondb_owner','MEMBER')", (OPERATOR_LOGIN, OPERATOR_LOGIN)).fetchone()
        if bad != (False,):
            raise SystemExit("Operator login unexpectedly privileged; refusing")
    host = urlsplit(dsn).hostname
    print(f"postgresql://{OPERATOR_LOGIN}:{quote(password, safe='')}@{host}:5432/neondb"
          "?sslmode=require&channel_binding=require")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="list pending only")
    parser.add_argument("--provision-operator", action="store_true")
    args = parser.parse_args(argv)
    import psycopg
    dsn = os.environ.get("NEON_OWNER_DSN", "")
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("BLOCKED: owner DSN unavailable")
        return 2
    if args.provision_operator:
        return provision_operator(dsn)
    with psycopg.connect(dsn, sslmode="require", connect_timeout=10,
                         autocommit=True) as conn:
        conn.execute(LEDGER)
        applied = dict(conn.execute(
            "SELECT name, sha256 FROM public.hds_schema_migrations").fetchall())
        todo = pending(applied, migration_files())
        for path, digest in todo:
            print(("PENDING " if args.plan else "APPLYING ") + path.name)
            if args.plan:
                continue
            try:
                conn.execute(path.read_text(encoding="utf-8"))
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                print(f"FAILED {path.name}: {type(exc).__name__}: "
                      f"{getattr(getattr(exc, 'diag', None), 'message_primary', '')}")
                return 2
            conn.execute("INSERT INTO public.hds_schema_migrations(name, sha256) "
                         "VALUES (%s, %s)", (path.name, digest))
            print("APPLIED " + path.name)
    if not todo:
        print("Schema up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
