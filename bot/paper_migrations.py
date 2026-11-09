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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="list pending only")
    args = parser.parse_args(argv)
    import psycopg
    dsn = os.environ.get("NEON_OWNER_DSN", "")
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("BLOCKED: owner DSN unavailable")
        return 2
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
