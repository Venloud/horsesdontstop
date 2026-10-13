"""Simulator adapters for the preregistered candidates. Offline, research only.

Each implemented variant becomes run_portfolio keyword arguments: a score_fn
built from an EXISTING signal function plus its exit policy. No new signal
logic lives here. overnight_drift stays research-only (no adapter).

Coverage of the frozen 21-variant grid (PREREGISTRATION section 3):
- implemented (11): the existing default of each of the 4 coded strategies,
  4 short_term_reversal and 3 etf_dual_momentum variants;
- not_implemented (8): non-default grid points of the 4 coded strategies.
  bot/strategies.py hard-codes only the defaults; adding parameters is a
  separate reviewed change, not done here;
- research_only (2): overnight_drift.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

from .phase2_research_signals import (
    DUAL_MOMENTUM_LOOKBACKS, REVERSAL_HOLD_SESSIONS, REVERSAL_RSI_THRESHOLDS,
    SECTOR_ETF_UNIVERSE, TotalReturnPoint, etf_dual_momentum_signal,
    short_term_reversal_signal,
)
from .strategies import signal_score


class VariantNotRunnable(ValueError):
    """Variant is in the grid but has no runnable adapter."""


@dataclass(frozen=True)
class Variant:
    variant_id: str
    candidate: str
    params: tuple
    status: str  # implemented | not_implemented | research_only


def _grid() -> tuple:
    v = []
    for lb in (21, 42):
        for rv in (1.15, 1.50):
            v.append(Variant(f"momentum_volume:lb{lb}_rv{rv:.2f}", "momentum_volume",
                             (("lookback", lb), ("rel_volume", rv)),
                             "implemented" if (lb, rv) == (21, 1.15) else "not_implemented"))
    for short, long in ((20, 50), (20, 100), (50, 200)):
        v.append(Variant(f"trend_following:sma{short}_{long}", "trend_following",
                         (("short", short), ("long", long)),
                         "implemented" if (short, long) == (20, 50) else "not_implemented"))
    for pct in (1, 2, 5):
        v.append(Variant(f"high_52_week:within{pct}pct", "high_52_week",
                         (("within_pct", pct),),
                         "implemented" if pct == 2 else "not_implemented"))
    for rs in (63, 126):
        v.append(Variant(f"sector_leadership:rs{rs}", "sector_leadership",
                         (("rs_sessions", rs),),
                         "implemented" if rs == 63 else "not_implemented"))
    for rsi in REVERSAL_RSI_THRESHOLDS:
        for hold in REVERSAL_HOLD_SESSIONS:
            v.append(Variant(f"short_term_reversal:rsi{rsi}_hold{hold}",
                             "short_term_reversal",
                             (("rsi_threshold", rsi), ("hold_sessions", hold)),
                             "implemented"))
    for lb in DUAL_MOMENTUM_LOOKBACKS:
        v.append(Variant(f"etf_dual_momentum:lb{lb}", "etf_dual_momentum",
                         (("lookback", lb),), "implemented"))
    for flt in (False, True):
        v.append(Variant(f"overnight_drift:spy20_{'on' if flt else 'off'}",
                         "overnight_drift", (("positive_spy_20", flt),),
                         "research_only"))
    return tuple(v)


GRID = _grid()
VARIANTS = {v.variant_id: v for v in GRID}


def _legacy(candidate):
    def score(symbol, history, sector_rows, market_rows):
        return signal_score(candidate, history, sector_rows, market_rows)
    return score


def _reversal(rsi, hold):
    def score(symbol, history, sector_rows, market_rows):
        if not history:
            return None
        # The guard only needs a session strictly after the last completed
        # bar; run_portfolio enters at the next session's open.
        sig = short_term_reversal_signal(
            list(history), entry_session=history[-1].day + timedelta(days=1),
            rsi_threshold=rsi, hold_sessions=hold)
        return None if sig is None else sig.score
    return score


def month_start_sessions(sessions: Sequence[date]) -> frozenset:
    """First session of each calendar month after the first one seen."""
    return frozenset(b for a, b in zip(sessions, sessions[1:])
                     if (a.year, a.month) != (b.year, b.month))


def _etf(lookback, total_return, sessions, verified):
    cache = {}

    def selected(last_completed):
        if last_completed not in cache:
            i = bisect_right(sessions, last_completed)
            if i >= len(sessions) or sessions[i - 1] != last_completed:
                cache[last_completed] = None
            else:
                hist = {s: [p for p in total_return.get(s, ()) if p.day <= last_completed]
                        for s in SECTOR_ETF_UNIVERSE}
                sig = etf_dual_momentum_signal(
                    hist, completed_session=last_completed,
                    next_session=sessions[i], lookback_sessions=lookback,
                    total_return_source_verified=verified["total_return"],
                    point_in_time_universe_verified=verified["universe"],
                    next_session_calendar_verified=verified["calendar"],
                    verified_session_calendar=list(sessions))
                cache[last_completed] = None if sig is None else sig.selected_symbol
        return cache[last_completed]

    def score(symbol, history, sector_rows, market_rows):
        if not history:
            return None
        return 1.0 if selected(history[-1].day) == symbol else None
    return score


def adapter(variant_id: str, *, sessions: Sequence[date] | None = None,
            total_return: Mapping[str, Sequence[TotalReturnPoint]] | None = None,
            verified: Mapping[str, bool] | None = None,
            etf_use_standard_stops: bool | None = None) -> dict:
    """run_portfolio keyword arguments for one implemented grid variant.

    Pass the returned dict as **kwargs together with data, symbols, risk and
    slippage. The strategy label is part of it (sector_leadership needs it
    for the sector ETF rows).

    etf_dual_momentum needs: the session calendar, a verified total-return
    series per ETF, the three verification booleans, and an explicit
    etf_use_standard_stops choice (the preregistration does not say whether
    the 3%/5% stops apply between monthly rebalances).
    """
    v = VARIANTS.get(variant_id)
    if v is None:
        raise ValueError("unknown variant")
    if v.status != "implemented":
        raise VariantNotRunnable(f"{variant_id}: {v.status}")
    p = dict(v.params)
    if v.candidate in ("momentum_volume", "trend_following", "high_52_week",
                       "sector_leadership"):
        return {"strategy": v.candidate, "score_fn": _legacy(v.candidate)}
    if v.candidate == "short_term_reversal":
        # Exit at the open hold_sessions after entry, earlier on the standard
        # stop (section 3). The exit-at-next-open convention is this
        # adapter's reading and needs owner confirmation.
        return {"strategy": v.candidate,
                "score_fn": _reversal(p["rsi_threshold"], p["hold_sessions"]),
                "max_hold_sessions": p["hold_sessions"]}
    # etf_dual_momentum
    if sessions is None or total_return is None or verified is None:
        raise ValueError("ETF adapter needs sessions, total_return and verified")
    if type(etf_use_standard_stops) is not bool:
        raise ValueError("choose etf_use_standard_stops explicitly")
    sessions = list(sessions)
    flags = {k: verified.get(k) is True for k in ("total_return", "universe", "calendar")}
    return {"strategy": v.candidate,
            "score_fn": _etf(p["lookback"], total_return, sessions, flags),
            "rebalance_days": month_start_sessions(sessions),
            "use_stops": etf_use_standard_stops}


def etf_first_eligible_rebalance(first_trade_day: Mapping[str, date],
                                 sessions: Sequence[date], lookback: int):
    """Earliest month-start rebalance at which EVERY ETF has lookback+1 bars.

    Returns None if never reached within `sessions`. Used to show how a late
    listing (XLC 2018, XLRE 2015) caps usable ETF history.
    """
    sessions = list(sessions)
    ready = []
    for symbol in SECTOR_ETF_UNIVERSE:
        start = first_trade_day[symbol]
        i = bisect_right(sessions, start - timedelta(days=1))
        if i + lookback >= len(sessions):
            return None
        ready.append(sessions[i + lookback])
    latest = max(ready)
    for d in sorted(month_start_sessions(sessions)):
        prev = sessions[sessions.index(d) - 1]
        if prev >= latest:
            return d
    return None
