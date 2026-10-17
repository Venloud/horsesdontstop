"""Read-only Alpaca PAPER check before intentionally idling Neon off-hours.

This is NOT a trading gate or a trading calendar. The caller must first
obtain a healthy, flat durable journal baseline from the SAME supervisor
process. This probe establishes current broker flatness on every idle tick.
No Neon/heartbeat access or Alpaca order writes are performed here.
"""


def broker_confirms_closed_flat(trading) -> bool:
    """Fail closed on any incomplete or uncertain broker observation.

    Only an authenticated PAPER TradingClient should be passed by the
    caller. An exchange clock that is unavailable or not *exactly* closed,
    unknown positions, or unknown open orders must force the FULL journal
    poll instead of allowing the Neon idle path.
    """
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        clock = trading.get_clock()
        if getattr(clock, "is_open", None) is not False:
            return False
        positions = trading.get_all_positions()
        if not isinstance(positions, (list, tuple)) or positions:
            return False
        orders = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=500, nested=True))
        return isinstance(orders, (list, tuple)) and len(orders) == 0
    except Exception:
        # Broker/client exceptions can contain private credentials or IDs.
        # Never log the exception, and never infer zero exposure from errors.
        return False


def verified_flat_journal_baseline(report) -> bool:
    """Require a prior complete and consistent *journal+broker* observation.

    A lookalike or partial report never permits Neon to be idled. This
    attestation is deliberately narrower than general read-only health.
    """
    if not isinstance(report, dict):
        return False
    reconciliation = report.get("reconciliation")
    if not isinstance(reconciliation, dict):
        return False
    from .paper_supervisor_loop import valid_observation
    return (
        valid_observation(report)
        and report.get("journal_kill_switch_active") is True
        and report.get("journal_submissions_enabled") is False
        and report.get("code_submissions_enabled") is False
        and type(reconciliation.get("open_broker_positions")) is int
        and reconciliation["open_broker_positions"] == 0
        and type(reconciliation.get("open_broker_orders")) is int
        and reconciliation["open_broker_orders"] == 0
        and type(reconciliation.get("unresolved_journal_intents")) is int
        and reconciliation["unresolved_journal_intents"] == 0
    )
