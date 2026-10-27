"""Atomic paper-intent reservation behind Neon database emergency switch.

NO Alpaca submit_order() calls. This layer only prepares for a
future supervised worker with independently verified live order safety.
"""
from datetime import date, datetime, timedelta, timezone

from .paper_safety import (
    PaperOrderPlan, TradeBlocked, make_client_order_id, money,
)
from .paper_store import PostgresPaperJournal


class PaperExecutorJournal(PostgresPaperJournal):
    """Must use dedicated narrowly scoped PAPER writer role, never monitor."""

    def assert_executor_permissions(self, *, reservation=False):
        """Fail closed for Neon owner/admin and switch-changing DB accounts."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    "pg_has_role(current_user,'neon_superuser','MEMBER'), "
                    "(SELECT rolsuper OR rolcreaterole OR rolcreatedb OR "
                    "rolreplication OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user), "
                    "has_table_privilege(current_user,'public.paper_control','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','INSERT'), "
                    "has_column_privilege(current_user,'public.paper_intents','status','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','DELETE'), "
                    "has_table_privilege(current_user,'public.paper_audit_events','INSERT'), "
                    "has_table_privilege(current_user,'public.paper_equity_snapshots','INSERT')"
                )
                row = cur.fetchone()
        # Existing opening-equity work remains compatible with the legacy
        # restricted role and the new reservation-only INSERT boundary.
        if (not isinstance(row, (tuple, list)) or len(row) != 8
                or row[:3] != (False, False, False)
                or type(row[3]) is not bool
                or row[4:] != (True, False, True, True)):
            raise TradeBlocked("Unrestricted or improperly granted writer role")
        if reservation:
            if row[3] is not False:
                raise TradeBlocked("Direct intent INSERT bypass is forbidden")
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT has_function_privilege(current_user,"
                        "'public.paper_reserve_intent_if_enabled("
                        "text,text,text,date,integer,numeric,numeric)', 'EXECUTE')")
                    if cur.fetchone() != (True,):
                        raise TradeBlocked("Restricted reservation API unavailable")

    def record_opening_equity(self, *, session_open_utc: datetime,
                              equity, cash) -> bool:
        """Once per NYSE opening, write a broker-observed PAPER baseline.

        Only the restricted executor account can insert. Database time,
        not runner/client time, MUST be inside [open, open+5m).
        Equity observation is separate from permission to place
        broker orders. It must remain available even after a future,
        separately approved PAPER launch; it never changes either
        submission control. Missing a delayed scheduled run does
        not fabricate a baseline.
        Returns False for a previously recorded valid opening snapshot.
        """
        if (not isinstance(session_open_utc, datetime)
                or session_open_utc.tzinfo is None):
            raise TradeBlocked("NYSE opening time must be timezone-aware")
        start = session_open_utc.astimezone(timezone.utc)
        if start.second or start.microsecond:
            raise TradeBlocked("Invalid NYSE opening timestamp")
        from zoneinfo import ZoneInfo
        local = start.astimezone(ZoneInfo("America/New_York"))
        if (local.hour, local.minute) != (9, 30) or local.weekday() > 4:
            raise TradeBlocked("Not a regular NYSE trading-day opening")
        end = start + timedelta(minutes=5)
        amount, available = money(equity), money(cash)
        if (amount <= 0 or available < 0
                or available > amount * money("1.1")):
            raise TradeBlocked("Invalid Alpaca PAPER opening equity/cash")
        self.assert_executor_permissions()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('horsesdontstop-paper-opening-equity'))"
                )
                cur.fetchone()
                # This is a type check, NOT an order-release authorization.
                # PostgreSQL FOR SHARE requires UPDATE on paper_control,
                # which the restricted executor must never have. The advisory
                # lock above already serializes competing equity collectors.
                cur.execute(
                    "SELECT clock_timestamp(), kill_switch_active, "
                    "submissions_enabled FROM public.paper_control "
                    "WHERE singleton=TRUE"
                )
                row = cur.fetchone()
                if not row or len(row) != 3:
                    raise TradeBlocked("Missing Neon paper emergency control row")
                db_now, kill_switch, submissions_enabled = row
                if (not isinstance(db_now, datetime)
                        or db_now.tzinfo is None
                        or not start <= db_now < end):
                    raise TradeBlocked("Opening baseline window missed")
                # Baseline recording must work on every future
                # trading day, even if the user separately approves
                # enabling PAPER submissions later. The snapshot writer
                # is NEVER permission to submit orders and never
                # changes either control state. Only trusted booleans
                # from the singleton control are accepted.
                if type(kill_switch) is not bool or type(submissions_enabled) is not bool:
                    raise TradeBlocked("Unverifiable PAPER control state during snapshot")
                cur.execute(
                    "SELECT snapshot_id FROM public.paper_equity_snapshots "
                    "WHERE observed_at >= %s AND observed_at < %s "
                    "ORDER BY observed_at ASC LIMIT 1",
                    (start, end),
                )
                if cur.fetchone() is not None:
                    return False
                cur.execute(
                    "INSERT INTO public.paper_equity_snapshots "
                    "(observed_at, equity, cash) VALUES (%s, %s, %s) "
                    "RETURNING snapshot_id", (db_now, amount, available)
                )
                if cur.fetchone() is None:
                    raise TradeBlocked("Neon opening snapshot was not recorded")
                return True

    @staticmethod
    def validate_protected_intent(plan: PaperOrderPlan):
        if not isinstance(plan, PaperOrderPlan):
            raise TradeBlocked("Invalid paper intent plan")
        try:
            key = make_client_order_id(
                date.fromisoformat(plan.trading_day),
                plan.symbol, plan.strategy)
        except (ValueError, TypeError) as exc:
            raise TradeBlocked("Invalid session identifier") from exc
        if key != plan.client_order_id:
            raise TradeBlocked("Unexpected client order ID")
        if (plan.order_class != "oto" or plan.whole_shares < 1
                or type(plan.whole_shares) is not int):
            raise TradeBlocked("Only whole-share protected OTO supported")
        entry, stop = money(plan.estimated_entry), money(plan.protective_stop)
        cost = money(plan.estimated_cost)
        budget = money(plan.risk_budget_usd)
        expected_loss = money(plan.estimated_risk_usd)
        if (entry <= 0 or stop <= 0 or stop >= entry
                or budget <= 0 or cost <= 0
                or expected_loss < 0 or expected_loss > budget
                or cost + money('0.02') < entry * plan.whole_shares
                or expected_loss + money('0.02') <
                   (entry - stop) * plan.whole_shares):
            raise TradeBlocked("Unprotected or over-budget order intent")

    def reserve_if_enabled(self, plan: PaperOrderPlan) -> bool:
        """Atomic kill-switch+idempotency check, only when paper allowed.

        A duplicate trading-day reservation raises PostgreSQL SQLSTATE
        23505, even for the same client order ID; do not assume it returns
        False. A False result from the database function also means no
        new intent was inserted. Neither result authorizes broker submission.
        On any ambiguous outcome, reconcile broker/client ID before retrying.
        """
        self.validate_protected_intent(plan)
        self.assert_executor_permissions(reservation=True)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                # The narrowly owned database function holds advisory and
                # control-row locks, checks the kill switch, and atomically
                # inserts at most one intent per trading day. This role has
                # no raw paper_intents INSERT privilege after migration.
                cur.execute(
                    "SELECT public.paper_reserve_intent_if_enabled("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    (plan.client_order_id, plan.strategy, plan.symbol,
                     date.fromisoformat(plan.trading_day), plan.whole_shares,
                     money(plan.estimated_entry), money(plan.protective_stop)))
                row = cur.fetchone()
                if not row or len(row) != 1 or type(row[0]) is not bool:
                    raise TradeBlocked("Unverifiable reservation transaction")
                return row[0]

    def mark_uncertain(self, client_order_id):
        """Record an unknown submit outcome, NEVER blindly resubmit."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.paper_intents SET status='unknown',"
                    "updated_at=now() WHERE client_order_id=%s "
                    "AND status='reserved' RETURNING client_order_id",
                    (client_order_id,))
                if not cur.fetchone():
                    raise TradeBlocked("Cannot mark nonreserved intent unknown")
                cur.execute(
                    "INSERT INTO public.paper_audit_events "
                    "(client_order_id,kind,summary) VALUES (%s,%s,%s)",
                    (client_order_id, "submission_outcome_unknown",
                     "Broker response unavailable; do not blind retry"))
