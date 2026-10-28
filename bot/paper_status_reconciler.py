"""Staged PAPER-only broker GET -> separate-role journal reconciliation.

NO scheduler or production entrypoint. NEVER submit/cancel/replace an order,
toggle controls, or trust an executor-supplied broker status. Requires the
review-only PR #41 PostgreSQL PREPARE function and a separately authenticated
reconciler login; neither has been deployed. No login DSN is stored here.

Conservative by design: a filled entry is journal-promoted ONLY while an
independently GET-verified active matching GTC stop covers live exposure.
Unknown/not-found/partial/ambiguous states remain unresolved and blocked.
"""
from datetime import date

from .paper_broker_recovery import recover_by_client_id
from .paper_safety import PAPER_ENDPOINT, PaperOrderPlan, TradeBlocked, money
from .paper_store import PostgresPaperJournal, transition_allowed
from .paper_role_boundary import assert_journal_acl_boundary


# Broker status is untrusted until full nested order + live position GETs pass.
# Conservatively do not promote partially_filled, expired, or unknown.
BROKER_TO_JOURNAL = {
    "new": "submitted",
    "pending_new": "submitted",
    "accepted": "accepted",
    "accepted_for_bidding": "accepted",
    "filled": "filled",
    "rejected": "rejected",
    "canceled": "canceled",
}


