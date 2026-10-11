"""Offline audit metrics for existing backtest trades, no broker access."""
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class Audit:
    trade_count: int
    win_rate_pct: float | None
    profit_factor: float | None
    max_closed_trade_drawdown_pct: float
    ending_equity: float
    return_pct: float

def audit_trades(trades: Sequence, initial_equity: float = 500.0) -> Audit:
    """Closed-trade equity drawdown, NOT intratrade mark-to-market drawdown.

    Assumes a single sequential strategy, with no deposits and no overlapping
    positions. Not suitable for summing separate per-symbol simulations.
    """
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    equity = peak = initial_equity
    worst = 0.0
    wins = losses = 0.0
    win_count = 0
    for trade in trades:
        pnl = float(trade.pnl)
        if pnl > 0:
            wins += pnl
            win_count += 1
        elif pnl < 0:
            losses -= pnl
        equity += pnl
        if equity < 0:
            raise ValueError("negative equity")
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak * 100)
    n = len(trades)
    return Audit(n, round(win_count / n * 100, 2) if n else None,
                 round(wins / losses, 3) if losses else None,
                 round(worst, 2), round(equity, 2),
                 round((equity / initial_equity - 1) * 100, 2))

def conservative_exit(open_price: float, low: float, high: float,
                      stop: float, target: float, slippage_bps: float = 10.0):
    """Conservative OHLC execution: gap below stop fills at worse open;
    intrabar stop precedes target when both touched. No price improvement.
    """
    if min(open_price, low, high, stop, target) <= 0 or low > high or stop >= target:
        raise ValueError("invalid prices")
    if not 0 <= slippage_bps <= 100:
        raise ValueError("invalid slippage")
    slip = 1 - slippage_bps / 10000
    if open_price <= stop:
        return open_price * slip, "gap_stop"
    if low <= stop:
        return stop * slip, "stop"
    if open_price >= target:
        return target * slip, "gap_target"
    if high >= target:
        return target * slip, "target"
    return None
