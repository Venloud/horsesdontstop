"""Pure synthetic next-session entry adapters for six frozen core strategies.

Never calls Alpaca, Neon, production workflows, or a scored Phase 3 runner.
The previously coded strategy functions are reused for their DEFAULT
variants; reversal/ETF are reused from phase2_research_signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

from .phase2_research_signals import (
    SECTOR_ETF_UNIVERSE, TotalReturnPoint,
    etf_dual_momentum_signal, short_term_reversal_signal,
)
from .strategies import DailyBar, signal_score


# Frozen grid in docs/PREREGISTRATION.md section 3:
# 4 + 3 + 3 + 2 + 4 + 3 = 19 executable research variants.
# The remaining TWO are overnight drift, explicitly research-only.
SIX_CORE_VARIANTS = {
    "momentum_volume": ((21, 1.15), (21, 1.50), (42, 1.15), (42, 1.50)),
    "trend_following": ((20, 50), (20, 100), (50, 200)),
    "high_52_week": ((.01,), (.02,), (.05,)),
    "sector_leadership": ((63,), (126,)),
    "short_term_reversal": ((5, 1), (5, 3), (10, 1), (10, 3)),
    "etf_dual_momentum": ((63,), (126,), (252,)),
}
OVERNIGHT_DRIFT_VARIANTS = (False, True)


@dataclass(frozen=True)
class NextOpenEligibility:
    strategy: str
    symbol: str
    entry_session: date
    observed_through: date
    variant: tuple
    score: float
    max_hold_sessions: int | None = None
    broker_order_allowed: bool = False


def _completed(history: Sequence[DailyBar], entry_session: date) -> bool:
    if type(entry_session) is not date or not isinstance(history, (list, tuple)):
        raise ValueError("Invalid next-open research input")
    if any(not isinstance(b, DailyBar) or type(b.day) is not date
           for b in history):
        raise ValueError("Unexpected daily-bar type")
    days = [b.day for b in history]
    if days != sorted(set(days)):
        raise ValueError("Unsorted or repeated research sessions")
    if days and days[-1] >= entry_session:
        raise ValueError("Future or entry-session completed bar")
    return bool(days)


def next_open_eligibility(
    strategy: str, variant: tuple, *,
    entry_session: date, completed_history: Sequence[DailyBar] = (),
    symbol: str = "",
    sector_history: Sequence[DailyBar] = (),
    spy_history: Sequence[DailyBar] = (),
    total_return_series: Mapping[str, Sequence[TotalReturnPoint]] | None = None,
    verified_session_calendar: Sequence[date] | None = None,
    pit_membership_verified: bool = False,
    total_return_verified: bool = False,
    calendar_verified: bool = False,
) -> NextOpenEligibility | None:
    """Map exactly one preregistered hypothesis to a prior-known signal.

    Verification flags are *caller-provided evidence*, not independently
    proven by this function. A PIT membership claim cannot be inferred from
    present-day surviving symbols. No executable market-order request.
    """
    if strategy == "overnight_drift":
        raise ValueError("Overnight drift stays research-only and unexecutable")
    if strategy not in SIX_CORE_VARIANTS or type(variant) is not tuple:
        raise ValueError("Unknown core strategy or parameter variant")
    if variant not in SIX_CORE_VARIANTS[strategy]:
        raise ValueError("Non-preregistered strategy parameters")
    if type(entry_session) is not date:
        raise ValueError("Invalid entry session")
    if pit_membership_verified is not True:
        return None

    if strategy == "etf_dual_momentum":
        if not isinstance(total_return_series, Mapping):
            return None
        if (not isinstance(verified_session_calendar, (list, tuple))
                or entry_session not in verified_session_calendar):
            return None
        idx = verified_session_calendar.index(entry_session)
        if idx < 1:
            return None
        completed = verified_session_calendar[idx-1]
        # Truncate the available index to the completed session.
        truncated = {
            ticker: tuple(p for p in pts if p.day <= completed)
            for ticker, pts in total_return_series.items()
        }
        result = etf_dual_momentum_signal(
            truncated, completed_session=completed, next_session=entry_session,
            lookback_sessions=variant[0],
            total_return_source_verified=total_return_verified,
            point_in_time_universe_verified=pit_membership_verified,
            next_session_calendar_verified=calendar_verified,
            verified_session_calendar=verified_session_calendar,
        )
        if result is None or result.selected_symbol is None:
            return None
        return NextOpenEligibility(
            strategy, result.selected_symbol, entry_session, completed,
            variant, result.qualifying_return)

    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Stock candidate needs a stable symbol")
    if not _completed(completed_history, entry_session):
        return None
    latest = completed_history[-1].day
    hold = None
    if strategy == "momentum_volume":
        length, rv_threshold = variant
        if len(completed_history) < max(length+1,21):
            return None
        if variant == (21, 1.15):
            score = signal_score(strategy, completed_history)
        else:
            baseline = sum(b.volume for b in completed_history[-21:-1]) / 20
            if baseline <= 0:
                return None
            rv = completed_history[-1].volume / baseline
            momentum = completed_history[-1].close / completed_history[-length-1].close - 1
            score = momentum * min(rv, 3) if momentum > 0 and rv >= rv_threshold else None
    elif strategy == "trend_following":
        short, long = variant
        if len(completed_history) < long+1:
            return None
        if variant == (20, 50):
            score = signal_score(strategy, completed_history)
        else:
            short_avg = sum(b.close for b in completed_history[-short:])/short
            past_short = sum(b.close for b in completed_history[-short-1:-1])/short
            long_avg = sum(b.close for b in completed_history[-long:])/long
            score = (completed_history[-1].close/long_avg-1
                     if (completed_history[-1].close > short_avg > long_avg
                         and short_avg > past_short) else None)
    elif strategy == "high_52_week":
        if len(completed_history) < 253:
            return None
        if variant == (.02,):
            score = signal_score(strategy, completed_history)
        else:
            prior_high = max(b.high for b in completed_history[-253:-1])
            prior_return = completed_history[-1].close / completed_history[-64].close - 1
            score = (prior_return + completed_history[-1].close/prior_high - 1
                     if prior_return > 0
                     and completed_history[-1].close >= (1-variant[0])*prior_high
                     else None)
    elif strategy == "sector_leadership":
        length = variant[0]
        if (not _completed(sector_history, entry_session)
                or not _completed(spy_history, entry_session)
                or len(completed_history) < length+1
                or len(sector_history) < length+1
                or len(spy_history) < length+1):
            return None
        periods = tuple(b.day for b in completed_history[-(length+1):])
        if any(tuple(b.day for b in h[-(length+1):]) != periods
               for h in (sector_history, spy_history)):
            return None
        if variant == (63,):
            score = signal_score(strategy, completed_history,
                                 sector_history, spy_history)
        else:
            stock = completed_history[-1].close / completed_history[-length-1].close - 1
            sector = sector_history[-1].close / sector_history[-length-1].close - 1
            spy = spy_history[-1].close / spy_history[-length-1].close - 1
            score = (stock-spy if stock > sector > spy and sector > 0 else None)
    else:  # short_term_reversal
        decision = short_term_reversal_signal(
            completed_history, entry_session=entry_session,
            rsi_threshold=variant[0], hold_sessions=variant[1])
        score = decision.score if decision is not None else None
        hold = decision.hold_sessions if decision is not None else None
    if score is None or not isfinite(score) or score <= 0:
        return None
    return NextOpenEligibility(
        strategy, symbol, entry_session, latest, variant, float(score),
        max_hold_sessions=hold,
    )


def adapter_variant_count() -> int:
    return sum(len(v) for v in SIX_CORE_VARIANTS.values()) + len(OVERNIGHT_DRIFT_VARIANTS)
