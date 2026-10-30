"""Atomic paper-intent reservation behind Neon database emergency switch.

NO Alpaca submit_order() calls. This layer only prepares for a
future supervised worker with independently verified live order safety.
"""
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager

from .paper_safety import (
    PaperOrderPlan, TradeBlocked, make_client_order_id, money,
)
from .paper_store import PostgresPaperJournal
from .paper_role_boundary import assert_journal_acl_boundary


class PaperExecutorJournal(PostgresPaperJournal):
    """Must use dedicated narrowly scoped PAPER writer role, never monitor."""

    def assert_executor_permissions(self, *, reservation=False):
        """Post-PR38 + status cutover only, never allow legacy direct writes.

        This intentionally fails against unmigrated production. The existing
        collector running from main is NOT changed by a review-only branch.
        """
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
                    "has_column_privilege(current_user,'public.paper_intents','broker_order_id','UPDATE'), "
                    "has_column_privilege(current_user,'public.paper_intents','updated_at','UPDATE'), "
                    "has_table_privilege(current_user,'public.paper_intents','DELETE'), "
                    "has_table_privilege(current_user,'public.paper_audit_events','INSERT'), "
                    "has_table_privilege(current_user,'public.paper_equity_snapshots','INSERT'), "
                    "has_function_privilege(current_user,"
                    "'public.paper_reserve_intent_if_enabled("
                    "text,text,text,date,integer,numeric,numeric)','EXECUTE'), "
                    "has_function_privilege(current_user,"
                    "'public.paper_mark_reserved_intent_unknown(text)','EXECUTE'), "
                    "has_function_privilege(current_user,"
                    "'public.paper_record_reconciled_intent_status("
                    "text,text,text,text)','EXECUTE'), "
                    "pg_has_role(current_user,'hds_paper_reconciler','MEMBER'), "
                    "pg_has_role(current_user,'hds_paper_status_owner','MEMBER'), "
                    "pg_has_role(current_user,'hds_paper_reservation_guard','MEMBER'), "
                    "pg_has_role(current_user,'neondb_owner','MEMBER'), "
                    "has_schema_privilege(current_user,'public','CREATE'), "
                    "has_database_privilege(current_user,current_database(),'CREATE')"
                )
                row = cur.fetchone()
                if row == (False, False, False, False, False, False, False,
                           False, False, True, True, True, False, False, False,
                           False, False, False, False):
                    assert_journal_acl_boundary(cur)
        expected = (False, False, False, False, False, False, False,
                    False, False, True, True, True, False, False, False, False, False, False, False)
        if row != expected:
            raise TradeBlocked("Unsafe or incomplete post-cutover PAPER executor role")

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
        if (plan.order_class != "oto"
                or plan.time_in_force not in {"day", "gtc"}
                or plan.whole_shares < 1
                or type(plan.whole_shares) is not int):
            raise TradeBlocked("Only whole-share protected OTO supported")
        entry, stop = money(plan.estimated_entry), money(plan.protective_stop)
        cost = money(plan.estimated_cost)
        budget = money(plan.risk_budget_usd)
        expected_loss = money(plan.estimated_risk_usd)
        if (entry <= 0 or stop <= 0 or stop >= entry
                or budget <= 0 or cost <= 0
                or expected_loss < 0 or expected_loss > budget
                or (plan.time_in_force == "gtc"
                    and cost > money("5.00"))
                or cost + money('0.02') < entry * plan.whole_shares
                or expected_loss + money('0.02') <
                   (entry - stop) * plan.whole_shares):
            raise TradeBlocked("Unprotected or over-budget order intent")

    def reserve_if_enabled(self, plan: PaperOrderPlan) -> bool:
        """PR38 atomic reservation: no executor direct INSERT privilege.

        Refuse if the DB guard and corresponding post-cutover role have not
        both been installed. Duplicate-day SQLSTATE 23505 is not a permit
        to send another order; errors must fail closed.
        """
        self.validate_protected_intent(plan)
        self.assert_executor_permissions(reservation=True)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute(
                    "SELECT public.paper_reserve_intent_if_enabled("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    (plan.client_order_id, plan.strategy, plan.symbol,
                     date.fromisoformat(plan.trading_day), plan.whole_shares,
                     money(plan.estimated_entry), money(plan.protective_stop)))
                row = cur.fetchone()
                if (not isinstance(row, tuple) or len(row) != 1
                        or type(row[0]) is not bool):
                    raise TradeBlocked("Unverifiable protected reservation")
                return row[0]

    def mark_uncertain(self, client_order_id):
        """Atomically mark reserved -> unknown, audit, NEVER blindly retry.

        Requires the separately reviewed PAPER status prepare migration.
        This is staged behind the unreleased broker gateway and MUST NOT
        be enabled against an unmigrated database.
        """
        if not isinstance(client_order_id, str) or not client_order_id:
            raise TradeBlocked("Missing PAPER client ID")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT public.paper_mark_reserved_intent_unknown(%s)",
                    (client_order_id,))
                if cur.fetchone() != (True,):
                    raise TradeBlocked("Cannot mark nonreserved intent unknown")

    @contextmanager
    def authorize_reserved_submission(self, plan):
        """Consume the reservation before POST, then hold the control row lock.

        UNKNOWN is committed separately first: a crash or transaction rollback
        after sending cannot restore a reusable reserved permit. The second
        transaction locks controls until the one attempted POST completes.
        Requires the separately approved Phase6 reauthorization migration.
        """
        self.validate_protected_intent(plan)
        self.assert_executor_permissions(reservation=True)
        self.mark_uncertain(plan.client_order_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute("SET LOCAL lock_timeout = '3000ms'")
                cur.execute(
                    "SELECT public.paper_authorize_unknown_submission("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    (plan.client_order_id, plan.strategy, plan.symbol,
                     date.fromisoformat(plan.trading_day), plan.whole_shares,
                     money(plan.estimated_entry), money(plan.protective_stop)))
                if cur.fetchone() != (True,):
                    raise TradeBlocked("PAPER final submission authorization denied")
                # Separate committed receipt survives rollback of this lock
                # transaction, including a broker timeout or lost connection.
                self.consume_submission_authorization(plan.client_order_id)
                def assert_lock_alive():
                    # Use this SAME connection: a fresh connection could hide
                    # termination of the backend holding the control lock.
                    cur.execute("SELECT kill_switch_active,submissions_enabled "
                                "FROM public.paper_control WHERE singleton=TRUE")
                    if cur.fetchone() != (False, True):
                        raise TradeBlocked("PAPER control lock no longer verified")
                yield assert_lock_alive

    def consume_submission_authorization(self, client_order_id):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT public.paper_consume_submission_authorization(%s)",
                            (client_order_id,))
                if cur.fetchone() != (True,):
                    raise TradeBlocked("PAPER submission authorization consumption failed")
