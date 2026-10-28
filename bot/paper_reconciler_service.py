"""Staged independent PAPER GET reconciler, observe-only by default.

No task is installed. Status-writing service release is hard-disabled until
separate approval, migration acceptance and independent identity deployment.
Existing collector/supervisor code and credentials are not reused or changed.
"""
import argparse
import os
import time

from .paper_broker_transport import make_reconciler_paper_client
from .paper_status_reconciler import PaperStatusReconcilerJournal, reconcile_pending_once
from .paper_safety import TradeBlocked

PAPER_RECONCILIATION_SERVICE_RELEASED = False


def run_service(*, trading, journal, apply_status=False, max_ticks=1,
                interval_seconds=30, sleeper=time.sleep, alert=None):
    if apply_status is True and PAPER_RECONCILIATION_SERVICE_RELEASED is not True:
        raise TradeBlocked("PAPER status-writing service NOT RELEASED")
    if type(max_ticks) is not int or max_ticks < 0 or type(interval_seconds) is not int or interval_seconds < 30:
        raise TradeBlocked("Invalid independent reconciler supervision settings")
    if max_ticks == 0 and alert is None:
        raise TradeBlocked("Continuous reconciler requires critical alert transport")
    ticks = 0
    while max_ticks == 0 or ticks < max_ticks:
        try:
            reconcile_pending_once(trading=trading, journal=journal, record=apply_status)
        except Exception:
            if alert is not None:
                try:
                    if alert() is not True:
                        raise TradeBlocked("Independent reconciler alert unacknowledged")
                except Exception:
                    raise TradeBlocked("Independent reconciler critical alert failed") from None
            raise TradeBlocked("Independent reconciliation unresolved; block new submissions") from None
        ticks += 1
        if max_ticks == 0 or ticks < max_ticks:
            sleeper(interval_seconds)
    return ticks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-status", action="store_true")
    parser.add_argument("--continuous", action="store_true")
    args = parser.parse_args(argv)
    if args.apply_status and PAPER_RECONCILIATION_SERVICE_RELEASED is not True:
        print("BLOCKED: PAPER status-writing service NOT RELEASED")
        return 2
    names = ("PAPER_RECONCILER_DSN", "ALPACA_RECONCILER_PAPER_API_KEY", "ALPACA_RECONCILER_PAPER_SECRET_KEY")
    if any(not os.environ.get(name) for name in names):
        print("BLOCKED: independent reconciler credentials required")
        return 2
    try:
        from .monitor_notify import send_critical_ntfy, validate_topic
        from .monitor_policy import make_incident
        topic = validate_topic(os.environ.get("PAPER_MONITOR_NTFY_TOPIC"))
        alert = lambda: send_critical_ntfy(
            make_incident("critical", "unverifiable", ("independent_reconciler_failed",)), topic=topic)
        trading = make_reconciler_paper_client(os.environ[names[1]], os.environ[names[2]])
        journal = PaperStatusReconcilerJournal(os.environ[names[0]])
        ticks = run_service(trading=trading, journal=journal, apply_status=args.apply_status,
                            max_ticks=0 if args.continuous else 1, alert=alert)
        print(f"PAPER reconciler completed {ticks} ticks; broker writes=0; observe_only={not args.apply_status}")
        return 0
    except Exception:
        print("BLOCKED: independent reconciler identity/evidence/alert unavailable; broker writes=0")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
