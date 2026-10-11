"""Atomic paper-intent reservation behind Neon database emergency switch.

NO Alpaca submit_order() calls. This layer only prepares for a
future supervised worker with independently verified live order safety.
"""
from datetime import date

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
                cur.execute(
                    "SELECT kill_switch_active, submissions_enabled "
                    "FROM public.paper_control WHERE singleton=TRUE FOR SHARE"
                )
                if cur.fetchone() != (False, True):
                    raise TradeBlocked("Paper kill switch active or not approved")
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
