"""Read-only single-position, shared-cash, daily-bar strategy comparison.

Simulates at most one whole-share long position across all candidate stocks.
No network calls, no Alpaca TradingClient, and no brokerage order code.
All entry signals are evaluated on the previous completed session.
"""
from dataclasses import asdict, dataclass
from typing import Callable, Collection, Mapping, Sequence

from .risk import max_shares, risk_budget
from .strategies import DailyBar, SECTOR_ETFS, STRATEGIES, signal_score


@dataclass(frozen=True)
class ResearchTrade:
    symbol: str
    entry_day: str
    exit_day: str
    shares: int
    entry: float
    exit: float
    pnl: float
    reason: str
    entry_cash: float
    exit_cash: float
    risk_budget: float


def run_portfolio(bars_by_symbol: Mapping[str, Sequence[DailyBar]],
                  strategy: str, symbols: Sequence[str],
                  risk_fraction: float = 0.02,
                  start_index: int = 0, end_index: int | None = None,
                  starting_cash: float = 500.0, slippage_bps: float = 10.0,
                  hard_stop_pct: float = 0.03, trailing_stop_pct: float = 0.05,
                  score_fn: Callable | None = None,
                  cash_flows: Mapping | None = None,
                  return_equity_curve: bool = False,
                  max_hold_sessions: int | None = None,
                  rebalance_days: Collection | None = None,
                  use_stops: bool = True) -> dict:
    """Research using SPY-session dates and next-session open entries.

    Intraday stops use the previously known stop; today's high updates a
    trailing stop only for tomorrow. Stop gaps fill at the worse opening
    price. Stops can lose more than intended. End-of-period marks are closed
    with adverse slippage. Portfolio drawdown is DAILY close-to-close, not an
    intraday worst-case measure. No leverage, fractional shares, fees,
    tax, dividends, or intraday market impact.

    score_fn (research harness only): optional replacement for the coded
    signal, called as score_fn(symbol, history, sector_rows, market_rows)
    with the same completed-bar slices. None keeps the legacy behavior.

    cash_flows (research harness only): {session date: deposit > 0}, already
    aligned with research_stats.align_cash_flows so the passive benchmark
    gets the identical schedule. Each deposit lands before that session's
    open. Drawdown is then measured on unit NAV, not raw equity, so deposits
    cannot hide losses. None keeps the legacy behavior and output.

    Strategy-specific exit policies (research harness only; defaults keep the
    legacy behavior): max_hold_sessions=N sells at the open N sessions after
    entry unless a stop hit first (no re-entry that day). rebalance_days sells
    any holding at that session's open and allows a new entry the same open.
    use_stops=False disables the 3%/trailing stops (1R stays the modeled 3%).
    """
    if score_fn is None and strategy not in STRATEGIES:
        raise ValueError("unsupported strategy")
    if score_fn is not None and (not isinstance(strategy, str) or not strategy):
        raise ValueError("score_fn runs need a non-empty strategy label")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("provide unique candidate symbols")
    if (starting_cash <= 0 or not 0 < risk_fraction <= 0.02
            or not 0 < hard_stop_pct < trailing_stop_pct < 1
            or not 0 <= slippage_bps <= 100):
        raise ValueError("invalid simulation parameters")
    if cash_flows is not None and any(
            not isinstance(a, (int, float)) or not a > 0
            for a in cash_flows.values()):
        raise ValueError("cash flows must be positive deposits")
    if max_hold_sessions is not None and (
            type(max_hold_sessions) is not int or max_hold_sessions < 1):
        raise ValueError("max_hold_sessions must be a positive int")
    if type(use_stops) is not bool:
        raise ValueError("use_stops must be a bool")
    if "SPY" not in bars_by_symbol:
        raise ValueError("SPY benchmark required")
    series = {}
    index = {}
    for symbol, rows in bars_by_symbol.items():
        seq = sorted(rows, key=lambda row: row.day)
        if len(set(b.day for b in seq)) != len(seq):
            raise ValueError("duplicate session in " + symbol)
        series[symbol] = seq
        index[symbol] = {bar.day: i for i, bar in enumerate(seq)}
    if not series["SPY"]:
        raise ValueError("empty SPY benchmark")
    for symbol in symbols:
        if symbol not in series:
            raise ValueError("missing symbol " + symbol)
    market_days = [b.day for b in series["SPY"]]
    end_index = len(market_days) if end_index is None else end_index
    if not 0 <= start_index < end_index <= len(market_days):
        raise ValueError("invalid date window")
    cash = starting_cash
    held = None
    trades = []
    equity_curve = []
    slip = slippage_bps / 10000
    first_1000_day = None
    units = starting_cash  # unit NAV starts at 1.0 (flows only)
    nav_curve = []
    flow_events = []
    if cash_flows is not None and set(cash_flows) - set(market_days[start_index:end_index]):
        raise ValueError("cash flows must fall on sessions inside the window")

    def close_position(day, price, reason):
        nonlocal cash, held
        exit_fill = price * (1 - slip)
        cash += held["shares"] * exit_fill
        pnl = held["shares"] * (exit_fill - held["entry"])
        trades.append(ResearchTrade(
            held["symbol"], held["day"].isoformat(), day.isoformat(),
            held["shares"], round(held["entry"], 4), round(exit_fill, 4),
            round(pnl, 2), reason, round(held["entry_cash"], 2),
            round(cash, 2), round(held["risk_budget"], 2)))
        held = None

    for day_i in range(start_index, end_index):
        day = market_days[day_i]
        previous_market_day = market_days[day_i - 1] if day_i else None
        held_at_open = held is not None
        if cash_flows and cash_flows.get(day):
            value = cash
            if held is not None:
                hi_ = index[held["symbol"]].get(day)
                if hi_ is None:
                    raise ValueError(f"Missing held-stock daily bar: {held['symbol']} on {day}")
                value += held["shares"] * series[held["symbol"]][hi_].open
            amount = float(cash_flows[day])
            flow_events.append((day.isoformat(), value, amount))
            units += amount / (value / units)
            cash += amount
        if held is not None:
            symbol = held["symbol"]
            current_i = index[symbol].get(day)
            if current_i is None:
                # Marking the holding with a stale last close invents a price
                # path. Stop rather than quietly overstate performance.
                raise ValueError(f"Missing held-stock daily bar: {symbol} on {day}")
            forced = None
            if (rebalance_days is not None and day in rebalance_days
                    and day != held["day"]):
                forced = "rebalance"
            elif (max_hold_sessions is not None
                    and day_i - held["day_index"] >= max_hold_sessions):
                forced = "max_hold"
            if forced:
                close_position(day, series[symbol][current_i].open, forced)
                if forced == "rebalance":
                    held_at_open = False  # rotation re-enters at this open
            elif not use_stops:
                bar = series[symbol][current_i]
                held["peak_before_today"] = max(held["peak_before_today"], bar.high)
                held["last_close"] = bar.close
            else:
                bar = series[symbol][current_i]
                # Stop levels derive strictly from completed PRIOR bars.
                stop = max(held["initial_stop"],
                           held["peak_before_today"] * (1 - trailing_stop_pct))
                if bar.open <= stop:
                    close_position(day, bar.open, "gap_stop")
                elif bar.low <= stop:
                    close_position(day, stop, "stop")
                else:
                    held["peak_before_today"] = max(held["peak_before_today"], bar.high)
                    held["last_close"] = bar.close

        # Never reopen on a day where we held a position at the open.
        if held is None and not held_at_open and day_i < end_index - 1:
            ranked = []
            for symbol in symbols:
                current_i = index[symbol].get(day)
                if current_i is None or current_i == 0:
                    continue
                history = series[symbol][:current_i]
                if history[-1].day != previous_market_day:
                    continue  # reject stale candidate data
                sector_rows = ()
                market_rows = ()
                if strategy == "sector_leadership":
                    sector_symbol = SECTOR_ETFS.get(symbol)
                    if not sector_symbol or sector_symbol not in index:
                        continue
                    sector_i = index[sector_symbol].get(day)
                    market_i = index["SPY"].get(day)
                    if sector_i is None or market_i is None:
                        continue
                    sector_rows = series[sector_symbol][:sector_i]
                    market_rows = series["SPY"][:market_i]
                if score_fn is None:
                    score = signal_score(strategy, history, sector_rows, market_rows)
                else:
                    score = score_fn(symbol, history, sector_rows, market_rows)
                if score is not None and score > 0:
                    ranked.append((score, symbol))
            # Stable tie breaking and one shared cash balance.
            for score, symbol in sorted(ranked, key=lambda row: (-row[0], row[1])):
                bar = series[symbol][index[symbol][day]]
                entry = bar.open * (1 + slip)
                initial_stop = entry * (1 - hard_stop_pct)
                qty = max_shares(cash, entry, initial_stop, risk_fraction)
                if qty < 1:
                    continue
                entry_cash = cash
                cash -= qty * entry
                held = {"symbol": symbol, "shares": qty, "entry": entry,
                        "entry_cash": entry_cash,
                        "risk_budget": risk_budget(entry_cash, risk_fraction),
                        "initial_stop": initial_stop, "peak_before_today": bar.open,
                        "last_close": bar.close, "day": day,
                        "day_index": day_i}
                # Open-to-low order is observable only in coarse daily bars;
                # assume the conservative stop-first outcome on this entry bar.
                if use_stops and bar.low <= initial_stop:
                    close_position(day, initial_stop, "entry_day_stop")
                else:
                    held["peak_before_today"] = max(bar.open, bar.high)
                break

        if day_i == end_index - 1 and held is not None:
            # Liquidate the strategy's open exposure at period-end close.
            close_position(day, held["last_close"], "end_of_window")
        equity = cash if held is None else cash + held["shares"] * held["last_close"]
        equity_curve.append(equity)
        nav_curve.append(equity / units)
        if first_1000_day is None and equity >= 1000:
            first_1000_day = day.isoformat()

    peak = starting_cash
    max_drawdown = 0.0
    for equity in (equity_curve if cash_flows is None else
                   [starting_cash * v for v in nav_curve]):
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 100 * (peak - equity) / peak)
    gains = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    wins = sum(t.pnl > 0 for t in trades)
    first_spy = series["SPY"][start_index].close
    last_spy = series["SPY"][end_index - 1].close
    extra = {}
    if cash_flows is not None:
        extra = {"cash_flow_events": flow_events,
                 "total_deposits": round(sum(a for _, _, a in flow_events), 2),
                 "unit_nav_twr_pct": round(100 * (nav_curve[-1] - 1), 4)}
    if return_equity_curve:
        extra["equity_curve"] = [(market_days[start_index + k].isoformat(), e)
                                 for k, e in enumerate(equity_curve)]
    return {
        **extra,
        "strategy": strategy,
        "risk_pct": 100 * risk_fraction,
        "first_day": market_days[start_index].isoformat(),
        "last_day": market_days[end_index - 1].isoformat(),
        "sessions": len(equity_curve),
        "initial_cash": starting_cash,
        "reinvest_realized_profits_from_first_trade": True,
        "first_1000_equity_day": first_1000_day,
        "final_equity": round(equity_curve[-1], 2),
        "net_pnl": round(equity_curve[-1] - starting_cash, 2),
        "return_pct": round(100 * (equity_curve[-1] / starting_cash - 1), 2),
        "max_daily_close_drawdown_pct": round(max_drawdown, 2),
        "trade_count": len(trades),
        "win_rate_pct": round(100 * wins / len(trades), 2) if trades else None,
        "profit_factor": round(gains / losses, 3) if losses else None,
        "spy_price_return_pct": round(100 * (last_spy / first_spy - 1), 2),
        "trades": [asdict(t) for t in trades],
    }
