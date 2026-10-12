"""Snapshot Alpaca PAPER equity ONLY during 09:30:00–09:34:59 NY.

Uses the existing narrowly restricted journal executor role and broker
READ-ONLY API methods. No broker orders, no kill-switch changes.
GitHub Actions cron can be late: absent baseline stays a trading blocker.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
import os
import sys
from zoneinfo import ZoneInfo

from .paper_execution_journal import PaperExecutorJournal
from .paper_safety import PAPER_ENDPOINT, TradeBlocked, money


NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OpeningSnapshotResult:
    state: str
    trading_day: str
    observed_open_utc: str | None
    wrote_equity_snapshot: bool


def opening_window(now: datetime):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Opening snapshot needs timezone-aware time")
    now = now.astimezone(timezone.utc)
    local = now.astimezone(NY)
    opening_local = datetime.combine(
        local.date(), time(9, 30), tzinfo=NY)
    opened_at = opening_local.astimezone(timezone.utc)
    if (local.weekday() > 4
            or not opened_at <= now < opened_at + timedelta(minutes=5)):
        return None
    return opened_at


def snapshot_one_opening(trading, journal, *, now=None, after_read_now=None):
    now = now or datetime.now(timezone.utc)
    started = opening_window(now)
    local_day = now.astimezone(NY).date().isoformat()
    if started is None:
        return OpeningSnapshotResult("outside_opening_window", local_day, None, False)
    # Require the restricted SQL role, not a generic Neon owner or monitor.
    journal.assert_executor_permissions()
    try:
        clock = trading.get_clock()
        account = trading.get_account()
    except Exception:
        raise TradeBlocked("Alpaca PAPER account/clock read failed") from None
    if clock.is_open is not True:
        return OpeningSnapshotResult("broker_market_not_open", local_day, None, False)
    # Account trading restrictions must block ORDERS, not a read-only
    # risk baseline. A blocked PAPER account still needs its equity
    # observed so daily/peak risk reporting cannot go blind.
    amount, cash = money(account.equity), money(account.cash)
    if amount <= 0 or cash < 0 or cash > amount * money("1.1"):
        raise TradeBlocked("Invalid PAPER account cash/equity")
    # External requests can be delayed; never use an old 09:30 quote
    # to backfill a missed five-minute baseline.
    finished_window = opening_window(after_read_now or datetime.now(timezone.utc))
    if finished_window != started:
        raise TradeBlocked(
            "Opening snapshot window expired or changed during account read"
        )
    inserted = journal.record_opening_equity(
        session_open_utc=started, equity=amount, cash=cash)
    return OpeningSnapshotResult(
        state="recorded" if inserted else "already_recorded",
        trading_day=local_day,
        observed_open_utc=started.isoformat(),
        wrote_equity_snapshot=bool(inserted),
    )


def main():
    from alpaca.trading.client import TradingClient

    dsn = os.getenv("PAPER_EXECUTOR_DSN")
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not all((dsn, key, secret)):
        print("BLOCKED: PAPER-only secrets missing; no writes.", file=sys.stderr)
        return 2
    try:
        broker = TradingClient(key, secret, paper=True,
                               url_override=PAPER_ENDPOINT)
        journal = PaperExecutorJournal(dsn)
        report = snapshot_one_opening(broker, journal)
    except Exception:
        # Never expose a DSN, account number or broker error in CI logs.
        print(
            "BLOCKED: opening-equity collection failed. "
            "Missing baseline continues to block trading.",
            file=sys.stderr,
        )
        return 2
    print(
        f"PAPER OPEN BASELINE: {report.state}, "
        f"NY session {report.trading_day}, "
        f"Neon insert {report.wrote_equity_snapshot}; "
        "ZERO broker orders; kill switch unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
