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

    def assert_executor_permissions(self):
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
        if row != (False, False, False, True, True, False, True, True):
            raise TradeBlocked("Unrestricted or improperly granted writer role")

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

        False means order ID already reserved. Neither True nor False
        authorizes a broker submission by itself. If the broker call
        later becomes ambiguous, the supervisor must perform client-ID
        reconciliation before taking any further trading action.
        """
        self.validate_protected_intent(plan)
        self.assert_executor_permissions()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                # Account-wide advisory lock serializes different symbols
                # across simultaneously started scheduled jobs.
                cur.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('horsesdontstop-paper-exclusive-intent'))")
                cur.fetchone()
                cur.execute(
                    "SELECT kill_switch_active, submissions_enabled "
                    "FROM public.paper_control WHERE singleton=TRUE FOR SHARE"
                )
                if cur.fetchone() != (False, True):
                    raise TradeBlocked("Paper kill switch active or not approved")
                cur.execute(
                    "SELECT COUNT(*) FROM public.paper_intents "
                    "WHERE trading_day=%s", (plan.trading_day,))
                today = cur.fetchone()
                if today is None or today[0] != 0:
                    raise TradeBlocked(
                        "Only one protected PAPER order intent per NY trading day"
                    )
                cur.execute(
                    "INSERT INTO public.paper_intents "
                    "(client_order_id,strategy,symbol,trading_day,whole_shares,"
                    "expected_entry,protective_stop) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (client_order_id) DO NOTHING "
                    "RETURNING client_order_id",
                    (plan.client_order_id, plan.strategy, plan.symbol,
                     plan.trading_day, plan.whole_shares,
                     plan.estimated_entry, plan.protective_stop))
                return cur.fetchone() is not None

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
