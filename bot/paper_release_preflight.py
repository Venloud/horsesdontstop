"""Read-only PAPER release preflight. Never writes to Neon or the broker.

Run through ops/windows/paper-release-preflight.ps1. Prints PASS/FAIL/INFO per
gate and exits 0 only if every required gate passes for the requested stage:
  --stage locked     : current safe state (flags off, PAUSED, policy disabled)
  --stage supervised : ready for ONE supervised autonomous entry
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _age(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(data["observed_at_utc"].replace("Z", "+00:00"))
        return data, (datetime.now(timezone.utc) - observed).total_seconds()
    except Exception:
        return None, None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("locked", "supervised"), required=True)
    args = parser.parse_args(argv)
    supervised = args.stage == "supervised"
    results = []

    def gate(name, ok, detail="", required=True):
        results.append((("PASS" if ok else "FAIL") if required else "INFO", name, detail))

    from .paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED as entries
    from .paper_reconciler_service import PAPER_RECONCILIATION_SERVICE_RELEASED as recon
    gate("code: order submissions flag", entries is supervised, f"={entries}")
    gate("code: reconciler status-write flag", recon is supervised, f"={recon}")

    from .paper_safety import PAPER_ENDPOINT
    gate("code: PAPER endpoint pinned", PAPER_ENDPOINT == "https://paper-api.alpaca.markets")

    import psycopg
    from .paper_store import PostgresPaperJournal
    from .paper_execution_journal import PaperExecutorJournal
    from .paper_status_reconciler import PaperStatusReconcilerJournal
    for name, check in (
            ("neon: monitor role read-only", lambda: PostgresPaperJournal(
                os.environ["PAPER_JOURNAL_DSN"]).assert_monitor_only()),
            ("neon: executor role + ACL boundary", lambda: PaperExecutorJournal(
                os.environ["PAPER_EXECUTOR_DSN"]).assert_executor_permissions(reservation=True)),
            ("neon: reconciler role + ACL boundary", lambda: PaperStatusReconcilerJournal(
                os.environ["PAPER_RECONCILER_DSN"]).assert_reconciler_permissions())):
        try:
            check(); gate(name, True)
        except Exception as exc:
            gate(name, False, type(exc).__name__)
    arm = "to_regprocedure('public.paper_arm_autonomous_trading(text)')"
    for label, env in (("executor", "PAPER_EXECUTOR_DSN"), ("reconciler", "PAPER_RECONCILER_DSN")):
        with psycopg.connect(os.environ[env], sslmode="require", connect_timeout=10) as c:
            can_arm = c.execute(f"SELECT has_function_privilege(current_user,{arm},'EXECUTE')").fetchone()[0]
        gate(f"neon: {label} cannot arm", can_arm is False)
    with psycopg.connect(os.environ["PAPER_JOURNAL_DSN"], sslmode="require", connect_timeout=10) as c:
        kill, enabled = c.execute("SELECT kill_switch_active, submissions_enabled FROM paper_control").fetchone()
        policy = c.execute("SELECT autonomous_entries_enabled, max_trade_risk_fraction, "
                           "max_entries_per_day FROM paper_trading_policy").fetchone()
        open_intents = c.execute("SELECT count(*) FROM paper_intents WHERE status IN "
                                 "('reserved','submitted','accepted','unknown')").fetchone()[0]
        today = c.execute("SELECT count(*) FROM paper_intents WHERE status <> 'expired' AND "
                          "trading_day = (clock_timestamp() AT TIME ZONE 'America/New_York')::date").fetchone()[0]
        opening = c.execute(
            "SELECT min(equity) FROM paper_equity_snapshots WHERE "
            "(observed_at AT TIME ZONE 'America/New_York')::date = (clock_timestamp() AT TIME ZONE 'America/New_York')::date "
            "AND (observed_at AT TIME ZONE 'America/New_York')::time >= '09:30' "
            "AND (observed_at AT TIME ZONE 'America/New_York')::time < '09:35'").fetchone()[0]
        login = c.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hds_paper_operator_login' "
                          "AND rolcanlogin AND pg_has_role(oid,'hds_paper_operator','MEMBER'))").fetchone()[0]
    if supervised:
        gate("neon: controls ARMED (operator)", (kill, enabled) == (False, True),
             f"kill={kill} submissions={enabled}")
    else:
        gate("neon: controls PAUSED (kill ON)", (kill, enabled) == (True, False),
             f"kill={kill} submissions={enabled}")
    gate("neon: policy entries", policy[0] is supervised, f"enabled={policy[0]}")
    gate("neon: policy risk 1% and 1 entry/day", (float(policy[1]), policy[2]) == (0.01, 1))
    gate("neon: no open intents", open_intents == 0, f"open={open_intents}")
    gate("neon: today's entry unused", today == 0, f"used={today}", required=supervised)
    gate("neon: today's opening equity recorded", opening is not None, f"equity={opening}",
         required=supervised)
    gate("neon: operator login provisioned", login is True, f"exists={login}", required=supervised)

    from .paper_broker_transport import make_reconciler_paper_client
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    broker = make_reconciler_paper_client(os.environ["ALPACA_PAPER_API_KEY"],
                                          os.environ["ALPACA_PAPER_SECRET_KEY"])
    account = broker.get_account()
    from uuid import UUID
    digest = hashlib.sha256(str(UUID(str(account.id))).lower().encode()).hexdigest()
    gate("alpaca: pinned PAPER account", digest == os.environ.get("PAPER_ACCOUNT_SHA256"))
    gate("alpaca: account not blocked", account.trading_blocked is False)
    positions = broker.get_all_positions()
    orders = broker.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500))
    gate("alpaca: flat (no positions/orders)", not positions and not orders,
         f"positions={len(positions)} orders={len(orders)}")
    clock = broker.get_clock()
    gate("alpaca: market open", clock.is_open is True, f"is_open={clock.is_open}", required=supervised)
    gate("alpaca: equity", True, f"equity={account.equity} buying_power={account.buying_power}",
         required=False)
    from .paper_broker_transport import ReadOnlyPaperSession
    # Type check only: never send a write request to prove it would fail.
    gate("transport: reconciler client is GET-only",
         type(broker._session) is ReadOnlyPaperSession)

    folder = Path(os.environ["LOCALAPPDATA"]) / "horsesdontstop"
    sup, sup_age = _age(folder / "supervisor-runtime.json")
    rec, rec_age = _age(folder / "reconciler-runtime.json")
    gate("supervisor: receipt fresh (<=120s)", sup_age is not None and 0 <= sup_age <= 120,
         f"age={None if sup_age is None else round(sup_age)}s status={sup and sup.get('status')}")
    gate("supervisor: active poll with external ACK",
         bool(sup) and sup.get("status") == "read_only_poll_ok" and sup.get("external_ack") is True,
         f"status={sup and sup.get('status')} ack={sup and sup.get('external_ack')}",
         required=supervised)
    gate("reconciler: receipt fresh (<=120s)", rec_age is not None and 0 <= rec_age <= 120,
         f"age={None if rec_age is None else round(rec_age)}s")

    width = max(len(r[1]) for r in results)
    for status, name, detail in results:
        print(f"{status:4}  {name.ljust(width)}  {detail}")
    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\nSTAGE {args.stage.upper()}: " + ("READY" if not failed else f"NOT READY ({len(failed)} failing)"))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
