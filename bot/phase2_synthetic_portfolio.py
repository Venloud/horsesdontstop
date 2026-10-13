"""One-position CASH-only SYNTHETIC portfolio integration for six adapters.

This is an offline mechanics test, not a validated market backtest.
No network, portfolio optimization, fills from Alpaca, or Phase 3 scores.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Mapping, Sequence

from .phase2_research_signals import TotalReturnPoint
from .phase2_simulator_adapters import SIX_CORE_VARIANTS, next_open_eligibility
from .risk import max_shares
from .strategies import DailyBar

SLIPPAGE_BPS_PER_SIDE = 30
MAX_RISK_FRACTION = .01


@dataclass(frozen=True)
class SyntheticCoreTrade:
    symbol: str
    buy_day: date
    sell_day: date
    shares: int
    buy_fill: float
    sell_fill: float
    pnl_usd: float
    reason: str


def run_synthetic_core_portfolio(
    bars_by_symbol: Mapping[str, Sequence[DailyBar]],
    strategy: str, variant: tuple, symbols: Sequence[str], *,
    starting_cash: float = 500.0,
    synthetic_fixture: bool = False,
    pit_membership_verified: bool = False,
    calendar_verified: bool = False,
    total_return_verified: bool = False,
    total_return_series: Mapping[str, Sequence[TotalReturnPoint]] | None = None,
    sector_by_symbol: Mapping[str, str] | None = None,
) -> dict:
    """Exercise six core signal adapters under conservative DAY-bar fills.

    Inputs MUST be constructed synthetic fixtures: no scored or imported
    real-world market performance. Daily stops use prior levels and gap
    lows at the worse opening price. Same-day stop is conservatively
    allowed; no second opening trade when exposure existed at open.
    """
    if synthetic_fixture is not True:
        raise ValueError("Synthetic adapter may not score actual historical data")
    if strategy not in SIX_CORE_VARIANTS or variant not in SIX_CORE_VARIANTS[strategy]:
        raise ValueError("Not one of the six frozen core strategies/variants")
    if not isinstance(symbols, (list, tuple)) or not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("Invalid candidate symbols")
    if type(starting_cash) not in (float, int) or not 0 < starting_cash <= 1_000_000:
        raise ValueError("Invalid initial cash")
    if pit_membership_verified is not True or calendar_verified is not True:
        raise ValueError("Synthetic provenance/calendar attestations missing")
    if strategy == "etf_dual_momentum" and (
            set(symbols) != set(SIX_CORE_ETFS)
            or total_return_verified is not True):
        raise ValueError("ETF frozen universe and total-return attestations required")
    if "SPY" not in bars_by_symbol:
        raise ValueError("Missing SPY sessions")
    sessions = [bar.day for bar in bars_by_symbol["SPY"]]
    if len(sessions) < 3 or sessions != sorted(set(sessions)):
        raise ValueError("Unsorted SPY session calendar")
    # Enforce a complete synthetic price panel to prevent stale marks and
    # unaccounted stocks, including while one position is held.
    required = set(symbols) | {"SPY"}
    if strategy == "sector_leadership":
        if not isinstance(sector_by_symbol, Mapping) or any(
            sym not in sector_by_symbol for sym in symbols):
            raise ValueError("Missing PIT sector mappings")
        required |= {sector_by_symbol[sym] for sym in symbols}
    for sym in required:
        if sym not in bars_by_symbol or [x.day for x in bars_by_symbol[sym]] != sessions:
            raise ValueError("Missing or misaligned synthetic OHLCV panel")
    if strategy == "etf_dual_momentum":
        if not isinstance(total_return_series, Mapping):
            raise ValueError("ETF total-return index required")
        if set(total_return_series) != set(SIX_CORE_ETFS):
            raise ValueError("Frozen 12-ETF total-return universe mismatch")

    cash = float(starting_cash)
    held = None
    trades = []
    marks = []
    peak_equity = cash
    block_new_entries = False
    slip = SLIPPAGE_BPS_PER_SIDE/10000

    def exit_trade(day: date, mark: float, reason: str):
        nonlocal cash, held
        sell = mark*(1-slip)
        cash += held["shares"]*sell
        trades.append(SyntheticCoreTrade(
            held["symbol"], held["buy_day"], day, held["shares"],
            held["buy_fill"], sell,
            held["shares"]*(sell-held["buy_fill"]), reason))
        held = None

    for i, day in enumerate(sessions):
        began_with_position = held is not None
        can_roll_etf = (strategy == "etf_dual_momentum" and i > 0
                        and sessions[i-1].month != day.month)
        prior_close_equity = marks[-1] if marks else cash

        if held is not None:
            bar = bars_by_symbol[held["symbol"]][i]
            if (can_roll_etf
                    or (strategy == "short_term_reversal"
                        and held["held_sessions"] >= variant[1])):
                exit_trade(day, bar.open, "scheduled_next_open_exit")
            else:
                stop = max(held["initial_stop"], held["peak"]*.95)
                if bar.open <= stop:
                    exit_trade(day, bar.open, "gap_stop")
                elif bar.low <= stop:
                    exit_trade(day, stop, "stop_first")
                else:
                    held["peak"] = max(held["peak"], bar.high)
                    held["held_sessions"] += 1

        # End-of-window exits cannot create phantom same-day reentries.
        if (held is None and i > 0 and i < len(sessions)-1
                and not block_new_entries
                and (not began_with_position or can_roll_etf)):
            picks = []
            if strategy == "etf_dual_momentum":
                e = next_open_eligibility(
                    strategy, variant, entry_session=day,
                    total_return_series=total_return_series,
                    verified_session_calendar=sessions,
                    pit_membership_verified=True,
                    calendar_verified=True,
                    total_return_verified=True)
                if e is not None:
                    picks = [e]
            else:
                for sym in symbols:
                    sector_rows = ()
                    if strategy == "sector_leadership":
                        sector_rows = bars_by_symbol[sector_by_symbol[sym]][:i]
                    e = next_open_eligibility(
                        strategy, variant, entry_session=day, symbol=sym,
                        completed_history=bars_by_symbol[sym][:i],
                        sector_history=sector_rows,
                        spy_history=bars_by_symbol["SPY"][:i],
                        pit_membership_verified=True)
                    if e is not None:
                        picks.append(e)
            for e in sorted(picks, key=lambda x: (-x.score,x.symbol)):
                bar = bars_by_symbol[e.symbol][i]
                buy = bar.open*(1+slip)
                stop = buy*.97
                qty = max_shares(cash, buy, stop, MAX_RISK_FRACTION)
                if qty < 1:
                    continue
                cash -= qty*buy
                held = dict(
                    symbol=e.symbol, shares=qty, buy_fill=buy,
                    buy_day=day, peak=bar.open, initial_stop=stop,
                    held_sessions=1)
                # Simulate worst intraday chronology if the first day's
                # low breaches a protective stop immediately after entry.
                if bar.low <= stop:
                    exit_trade(day, stop, "entry_day_stop_first")
                else:
                    held["peak"] = max(held["peak"], bar.high)
                break

        if i == len(sessions)-1 and held is not None:
            exit_trade(day, bars_by_symbol[held["symbol"]][i].close, "end_of_window")
        mark = cash if held is None else (
            cash + held["shares"]*bars_by_symbol[held["symbol"]][i].close)
        marks.append(mark)
        peak_equity = max(peak_equity, mark)
        # Modeled daily-close risk limits prevent *new* entry afterward,
        # not an imaginary guaranteed emergency fill.
        if mark <= prior_close_equity*.97 or mark <= peak_equity*.85:
            block_new_entries = True

    return {
        "strategy": strategy,
        "variant": variant,
        "sessions": len(sessions),
        "initial_cash": starting_cash,
        "final_equity": round(marks[-1], 2),
        "trades": [asdict(t) for t in trades],
        "equity_curve": tuple(marks),
        "simulator_mode": "synthetic_mechanics_only_not_phase3",
        "risk_fraction_max": MAX_RISK_FRACTION,
        "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
        "new_entries_blocked_by_close_risk_gate": block_new_entries,
        "broker_order_allowed": False,
    }


# One unchanged frozen set, with XLC/XLRE inception dates requiring an
# independently documented 10-year-history applicability decision.
from .phase2_research_signals import SECTOR_ETF_UNIVERSE as SIX_CORE_ETFS
