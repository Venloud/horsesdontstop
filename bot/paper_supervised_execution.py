"""Unreleased supervised Alpaca PAPER-only order gateway.

This module contains a single broker submit_order boundary but it is
HARD-DISABLED FOR PRODUCTION until separately reviewed and released.
There is NO CLI, scheduler, or GitHub workflow invoking it.

Tests may patch PAPER_ORDER_SUBMISSIONS_RELEASED=True only with FAKE
broker/journal objects to exercise idempotency, timeouts and approval
logic. Never change the release constant to True as a convenience.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from time import monotonic
from uuid import uuid4

from .live_quotes import LivePaperEntryPreview, build_live_protected_oto_request
from .live_quotes import preview_live_paper_entry
from .paper_broker_transport import assert_single_attempt_transport
from .paper_safety import (
    PAPER_ENDPOINT, PaperOrderPlan, PaperSafetyState, TradeBlocked,
    execution_blockers, money,
)


# Locked code-level release switch, separate from the Neon kill switch.
# Change only with explicit owner approval and verified persistent watchdog.
PAPER_ORDER_SUBMISSIONS_RELEASED = False


@dataclass(frozen=True)
class ExplicitPaperTradeApproval:
    """Exact trade details *previously approved* through a trusted workflow.

    This type alone does NOT authenticate an owner. A secure owner
    authorization mechanism must be built before releasing real runs.
    """
    client_order_id: str
    symbol: str
    shares: int
    max_buy_limit_usd: float
    protective_stop_usd: float
    approved_at_utc: datetime
    expires_at_utc: datetime
    order_time_in_force: str = "gtc"
    order_class: str = "oto"
    maximum_purchase_cost_usd: float = 5.00
    fee_reserve_usd: float = 0.50
    account_id_sha256: str = ""


@dataclass(frozen=True)
class PaperSubmitResult:
    client_order_id: str
    broker_order_id: str
    result: str
    submitted_to_paper_broker: bool


@dataclass(frozen=True)
class PreparedPaperOrder:
    """Durably reserved exact request awaiting owner approval; never submitted."""
    plan: PaperOrderPlan
    preview: LivePaperEntryPreview
    request_sha256: str
    account_id_sha256: str
    fee_reserve_usd: float = 0.50
    submitted_to_paper_broker: bool = False

    def to_dict(self):
        return {
            "client_order_id": self.plan.client_order_id,
            "symbol": self.plan.symbol,
            "side": "buy",
            "quantity": self.plan.whole_shares,
            "order_class": self.plan.order_class,
            "time_in_force": self.plan.time_in_force,
            "limit_price": self.plan.estimated_entry,
            "purchase_cost_cap": self.plan.estimated_cost,
            "stop_price": self.plan.protective_stop,
            "fee_reserve": self.fee_reserve_usd,
            "request_sha256": self.request_sha256,
            "account_id_sha256": self.account_id_sha256,
            "quote_preview": self.preview.to_dict(),
            "submitted": False,
        }


def _validate_approval(approval, preview, now, expected_account_sha256):
    if not isinstance(approval, ExplicitPaperTradeApproval):
        raise TradeBlocked("Specific owner review/approval not supplied")
    if (approval.approved_at_utc.tzinfo is None
            or approval.expires_at_utc.tzinfo is None or now.tzinfo is None):
        raise TradeBlocked("Trade approval timestamps must have timezones")
    age = (now - approval.approved_at_utc).total_seconds()
    lifetime = (approval.expires_at_utc -
                approval.approved_at_utc).total_seconds()
    if not 0 <= age <= 120 or not 0 < lifetime <= 120:
        raise TradeBlocked("Trade approval expired or has invalid lifetime")
    if now > approval.expires_at_utc:
        raise TradeBlocked("Trade approval expired")
    if (approval.client_order_id != preview.client_order_id
            or approval.symbol != preview.symbol
            or type(approval.shares) is not int
            or approval.shares != preview.shares
            or money(approval.max_buy_limit_usd) !=
               money(preview.buy_limit_usd)
            or money(approval.protective_stop_usd) !=
               money(preview.stop_usd)):
        raise TradeBlocked("Approval does not match exact paper order")
    if (approval.order_time_in_force != "gtc"
            or approval.order_class != "oto"
            or money(approval.maximum_purchase_cost_usd) != money("5.00")
            or money(approval.fee_reserve_usd) != money("0.50")
            or money(preview.max_limit_purchase_usd) > money("5.00")):
        raise TradeBlocked("Approval does not match bounded GTC OTO pilot policy")
    if (not isinstance(expected_account_sha256, str)
            or len(expected_account_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_account_sha256)
            or approval.account_id_sha256 != expected_account_sha256):
        raise TradeBlocked("Approval does not match pinned PAPER account")


def _paper_account_sha256(account):
    from uuid import UUID
    raw = getattr(account, "id", None)
    try:
        normalized = str(UUID(str(raw))).lower()
    except (ValueError, TypeError, AttributeError):
        raise TradeBlocked("Authenticated PAPER account identity unavailable") from None
    return sha256(normalized.encode("ascii")).hexdigest()


def _request_sha256(order_request):
    """Hash the exact Alpaca SDK wire fields approved and dispatched."""
    fields = order_request.to_request_fields()
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"),
        default=lambda value: getattr(value, "value", str(value)),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _plan_from_quoted_preview(preview):
    from datetime import date
    if not isinstance(preview, LivePaperEntryPreview):
        raise TradeBlocked("Invalid live IEX entry preview")
    if (preview.order_submission_enabled is not False
            or preview.quote_feed != "IEX_ONLY_NOT_NBBO"
            or preview.order_class != "oto"
            or preview.order_time_in_force != "gtc"):
        raise TradeBlocked("Quote preview not eligible for protected PAPER order")
    try:
        day = datetime.fromisoformat(preview.observed_at_utc)
        if day.tzinfo is None:
            raise ValueError
        from zoneinfo import ZoneInfo
        session_day = day.astimezone(ZoneInfo("America/New_York")).date()
    except (ValueError, TypeError, AttributeError) as exc:
        raise TradeBlocked("Invalid quote observation timestamp") from exc
    plan = PaperOrderPlan(
        symbol=preview.symbol, strategy="momentum_volume",
        trading_day=session_day.isoformat(),
        client_order_id=preview.client_order_id,
        estimated_entry=preview.buy_limit_usd,
        protective_stop=preview.stop_usd,
        whole_shares=preview.shares,
        estimated_cost=preview.max_limit_purchase_usd,
        risk_budget_usd=preview.risk_budget_usd,
        estimated_risk_usd=preview.stop_distance_risk_usd,
        time_in_force=preview.order_time_in_force,
    )
    return plan


def _broker_did_not_find_order(trading, client_id):
    """Return True only on a definite 404; any other result aborts.

    Absence is checked only BEFORE the durable reservation, never
    interpreted as an instruction to resubmit an earlier reservation.
    """
    from alpaca.common.exceptions import APIError
    try:
        found = trading.get_order_by_client_id(client_id)
    except APIError as exc:
        if exc.status_code == 404:
            return True
        raise TradeBlocked("Pre-submission broker lookup unavailable") from None
    except Exception:
        raise TradeBlocked("Pre-submission broker lookup unavailable") from None
    # Any existing broker order with this client ID blocks another.
    raise TradeBlocked("Broker already has this client order ID")


def inspect_uncertain_submit_by_client_id(trading, plan: PaperOrderPlan):
    """Observe ambiguous POST using broker GETs; NEVER POST a second order.

    Called after a durable reservation is recorded as unknown (or that
    status update fails). Missing broker client ID is NEVER permission
    to retry. Returns a fixed sanitized classification without secrets.
    """
    try:
        from .paper_broker_recovery import recover_by_client_id
        positions = trading.get_all_positions()
        relevant = [
            x for x in positions if getattr(x, "symbol", None) == plan.symbol
        ]
        if len(relevant) > 1:
            return "broker_position_state_unverifiable"
        exposure = getattr(relevant[0], "qty", 0) if relevant else 0
        audit = recover_by_client_id(trading, plan, position_qty=exposure)
    except Exception:
        return "broker_client_id_lookup_unavailable"
    if audit.status == "not_found_unresolved":
        return "broker_client_id_not_found_still_uncertain"
    if audit.blockers:
        return "broker_order_found_requires_stop_or_state_review"
    return "broker_order_found_requires_supervisor_reconciliation"



def _verify_paper_market_session(trading, clock, *, now):
    """Require today's authentic Alpaca PAPER calendar AND clock to agree.

    Read-only GET; this does NOT establish stop duration or grant trade
    approval. Never trust a caller-constructed calendar-ready boolean.
    """
    from zoneinfo import ZoneInfo
    from alpaca.trading.requests import GetCalendarRequest
    from alpaca.trading.models import Calendar

    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Missing timezone-aware PAPER execution clock")
    local_today = now.astimezone(ZoneInfo("America/New_York")).date()
    try:
        records = trading.get_calendar(
            GetCalendarRequest(start=local_today, end=local_today))
    except Exception:
        raise TradeBlocked("Alpaca PAPER trading calendar unavailable") from None
    if not isinstance(records, (list, tuple)) or len(records) != 1:
        raise TradeBlocked("Today's NYSE trading session not verified")
    session = records[0]
    if getattr(session, "date", None) != local_today:
        raise TradeBlocked("Alpaca calendar trading date mismatch")
    opens = getattr(session, "open", None)
    closes = getattr(session, "close", None)
    broker_close = getattr(clock, "next_close", None)
    if any(not isinstance(d, datetime) for d in (opens, closes, broker_close)):
        raise TradeBlocked("Alpaca calendar/clock missing session datetimes")
    # alpaca-py Calendar creates NAIVE New York wall-time datetimes from
    # API date + HH:MM strings. Interpret them as NY only for genuine
    # Calendar models, never as the machine's local timezone or as UTC.
    ny_zone = ZoneInfo("America/New_York")
    if isinstance(session, Calendar):
        if opens.tzinfo is None:
            opens = opens.replace(tzinfo=ny_zone)
        if closes.tzinfo is None:
            closes = closes.replace(tzinfo=ny_zone)
    if any(d.tzinfo is None or d.utcoffset() is None
           for d in (opens, closes, broker_close)):
        raise TradeBlocked("Alpaca calendar/clock lacks timezone-aware session times")
    open_utc = opens.astimezone(timezone.utc)
    close_utc = closes.astimezone(timezone.utc)
    clock_close_utc = broker_close.astimezone(timezone.utc)
    if (opens.astimezone(ZoneInfo("America/New_York")).date() != local_today
            or closes.astimezone(ZoneInfo("America/New_York")).date() != local_today
            or not timedelta(hours=2) <= close_utc - open_utc <= timedelta(hours=7)
            or abs((clock_close_utc - close_utc).total_seconds()) > 60):
        raise TradeBlocked("Alpaca PAPER calendar and market clock disagree")
    # GTC parent may remain active after this process/session ends. Keep
    # the conservative entry cutoff and require restart reconciliation
    # before later dispatch; do not treat it as intraday-only.
    regular_cutoff = datetime.combine(
        local_today, datetime.min.time(), ZoneInfo("America/New_York")
    ).replace(hour=15, minute=30).astimezone(timezone.utc)
    latest_entry = min(regular_cutoff, close_utc - timedelta(minutes=30))
    now_utc = now.astimezone(timezone.utc)
    if not open_utc <= now_utc < latest_entry:
        raise TradeBlocked("PAPER entry too close to verified NYSE close")
    return True


def _verify_momentum_signal(trading, market_data, symbol, now):
    """Refetch completed bars; rankings or caller booleans are not signals."""
    if market_data is None:
        raise TradeBlocked("Independent completed-bar strategy evidence missing")
    from zoneinfo import ZoneInfo
    from alpaca.trading.requests import GetCalendarRequest
    from .paper_readiness_cli import research_candidates
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    try:
        sessions = trading.get_calendar(GetCalendarRequest(
            start=today - timedelta(days=14), end=today - timedelta(days=1)))
        days = [s.date for s in sessions]
        if (not days or len(set(days)) != len(days)
                or any(not today - timedelta(days=14) <= d < today for d in days)):
            raise ValueError
        candidates = research_candidates(market_data, [symbol], now=now,
                                         required_session=max(days))
        if len(candidates) != 1 or candidates[0]["symbol"] != symbol:
            raise ValueError
    except Exception:
        raise TradeBlocked("No verified fresh completed-session momentum signal") from None


def submit_paper_once_supervised(
    *, trading, journal, market_quote, preview, safety_state,
    approval, now: datetime, expected_account_sha256=None, market_data=None,
    prepare_only=False, intent_already_reserved=False,
) -> PaperSubmitResult | PreparedPaperOrder:
    """ONE broker submission at most, with exact matching reviewed terms.

    No executable entrypoint calls this function. Production release
    switch remains False. The journal must atomically reserve first.
    An API timeout always records UNKNOWN and aborts; never retry.
    """
    if type(prepare_only) is not bool or type(intent_already_reserved) is not bool:
        raise TradeBlocked("Invalid PAPER pilot mode")
    if prepare_only and intent_already_reserved:
        raise TradeBlocked("Preparation cannot reuse a prior intent")
    if PAPER_ORDER_SUBMISSIONS_RELEASED is not True and not prepare_only:
        raise TradeBlocked("PAPER broker submissions NOT RELEASED")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Missing trustworthy timezone-aware clock")
    started = monotonic()
    if not isinstance(safety_state, PaperSafetyState):
        raise TradeBlocked("Required independent persistent supervisor missing")
    blockers = execution_blockers(safety_state)
    if prepare_only:
        if (safety_state.kill_switch_active is not True
                or safety_state.order_submissions_explicitly_enabled is not False):
            raise TradeBlocked("Preparation requires kill switch ON and submissions OFF")
        blockers = [x for x in blockers if x not in
                    {"kill_switch_active", "paper_order_submission_disabled"}]
    if blockers:
        raise TradeBlocked("Execution readiness blocked: " + ",".join(blockers))
    if safety_state.broker_endpoint != PAPER_ENDPOINT:
        raise TradeBlocked("Live-money broker endpoint rejected")
    # Confirm the *actual* SDK client URL, not only caller-provided
    # PaperSafetyState. If SDK changes or its endpoint is hidden,
    # default to refusal instead of guessing.
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        raise TradeBlocked("Actual broker client endpoint is not Alpaca PAPER")
    assert_single_attempt_transport(trading)
    if not isinstance(preview, LivePaperEntryPreview):
        raise TradeBlocked("Invalid live IEX preview")

    # Refresh actual broker account/clock/exposure immediately before
    # any database reservation. No non-paper endpoint can be passed in
    # through production code (trading factory still required).
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    try:
        account = trading.get_account()
        clock = trading.get_clock()
        positions = trading.get_all_positions()
        orders = trading.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, nested=True, limit=500))
    except Exception:
        raise TradeBlocked("Live broker pre-submission verification unavailable") from None
    # SDK fields must be genuine broker booleans, not truthy text or
    # missing values. In particular, None does not prove trading is allowed.
    if (getattr(clock, "is_open", None) is not True
            or getattr(account, "trading_blocked", None) is not False
            or not isinstance(positions, (list, tuple))
            or not isinstance(orders, (list, tuple))
            or positions or orders):
        raise TradeBlocked("Paper market closed, account blocked or exposure exists")
    if (not isinstance(expected_account_sha256, str)
            or _paper_account_sha256(account) != expected_account_sha256):
        raise TradeBlocked("Authenticated PAPER account does not match pinned account")
    _verify_paper_market_session(trading, clock, now=now)
    _verify_momentum_signal(trading, market_data, preview.symbol, now)
    if (abs(money(account.equity) - money(safety_state.equity)) > money(".01")
            or abs(money(account.cash) - money(safety_state.available_cash))
               > money(".01")):
        raise TradeBlocked("Account balance changed since risk check")

    # Compute full order anew from a fresh IEX quote, latest account
    # balances, and today's Alpaca clock; reject stale/swapped previews.
    recomputed = preview_live_paper_entry(
        symbol=preview.symbol, quote=market_quote, equity=account.equity,
        available_cash=account.cash, now=now, market_open=bool(clock.is_open))
    for key in (
        "client_order_id", "quote_at_utc", "bid_usd", "ask_usd",
        "buy_limit_usd", "stop_usd", "shares", "risk_budget_usd",
        "stop_distance_risk_usd", "max_limit_purchase_usd",
    ):
        if getattr(preview, key) != getattr(recomputed, key):
            raise TradeBlocked("Live IEX quote/approved plan changed")
    # Both the reviewed preview itself and the live quote are fresh.
    observed = datetime.fromisoformat(preview.observed_at_utc)
    if observed.tzinfo is None or not -3 <= (now - observed).total_seconds() <= 45:
        raise TradeBlocked("Reviewed live order preview is stale")
    if not prepare_only:
        _validate_approval(approval, recomputed, now, expected_account_sha256)
    plan = _plan_from_quoted_preview(recomputed)
    from .paper_execution_journal import PaperExecutorJournal
    PaperExecutorJournal.validate_protected_intent(plan)
    order_request = build_live_protected_oto_request(recomputed)
    request_digest = _request_sha256(order_request)
    attempt_id = str(uuid4())
    from .live_quotes import PILOT_FEE_RESERVE_USD
    if (money(getattr(account, "buying_power", None))
            < money(plan.estimated_cost) + PILOT_FEE_RESERVE_USD):
        raise TradeBlocked("Insufficient verified PAPER buying power")

    # First read for prior broker orders, then reserve in Neon. Broker
    # lookup/race failures cannot cause a duplicate post: stable client
    # id + journal uniqueness + explicit no-repeat rule.
    _broker_did_not_find_order(trading, plan.client_order_id)
    if prepare_only:
        if journal.reserve_if_enabled(plan) is not True:
            raise TradeBlocked("Durable order already reserved: NEVER reuse")
        return PreparedPaperOrder(
            plan=plan, preview=recomputed, request_sha256=request_digest,
            account_id_sha256=expected_account_sha256,
        )
    if plan.time_in_force == "gtc":
        if intent_already_reserved is not True:
            raise TradeBlocked("GTC pilot dispatch requires separately prepared owner-approved intent")
    elif intent_already_reserved:
        raise TradeBlocked("Pre-reserved dispatch is only supported by bounded GTC pilot")
    elif journal.reserve_if_enabled(plan) is not True:
        raise TradeBlocked("Durable order already reserved: NEVER retry")

    attempted = False
    try:
        with journal.authorize_reserved_submission(
                plan, request_sha256=request_digest, attempt_id=attempt_id,
                account_id_sha256=expected_account_sha256,
                ) as assert_lock_alive:
            # Reservation is consumed durably before entering this context.
            # Recheck time-sensitive evidence after any DB lock wait.
            final_now = now + timedelta(seconds=monotonic() - started)
            _validate_approval(approval, recomputed, final_now,
                               expected_account_sha256)
            preview_live_paper_entry(
                symbol=preview.symbol, quote=market_quote, equity=account.equity,
                available_cash=account.cash, now=final_now, market_open=True)
            _verify_paper_market_session(trading, clock, now=final_now)
            # Refresh account/exposure again after reservation and lock waits.
            final_account = trading.get_account()
            final_clock = trading.get_clock()
            final_positions = trading.get_all_positions()
            final_orders = trading.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, nested=True, limit=500))
            if (getattr(final_clock, "is_open", None) is not True
                    or getattr(final_account, "trading_blocked", None) is not False
                    or not isinstance(final_positions, (list, tuple)) or final_positions
                    or not isinstance(final_orders, (list, tuple)) or final_orders
                    or money(final_account.equity) != money(account.equity)
                    or money(final_account.cash) != money(account.cash)
                    or _paper_account_sha256(final_account) != expected_account_sha256
                    or money(getattr(final_account, "buying_power", None))
                    < money(plan.estimated_cost) + PILOT_FEE_RESERVE_USD):
                raise TradeBlocked("PAPER account changed after reservation")
            _verify_paper_market_session(trading, final_clock, now=final_now)
            if not callable(assert_lock_alive):
                raise TradeBlocked("PAPER control lock liveness check unavailable")
            assert_lock_alive()
            # Network reads can themselves stall. Approval and quote freshness
            # must still hold immediately before the POST boundary.
            send_now = now + timedelta(seconds=monotonic() - started)
            _validate_approval(approval, recomputed, send_now,
                               expected_account_sha256)
            preview_live_paper_entry(
                symbol=preview.symbol, quote=market_quote, equity=final_account.equity,
                available_cash=final_account.cash, now=send_now, market_open=True)
            if send_now - final_now > timedelta(seconds=3):
                raise TradeBlocked("PAPER final broker verification exceeded freshness budget")
            if send_now >= final_clock.next_close - timedelta(minutes=30):
                raise TradeBlocked("PAPER entry deadline passed during verification")
            # A remote DB and broker cannot share an atomic transaction. A
            # backend can still die after this acknowledgement; submission
            # release stays disabled until that residual risk is reviewed.
            attempted = True
            result = trading.submit_order(order_data=order_request)
    except Exception:
        if not attempted:
            raise TradeBlocked(
                "Final PAPER reauthorization failed before POST; NEVER retry reserved ID"
            ) from None
        outcome = inspect_uncertain_submit_by_client_id(trading, plan)
        raise TradeBlocked(
            "Broker submit outcome UNKNOWN after client-ID lookup: " + outcome
            + "; NEVER retry this reserved ID"
        ) from None

    broker_id = getattr(result, "id", None)
    if (not broker_id
            or getattr(result, "client_order_id", None) != plan.client_order_id
            or str(getattr(getattr(result, "side", None), "value",
                           getattr(result, "side", ""))).lower() != "buy"):
        outcome = inspect_uncertain_submit_by_client_id(trading, plan)
        raise TradeBlocked(
            "Broker acknowledgement inconsistent; outcome UNKNOWN after "
            "client-ID lookup: " + outcome
        )
    # Even a genuine ACK cannot be promoted to "submitted" by the
    # executor. Only the independent PAPER GET/stop reconciler has that
    # DB function grant. Mark the durable reservation unresolved so
    # crashes or delayed broker visibility can NEVER trigger a second POST.
    # Already committed UNKNOWN before the attempt. No ACK can promote it.
    # Success means the broker ACK was observed, not that the durable
    # journal is independently verified or a GTC stop exists.
    return PaperSubmitResult(
        client_order_id=plan.client_order_id,
        broker_order_id=str(broker_id),
        result="broker_acknowledged_unreconciled_stop_not_proven",
        submitted_to_paper_broker=True,
    )