class PaperStatusReconcilerJournal(PostgresPaperJournal):
    """Only a separate reconciler credential, never the order executor.

    A future deployment must use a dedicated LOGIN with inherited/active
    reconciler grants. Do not use hds_paper_executor or a Neon owner DSN.
    """

    def assert_reconciler_permissions(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    "pg_has_role(current_user,'hds_paper_reconciler','MEMBER'), "
                    "pg_has_role(current_user,'hds_paper_executor','MEMBER'), "
                    "pg_has_role(current_user,'neon_superuser','MEMBER'), "
                    "(SELECT rolsuper OR rolcreaterole OR rolcreatedb OR "
                    "rolreplication OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user), "
                    "has_table_privilege(current_user,'public.paper_control','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','INSERT'), "
                    "has_column_privilege(current_user,'public.paper_intents','status','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_audit_events','INSERT'), "
                    "has_table_privilege(current_user,'public.paper_intents','SELECT'), "
                    "has_function_privilege(current_user,"
                    "'public.paper_record_reconciled_intent_status(text,text,text,text)',"
                    "'EXECUTE')"
                )
                row = cur.fetchone()
                if row == (True, False, False, False, False, False,
                           False, False, True, True):
                    assert_journal_acl_boundary(cur, reconciler=True)
        if row != (True, False, False, False, False, False,
                   False, False, True, True):
            raise TradeBlocked("Unsafe or incomplete PAPER broker-reconciler role")

    def get_durable_intent_terms(self, client_order_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, broker_order_id, strategy, symbol, trading_day, "
                    "whole_shares, expected_entry, protective_stop "
                    "FROM public.paper_intents WHERE client_order_id=%s",
                    (client_order_id,))
                return cur.fetchone()

    def read_pending_plans(self):
        """Rebuild reconciliation inputs after a restart; never reserve/send."""
        self.assert_reconciler_permissions()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT client_order_id,strategy,symbol,trading_day,whole_shares,"
                    "expected_entry,protective_stop FROM public.paper_intents "
                    "WHERE status IN ('reserved','submitted','accepted','unknown') "
                    "ORDER BY created_at LIMIT 501")
                rows = cur.fetchall()
        if len(rows) >= 501:
            raise TradeBlocked("Pending reconciler intent list incomplete")
        plans = []
        for cid, strategy, symbol, day, qty, entry, stop in rows:
            # Derived numbers exist only to reconstruct the durable terms;
            # this service cannot reserve or submit an order.
            cost = money(entry) * qty
            risk = (money(entry) - money(stop)) * qty
            plans.append(PaperOrderPlan(
                symbol=symbol, strategy=strategy, trading_day=day.isoformat(),
                client_order_id=cid, whole_shares=qty, estimated_entry=entry,
                protective_stop=stop, estimated_cost=cost,
                risk_budget_usd=risk, estimated_risk_usd=risk))
        return plans

    def record_broker_verified_state(self, client_order_id, old, new, broker_id):
        """Call restricted function, never direct UPDATE or audit INSERT.

        The DB enforces expected status and atomic audit even if a broker
        state changes concurrently. This function alone does not prove the
        external broker supplied the evidence; caller must validate GETs.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT public.paper_record_reconciled_intent_status("
                    "%s,%s,%s,%s)",
                    (client_order_id, old, new, broker_id))
                if cur.fetchone() != (True,):
                    raise TradeBlocked("PAPER reconciled transition not committed")


def _durable_matches_plan(plan, row):
    if not isinstance(row, (tuple, list)) or len(row) != 8:
        return False
    old, original_broker_id, strategy, symbol, trading_day, shares, entry, stop = row
    try:
        return (
            isinstance(old, str)
            and (original_broker_id is None
                 or isinstance(original_broker_id, str)
                 and len(original_broker_id) > 0)
            and strategy == plan.strategy
            and symbol == plan.symbol
            and trading_day == date.fromisoformat(plan.trading_day)
            and type(shares) is int and shares == plan.whole_shares
            and money(entry) == money(plan.estimated_entry)
            and money(stop) == money(plan.protective_stop)
        )
    except (ValueError, TypeError, TradeBlocked):
        return False


def reconcile_verified_paper_order(*, trading, journal, plan, record=True):
    """Reconcile ONE persisted intent, using only Alpaca PAPER read-only GETs.

    The journal is intentionally a separate service principal. Do not
    accept a caller's chosen new state or broker ID. Never authorize a
    broker POST from any result. Raises TradeBlocked on missing evidence,
    partially filled or unprotected exposure, stale/terminal journal state,
    mismatched DB terms, and identity or API inconsistencies.
    """
    if not isinstance(plan, PaperOrderPlan):
        raise TradeBlocked("No durable protected PAPER plan")
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        raise TradeBlocked("Reconciler is not bound to Alpaca PAPER")
    journal.assert_reconciler_permissions()
    row = journal.get_durable_intent_terms(plan.client_order_id)
    if not _durable_matches_plan(plan, row):
        raise TradeBlocked("Durable approved PAPER intent mismatch or missing")
    old, original_broker_id = row[:2]
    if old not in ("reserved", "submitted", "accepted", "unknown"):
        raise TradeBlocked("Terminal PAPER intent cannot be rewritten")

    try:
        positions = trading.get_all_positions()
    except Exception:
        raise TradeBlocked("Broker position GET unavailable") from None
    if not isinstance(positions, (list, tuple)):
        raise TradeBlocked("Broker position list unverifiable")
    if len(positions) > 1:
        raise TradeBlocked("Multiple broker positions require manual reconciliation")
    if positions and getattr(positions[0], "symbol", None) != plan.symbol:
        raise TradeBlocked("Unexpected broker position symbol")
    exposure = getattr(positions[0], "qty", None) if positions else 0
    try:
        audit = recover_by_client_id(trading, plan, position_qty=exposure)
    except Exception:
        raise TradeBlocked("Broker order and stop evidence unavailable") from None
    if audit.blockers or not audit.parent_order_id:
        raise TradeBlocked("Unresolved broker order/stop evidence")
    new = BROKER_TO_JOURNAL.get(audit.status)
    unchanged = new == old and original_broker_id == audit.parent_order_id
    if new is None or not (unchanged or transition_allowed(old, new)):
        raise TradeBlocked("Unverified or invalid broker state transition")
    if original_broker_id is not None and original_broker_id != audit.parent_order_id:
        raise TradeBlocked("Broker order ID conflicts with durable journal")
    if new == "filled" and (
            audit.has_verified_protective_stop is not True
            or audit.paper_exposure_shares <= 0):
        raise TradeBlocked("Filled order lacks proven active GTC stop coverage")
    if new in ("submitted", "accepted", "rejected", "canceled") and (
            audit.paper_exposure_shares != 0):
        raise TradeBlocked("Broker status inconsistent with live exposure")

    if record is True and not unchanged:
        journal.record_broker_verified_state(
            plan.client_order_id, old, new, audit.parent_order_id)
    return new


def reconcile_pending_once(*, trading, journal, record=True):
    """Bounded service tick, including durable restart recovery.

    The surrounding trusted supervisor must alert on ANY TradeBlocked and
    continue read-only protection checks for terminal filled positions.
    Deployment and independent credentials require separate owner approval.
    """
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        raise TradeBlocked("Reconciler is not bound to Alpaca PAPER")
    plans = journal.read_pending_plans()
    return tuple((plan.client_order_id, reconcile_verified_paper_order(
        trading=trading, journal=journal, plan=plan, record=record)) for plan in plans)
