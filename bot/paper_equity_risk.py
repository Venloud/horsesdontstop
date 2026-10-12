"""Offline-only contribution-adjusted PAPER equity risk references.

A cash deposit must never disguise trading losses or inflate observed
strategy profit. A withdrawal should not be mistaken for a trade loss.

THIS IS NOT AN EQUITY COLLECTOR, DATABASE READER, OR BROKER EXECUTOR.
The caller must independently prove COMPLETE chronologically sampled
PAPER equity, all audited external cash flows, and valid NYSE calendar.
If that evidence is not available, refuse a risk-approval decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .paper_safety import TradeBlocked, money

NY = ZoneInfo("America/New_York")
CURRENCY_QUANTUM = Decimal("0.000001")
MAX_SNAPSHOT_AGE = timedelta(seconds=120)


@dataclass(frozen=True)
class ObservedPaperEquity:
    observed_at: datetime
    equity_usd: Decimal
    cash_usd: Decimal


@dataclass(frozen=True)
class AuditedExternalCashFlow:
    event_id: str
    occurred_at: datetime
    amount_usd: Decimal
    # Positive: owner's CASH DEPOSIT. Negative: owner's WITHDRAWAL.
    # This value MUST NOT include realized trading gains/losses.
    verified_by_broker: bool


@dataclass(frozen=True)
class AdjustedPaperRisk:
    observed_at_utc: str
    opening_equity_usd: Decimal
    opening_performance_equity_usd: Decimal
    current_performance_equity_usd: Decimal
    verified_peak_performance_equity_usd: Decimal
    loss_from_open_pct: Decimal
    drawdown_from_peak_pct: Decimal
    external_flows_since_open_usd: Decimal
    external_flows_since_first_snapshot_usd: Decimal
    daily_loss_limit_breached: bool
    peak_drawdown_limit_breached: bool

    @property
    def safe_under_numeric_limits(self):
        # Numeric-only answer; does not authorize order submission,
        # prove stop coverage, or prove the market supervisor is live.
        return not (self.daily_loss_limit_breached
                    or self.peak_drawdown_limit_breached)


def _timestamp(value, label):
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise TradeBlocked("Missing timezone-aware " + label)
    return value.astimezone(timezone.utc)


def _equity(item):
    if not isinstance(item, ObservedPaperEquity):
        raise TradeBlocked("Unverified equity sample record")
    at = _timestamp(item.observed_at, "equity observation")
    eq = money(item.equity_usd)
    cash = money(item.cash_usd)
    if eq <= 0 or cash < 0 or cash > eq * Decimal("1.1"):
        raise TradeBlocked("Invalid PAPER broker equity/cash observation")
    return at, eq, cash


def contribution_adjusted_risk(
    observations: list[ObservedPaperEquity],
    external_flows: list[AuditedExternalCashFlow],
    *,
    as_of_utc: datetime,
    all_equity_samples_accounted_for: bool = False,
    all_external_flows_reconciled: bool = False,
    exchange_calendar_verified: bool = False,
) -> AdjustedPaperRisk:
    """Conservative risk estimates from externally verified inputs.

    Requires sorted unique samples, an independently attested
    09:30-09:35 NY start-of-session sample for today, all broker
    contributions, no stale latest sample, and a complete equity
    sampling history for any claimed durable high-water mark.

    A gap or missing historical peak must be treated as BLOCKED
    by the supervisor, never silently smoothed over.
    """
    if not (all_equity_samples_accounted_for is True
            and all_external_flows_reconciled is True
            and exchange_calendar_verified is True):
        raise TradeBlocked("Cannot verify full PAPER equity/flow/calendar history")
    now = _timestamp(as_of_utc, "risk inspection")
    if not isinstance(observations, (list, tuple)) or not observations:
        raise TradeBlocked("Missing durable PAPER equity samples")
    if not isinstance(external_flows, (list, tuple)):
        raise TradeBlocked("Missing audited external PAPER cash-flow ledger")

    verified = [_equity(item) for item in observations]
    times = [t for t, _, _ in verified]
    if times != sorted(set(times)) or times[0] > now:
        raise TradeBlocked("Duplicate, future or unordered equity observation")
    if not timedelta(0) <= now - times[-1] <= MAX_SNAPSHOT_AGE:
        raise TradeBlocked("Latest PAPER equity sample is not fresh")

    # Use today's NYSE opening even if the 09:30 NY close crosses a DST
    # boundary; a separate verified exchange calendar is mandatory.
    local = now.astimezone(NY)
    market_open = datetime.combine(
        local.date(), time(hour=9, minute=30), tzinfo=NY
    ).astimezone(timezone.utc)
    if local.weekday() > 4 or now < market_open:
        raise TradeBlocked("No verified regular NYSE opening for this risk check")
    opening = [
        entry for entry in verified
        if market_open <= entry[0] < market_open + timedelta(minutes=5)
    ]
    if len(opening) != 1:
        raise TradeBlocked("Missing or ambiguous 09:30–09:35 PAPER opening equity")
    baseline_time, baseline_equity, _ = opening[0]

    if times[0] > baseline_time:
        raise TradeBlocked("Historical equity high-water reference missing")

    raw_flows = []
    seen_flow_ids = set()
    for item in external_flows:
        if not isinstance(item, AuditedExternalCashFlow):
            raise TradeBlocked("Malformed external PAPER cash-flow event")
        if (not isinstance(item.event_id, str)
                or not item.event_id.strip()
                or item.event_id in seen_flow_ids
                or item.verified_by_broker is not True):
            raise TradeBlocked("Unaudited or duplicated external cash flow")
        seen_flow_ids.add(item.event_id)
        flow_time = _timestamp(item.occurred_at, "external cash flow")
        amount = money(item.amount_usd)
        if amount == 0 or flow_time <= times[0] or flow_time > now:
            raise TradeBlocked("External cash flow timestamp/amount invalid")
        if flow_time in times:
            raise TradeBlocked("Ambiguous coincident equity and cash-flow time")
        raw_flows.append((flow_time, amount))
    if raw_flows != sorted(raw_flows):
        raise TradeBlocked("Out-of-order audited external cash flow events")

    cumulative = Decimal("0")
    index = 0
    adjusted_snapshots = []
    for snap_time, eq, _ in verified:
        while index < len(raw_flows) and raw_flows[index][0] < snap_time:
            cumulative += raw_flows[index][1]
            index += 1
        normalized = eq - cumulative
        if normalized <= 0:
            raise TradeBlocked("Unverifiable nonpositive contribution-adjusted equity")
        adjusted_snapshots.append((snap_time, normalized))

    while index < len(raw_flows) and raw_flows[index][0] <= now:
        # Flows after the most recent equity sample need a fresh matching
        # broker equity reading; otherwise we would compare stale values.
        raise TradeBlocked("Cash flow after latest broker equity observation")

    current = adjusted_snapshots[-1][1]
    prior_peak = max(x for _, x in adjusted_snapshots)
    first_flow_date = times[0]
    total_flows = sum((x for t, x in raw_flows if t > first_flow_date),
                      Decimal("0"))
    today_flows = sum((x for t, x in raw_flows if t > baseline_time),
                      Decimal("0"))
    # Opening equity is already an account balance after any cash flows
    # before the actual opening observation, so only subtract later
    # flows when assessing daily trading P&L.
    daily_effective_now = verified[-1][1] - today_flows
    daily_loss = (baseline_equity - daily_effective_now) / baseline_equity * 100
    peak_drawdown = (prior_peak - current) / prior_peak * 100

    return AdjustedPaperRisk(
        observed_at_utc=now.isoformat(),
        opening_equity_usd=baseline_equity,
        opening_performance_equity_usd=next(
            eq for t, eq in adjusted_snapshots if t == baseline_time),
        current_performance_equity_usd=current,
        verified_peak_performance_equity_usd=prior_peak,
        loss_from_open_pct=daily_loss,
        drawdown_from_peak_pct=peak_drawdown,
        external_flows_since_open_usd=today_flows,
        external_flows_since_first_snapshot_usd=total_flows,
        daily_loss_limit_breached=daily_loss >= Decimal("3"),
        peak_drawdown_limit_breached=peak_drawdown >= Decimal("15"),
    )
