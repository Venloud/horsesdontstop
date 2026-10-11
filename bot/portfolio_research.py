"""Read-only single-position, shared-cash, daily-bar strategy comparison.

Simulates at most one whole-share long position across all candidate stocks.
No network calls, no Alpaca TradingClient, and no brokerage order code.
All entry signals are evaluated on the previous completed session.
"""
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from .risk import max_shares
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


def run_portfolio(bars_by_symbol: Mapping[str, Sequence[DailyBar]],
                  strategy: str, symbols: Sequence[str],
                  risk_fraction: float = 0.02,
                  start_index: int = 0, end_index: int | None = None,
                  starting_cash: float = 500.0, slippage_bps: float = 10.0,
                  hard_stop_pct: float = 0.03, trailing_stop_pct: float = 0.05) -> dict:
    """Research using SPY-session dates and next-session open entries.

    Intraday stops use the previously known stop; today's high updates a
    trailing stop only for tomorrow. Stop gaps fill at the worse opening
    price. Stops can lose more than intended. End-of-period marks are closed
    with adverse slippage. Portfolio drawdown is DAILY close-to-close, not an
    intraday worst-case measure. No leverage, fractional shares, fees,
    tax, dividends, or intraday market impact.
    """
    if strategy not in STRATEGIES:
        raise ValueError("unsupported strategy")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("provide unique candidate symbols")
    if (starting_cash <= 0 or not 0 < risk_fraction <= 0.02
            or not 0 < hard_stop_pct < trailing_stop_pct < 1
            or not 0 <= slippage_bps <= 100):
        raise ValueError("invalid simulation parameters")
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

    def close_position(day, price, reason):
        nonlocal cash, held
        exit_fill = price * (1 - slip)
        cash += held["shares"] * exit_fill
        pnl = held["shares"] * (exit_fill - held["entry"])
        trades.append(ResearchTrade(
            held["symbol"], held["day"].isoformat(), day.isoformat(),
            held["shares"], round(held["entry"], 4), round(exit_fill, 4),
            round(pnl, 2), reason))
        held = None

    for day_i in range(start_index, end_index):
        day = market_days[day_i]
        previous_market_day = market_days[day_i - 1] if day_i else None
        held_at_open = held is not None
        if held is not None:
            symbol = held["symbol"]
            current_i = index[symbol].get(day)
            if current_i is not None:
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
                score = signal_score(strategy, history, sector_rows, market_rows)
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
                cash -= qty * entry
                held = {"symbol": symbol, "shares": qty, "entry": entry,
                        "initial_stop": initial_stop, "peak_before_today": bar.open,
                        "last_close": bar.close, "day": day}
                # Open-to-low order is observable only in coarse daily bars;
                # assume the conservative stop-first outcome on this entry bar.
                if bar.low <= initial_stop:
                    close_position(day, initial_stop, "entry_day_stop")
                else:
                    held["peak_before_today"] = max(bar.open, bar.high)
                break

        if day_i == end_index - 1 and held is not None:
            # Liquidate the strategy's open exposure at period-end close.
            close_position(day, held["last_close"], "end_of_window")
        equity = cash if held is None else cash + held["shares"] * held["last_close"]
        equity_curve.append(equity)

    peak = starting_cash
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 100 * (peak - equity) / peak)
    gains = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    wins = sum(t.pnl > 0 for t in trades)
    first_spy = series["SPY"][start_index].close
    last_spy = series["SPY"][end_index - 1].close
    return {
        "strategy": strategy,
        "risk_pct": 100 * risk_fraction,
        "first_day": market_days[start_index].isoformat(),
        "last_day": market_days[end_index - 1].isoformat(),
        "sessions": len(equity_curve),
        "initial_cash": starting_cash,
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
