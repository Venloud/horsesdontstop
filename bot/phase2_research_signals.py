"""Phase 2 NEW research-only candidate signals using completed information.

Implements the three owner-preregistered hypotheses WITHOUT scoring returns,
placing orders, accessing Alpaca/Neon, or advancing the incomplete Phase 2
dataset manifest. These pure decisions are NOT connected to the older
backtest/strategy CLI or any broker execution gateway.

Inputs must be independently validated, chronologically ordered and strictly
known before their target execution event. The ETF source must be a
versioned, verified TOTAL-RETURN series, not unadjusted OHLCV closes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

from .strategies import DailyBar


SECTOR_ETF_UNIVERSE = (
    "SPY", "XLB", "XLC", "XLE", "XLF", "XLI",
    "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
REVERSAL_RSI_THRESHOLDS = (5, 10)
REVERSAL_HOLD_SESSIONS = (1, 3)
DUAL_MOMENTUM_LOOKBACKS = (63, 126, 252)
OVERNIGHT_FILTERS = (False, True)


@dataclass(frozen=True)
class ReversalSignal:
    rsi2: float
    sma200: float
    previous_sma200: float
    hold_sessions: int
    score: float
    # This is a research signal, not a quote, a fill or an order request.
    broker_order_allowed: bool = False


@dataclass(frozen=True)
class TotalReturnPoint:
    day: date
    total_return_index: float

    def __post_init__(self):
        if (not isinstance(self.day, date)
                or type(self.total_return_index) not in (float, int)
                or not isfinite(self.total_return_index)
                or self.total_return_index <= 0):
            raise ValueError("Invalid point-in-time total-return index")


@dataclass(frozen=True)
class DualMomentumSignal:
    completed_month_end: date
    next_rebalance_session: date
    lookback_sessions: int
    selected_symbol: str | None  # None represents cash, not a buy order.
    qualifying_return: float
    broker_order_allowed: bool = False


@dataclass(frozen=True)
class OvernightResearchSignal:
    proposed_close_session: date
    exit_next_open: bool
    completed_spy_regime_return: float | None
    hypothesis_eligible: bool
    # Close-auction cutoff data and executable prices are NOT proven.
    broker_order_allowed: bool = False
    auction_execution_verified: bool = False


def _chronological_bars(history: Sequence[DailyBar], *, before: date) -> bool:
    if not isinstance(before, date):
        raise ValueError("Entry session must be a date")
    if not isinstance(history, (list, tuple)):
        raise ValueError("Completed bar history must be a sequence")
    if any(not isinstance(b, DailyBar) for b in history):
        raise ValueError("Unexpected daily market bar type")
    days = [b.day for b in history]
    if days != sorted(set(days)):
        raise ValueError("Duplicate or unsequenced completed sessions")
    if days and days[-1] >= before:
        # No same-day completed close, high or volume may influence entry.
        raise ValueError("Future or entry-session OHLCV bar")


def _wilder_rsi2(history: Sequence[DailyBar]) -> float | None:
    """Wilder RSI(2), initialized from the first two completed changes.

    Standard exponential recurrence with alpha=1/2; no future bars.
    RSI is 50 with no changes, 0 with no gains, 100 with no losses.
    """
    if len(history) < 3:
        return None
    deltas = [
        history[i].close - history[i - 1].close
        for i in range(1, len(history))
    ]
    gains = sum(max(d, 0.0) for d in deltas[:2]) / 2
    losses = sum(max(-d, 0.0) for d in deltas[:2]) / 2
    for delta in deltas[2:]:
        gains = (gains + max(delta, 0.0)) / 2
        losses = (losses + max(-delta, 0.0)) / 2
    if gains == 0 and losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def short_term_reversal_signal(
    completed_bars: Sequence[DailyBar], *,
    entry_session: date, rsi_threshold: int, hold_sessions: int,
) -> ReversalSignal | None:
    """Preregistered 200-SMA uptrend + down close + RSI(2) <= 5 or 10.

    Latest completed close must exceed the rising 200-day SMA. This
    determines eligibility only; the separate exit policy is the
    predeclared 1 or 3 SESSION maximum, earlier if a standard stop hits.
    """
    if type(rsi_threshold) is not int or rsi_threshold not in REVERSAL_RSI_THRESHOLDS:
        raise ValueError("Non-preregistered reversal RSI threshold")
    if type(hold_sessions) is not int or hold_sessions not in REVERSAL_HOLD_SESSIONS:
        raise ValueError("Non-preregistered reversal holding period")
    _chronological_bars(completed_bars, before=entry_session)
    if len(completed_bars) < 201:
        return None
    current_sma = sum(x.close for x in completed_bars[-200:]) / 200
    previous_sma = sum(x.close for x in completed_bars[-201:-1]) / 200
    latest = completed_bars[-1].close
    previous = completed_bars[-2].close
    rsi = _wilder_rsi2(completed_bars)
    if (rsi is None or latest >= previous or latest <= current_sma
            or current_sma <= previous_sma or rsi > rsi_threshold):
        return None
    return ReversalSignal(
        rsi2=rsi, sma200=current_sma,
        previous_sma200=previous_sma,
        hold_sessions=hold_sessions,
        score=float(rsi_threshold - rsi),
    )


def etf_dual_momentum_signal(
    total_return_series: Mapping[str, Sequence[TotalReturnPoint]], *,
    completed_session: date, next_session: date,
    lookback_sessions: int,
    total_return_source_verified: bool = False,
    point_in_time_universe_verified: bool = False,
    next_session_calendar_verified: bool = False,
) -> DualMomentumSignal | None:
    """Monthly-only ETF absolute/relative ranking of 63, 126 or 252 closes.

    An externally verified calendar must prove next_session is the NEXT
    eligible trading session. Only the PREVIOUS completed month-end
    close may inform the next session open. Missing / unaligned ETF
    histories block the entire selection (never pick surviving ETFs).
    """
    if type(lookback_sessions) is not int or lookback_sessions not in DUAL_MOMENTUM_LOOKBACKS:
        raise ValueError("Non-preregistered ETF lookback")
    if (type(total_return_source_verified) is not bool
            or type(point_in_time_universe_verified) is not bool
            or type(next_session_calendar_verified) is not bool):
        raise ValueError("Attestation flags must be explicit booleans")
    if not isinstance(completed_session, date) or not isinstance(next_session, date):
        raise ValueError("Invalid month-end calendar dates")
    if next_session <= completed_session:
        raise ValueError("ETF rebalance must follow completed month-end")
    if (next_session.year, next_session.month) == (
            completed_session.year, completed_session.month):
        return None  # No monthly rebalance.
    if not (total_return_source_verified and point_in_time_universe_verified
            and next_session_calendar_verified):
        return None  # No legitimate total-return, universe or calendar proof.
    if not isinstance(total_return_series, Mapping):
        raise ValueError("ETF source must be a keyed set of total-return histories")
    if set(total_return_series) != set(SECTOR_ETF_UNIVERSE):
        return None
    histories = {}
    for symbol in SECTOR_ETF_UNIVERSE:
        history = total_return_series[symbol]
        if not isinstance(history, (list, tuple)) or len(history) < lookback_sessions + 1:
            return None
        if any(not isinstance(p, TotalReturnPoint) for p in history):
            raise ValueError("Unverified ETF data type")
        days = [p.day for p in history]
        if days != sorted(set(days)) or days[-1] != completed_session:
            return None
        histories[symbol] = history[-(lookback_sessions + 1):]

    # All ETFs must have the exact same observed sessions, otherwise
    # cross-asset return ranks compare different periods (potential bias).
    comparable_dates = tuple(p.day for p in histories["SPY"])
    if any(tuple(p.day for p in h) != comparable_dates for h in histories.values()):
        return None
    returns = {
        symbol: history[-1].total_return_index /
                history[0].total_return_index - 1
        for symbol, history in histories.items()
    }
    # Lexical tie break has NO performance tuning parameter.
    leader = min(SECTOR_ETF_UNIVERSE, key=lambda symbol: (-returns[symbol], symbol))
    best = returns[leader]
    return DualMomentumSignal(
        completed_month_end=completed_session,
        next_rebalance_session=next_session,
        lookback_sessions=lookback_sessions,
        selected_symbol=leader if best > 0 else None,
        qualifying_return=best,
    )


def overnight_drift_research_signal(
    completed_spy_bars: Sequence[DailyBar], *,
    proposed_entry_close_session: date, require_positive_spy_20: bool,
) -> OvernightResearchSignal | None:
    """Study eligibility only; close-auction entry remains UNEXECUTABLE.

    This function never uses target-day closing data. Even a positive
    research eligibility decision cannot become an executable order
    without later verified before-auction signals, cutoff, auction
    execution and market calendar signoff.
    """
    if type(require_positive_spy_20) is not bool:
        raise ValueError("Overnight filter must be a preregistered boolean")
    _chronological_bars(completed_spy_bars, before=proposed_entry_close_session)
    if not completed_spy_bars:
        return None
    if require_positive_spy_20 and len(completed_spy_bars) < 21:
        return None
    completed_return = (
        completed_spy_bars[-1].close / completed_spy_bars[-21].close - 1
        if len(completed_spy_bars) >= 21 else None
    )
    eligible = not require_positive_spy_20 or (
        completed_return is not None and completed_return > 0
    )
    return OvernightResearchSignal(
        proposed_close_session=proposed_entry_close_session,
        exit_next_open=True, completed_spy_regime_return=completed_return,
        hypothesis_eligible=eligible,
    )
