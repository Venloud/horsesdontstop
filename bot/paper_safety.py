"""Fail-closed order PLANNING for eventual Alpaca PAPER execution.

There is deliberately NO submit_order() call in this module. This remains
read-only until a durable journal, reconciliation and worker are deployed.
"""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from hashlib import sha256
import re


PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
CENT = Decimal("0.01")
ALLOWED_STRATEGIES = frozenset({"momentum_volume"})


class TradeBlocked(ValueError):
    """Order cannot be planned safely."""


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TradeBlocked("Invalid monetary amount") from exc
    if not result.is_finite():
        raise TradeBlocked("Non-finite monetary amount")
    return result


@dataclass(frozen=True)
class PaperOrderPlan:
    symbol: str
    strategy: str
    trading_day: str
    client_order_id: str
    estimated_entry: float
    protective_stop: float
    whole_shares: int
    estimated_cost: float
    risk_budget_usd: float
    estimated_risk_usd: float
    order_class: str = "oto"
    instruction: str = "RISK RESEARCH ONLY; LIVE entry uses capped DAY limit OTO preview"

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PaperSafetyState:
    equity: float
    available_cash: float
    day_open_equity: float | None
    recorded_peak_equity: float | None
    open_positions: int
    open_orders: int
    market_open: bool
    trading_blocked: bool
    recent_quote_seconds: int | None
    durable_journal_healthy: bool = False
    reconciled_with_broker: bool = False
    watchdog_heartbeat_seconds: int | None = None
    kill_switch_active: bool = True
    order_submissions_explicitly_enabled: bool = False
    broker_endpoint: str = PAPER_ENDPOINT


def execution_blockers(state: PaperSafetyState) -> list[str]:
    """All conditions must pass before ANY future submission capability.

    Fail closed on missing data and infra. Default state is intentionally
    blocked. No program in the repo currently sends brokerage orders.
    """
    failures = []
    if state.broker_endpoint != PAPER_ENDPOINT:
        failures.append("not_proven_paper_endpoint")
    if state.kill_switch_active:
        failures.append("kill_switch_active")
    if not state.order_submissions_explicitly_enabled:
        failures.append("paper_order_submission_disabled")
    if not state.market_open:
        failures.append("market_closed")
    if state.trading_blocked:
        failures.append("account_trading_blocked")
    if state.open_positions != 0 or state.open_orders != 0:
        failures.append("existing_position_or_open_order")
    if state.recent_quote_seconds is None or not 0 <= state.recent_quote_seconds <= 120:
        failures.append("no_fresh_realtime_quote")
    if not state.durable_journal_healthy:
        failures.append("durable_journal_not_ready")
    if not state.reconciled_with_broker:
        failures.append("broker_orders_not_reconciled")
    if state.watchdog_heartbeat_seconds is None or not 0 <= state.watchdog_heartbeat_seconds <= 120:
        failures.append("no_healthy_persistent_watchdog")
    try:
        equity = money(state.equity)
        cash = money(state.available_cash)
        if equity <= 0 or cash <= 0 or cash > equity * Decimal("1.1"):
            failures.append("invalid_cash_or_equity")
        if state.day_open_equity is None:
            failures.append("missing_daily_open_equity")
        else:
            opening = money(state.day_open_equity)
            if opening <= 0 or equity <= opening * Decimal(".97"):
                failures.append("daily_three_percent_loss_cutoff")
        if state.recorded_peak_equity is None:
            failures.append("missing_durable_equity_peak")
        else:
            peak = money(state.recorded_peak_equity)
            if peak <= 0 or equity <= peak * Decimal(".85"):
                failures.append("fifteen_percent_drawdown_cutoff")
    except TradeBlocked:
        failures.append("unverifiable_account_balances")
    return failures


def make_client_order_id(day: date, symbol: str, strategy: str = "momentum_volume"):
    if not isinstance(day, date) or not re.fullmatch(r"[A-Z]{1,5}", symbol):
        raise TradeBlocked("Invalid day or ticker")
    if strategy not in ALLOWED_STRATEGIES:
        raise TradeBlocked("Unapproved paper strategy")
    # Stable one-per-symbol-per-trading-day id for eventual broker reconciliation.
    key = f"horsesdontstop|{day.isoformat()}|{symbol}|{strategy}|v1"
    return f"hds-{day:%Y%m%d}-{symbol}-" + sha256(key.encode()).hexdigest()[:12]


def plan_paper_entry(symbol: str, day: date, reference_price: float,
                     equity: float, available_cash: float,
                     risk_fraction: float = .01,
                     strategy: str = "momentum_volume",
                     protective_stop_fraction: float = .03,
                     adverse_entry_buffer_fraction: float = .005) -> PaperOrderPlan:
    """Preview conservative WHOLE-SHARE sizing at current equity.

    Profits are compounded immediately, below $1000 too. The next trade uses
    the latest account equity and available cash, never a fixed $500 baseline.
    This is NOT a fill quote or authority to send an order.
    """
    id_ = make_client_order_id(day, symbol, strategy)
    price = money(reference_price)
    account_equity, cash = money(equity), money(available_cash)
    fraction = money(risk_fraction)
    stop_fraction = money(protective_stop_fraction)
    buffer = money(adverse_entry_buffer_fraction)
    if (price <= 0 or account_equity <= 0 or cash <= 0
            or not Decimal("0") < fraction <= Decimal(".02")
            or not Decimal("0") < stop_fraction < Decimal(".10")
            or not Decimal("0") <= buffer <= Decimal(".02")):
        raise TradeBlocked("Invalid price, cash or risk parameters")
    if cash > account_equity * Decimal("1.1"):
        raise TradeBlocked("Unexpected cash exceeds equity")
    estimated_fill = price * (1 + buffer)
    stop = (price * (1 - stop_fraction)).quantize(CENT, rounding=ROUND_DOWN)
    if stop <= 0 or stop >= estimated_fill:
        raise TradeBlocked("Invalid protective stop")
    max_loss_per_share = estimated_fill - stop
    # Do not round away tiny changes in risk budget; use full Decimal equity.
    budget = account_equity * fraction
    by_risk = int((budget / max_loss_per_share).to_integral_value(rounding=ROUND_DOWN))
    by_cash = int((cash / estimated_fill).to_integral_value(rounding=ROUND_DOWN))
    shares = min(by_risk, by_cash)
    if shares < 1:
        raise TradeBlocked("No affordable whole-share trade within the risk budget")
    return PaperOrderPlan(
        symbol=symbol, strategy=strategy, trading_day=day.isoformat(),
        client_order_id=id_, estimated_entry=float(estimated_fill.quantize(CENT)),
        protective_stop=float(stop), whole_shares=shares,
        estimated_cost=float((estimated_fill * shares).quantize(CENT)),
        risk_budget_usd=float(budget.quantize(CENT)),
        estimated_risk_usd=float((max_loss_per_share * shares).quantize(CENT)),
    )


def make_protective_alpaca_order(plan: PaperOrderPlan):
    """RETIRED: legacy GTC MARKET entry is prohibited.

    The only approved order builder for the future supervised PAPER
    executor is live_quotes.build_live_protected_oto_request, which
    requires an up-to-date IEX quote and creates a capped DAY LIMIT OTO.
    """
    raise TradeBlocked(
        "Legacy MARKET OTO order builder retired; use guarded live "
        "DAY LIMIT OTO preview only"
    )
