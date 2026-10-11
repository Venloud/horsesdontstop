"""Deterministic long-only, close-to-close research simulator.

No brokerage access. Signals use only the previous completed close; fills use
the next bar's open, with configurable adverse slippage and stop gap handling.
"""
from dataclasses import dataclass
from typing import Sequence
from .risk import max_shares

@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float

@dataclass(frozen=True)
class Trade:
    entry_index: int
    exit_index: int
    shares: int
    entry: float
    exit: float
    pnl: float
    reason: str

def simulate(bars: Sequence[Bar], initial_cash: float = 500.0,
             risk_fraction: float = 0.01, stop_pct: float = 0.03,
             take_profit_pct: float = 0.06, slippage_bps: float = 10.0):
    """Illustrative trend signal: prior close > close two sessions ago.

    Orders enter at following open, exits at intrabar stop/target or final close.
    If both stop and target are touched, assume stop first (conservative).
    No compounding while a position is open, no shorting or leverage.
    """
    if initial_cash <= 0 or not 0 < stop_pct < take_profit_pct < 1:
        raise ValueError("Invalid capital, stop, or target")
    if not 0 < risk_fraction <= .02 or not 0 <= slippage_bps <= 100:
        raise ValueError("Invalid risk or slippage")
    if any(min(b.open, b.high, b.low, b.close) <= 0 or b.low > b.high
           or b.low > min(b.open, b.close) or b.high < max(b.open, b.close) for b in bars):
        raise ValueError("Invalid OHLC bar")
    cash = initial_cash
    trades = []
    i = 2
    while i < len(bars):
        if bars[i-1].close <= bars[i-2].close:
            i += 1
            continue
        entry = bars[i].open * (1 + slippage_bps / 10000)
        stop = entry * (1 - stop_pct)
        target = entry * (1 + take_profit_pct)
        qty = max_shares(cash, entry, stop, risk_fraction)
        if qty < 1:
            i += 1
            continue
        entry_index = i
        cash -= qty * entry
        while i < len(bars):
            b = bars[i]
            if b.open <= stop:
                exit_price, reason = b.open * (1 - slippage_bps / 10000), "gap_stop"
            elif b.low <= stop:
                exit_price, reason = stop * (1 - slippage_bps / 10000), "stop"
            elif b.high >= target:
                exit_price, reason = target * (1 - slippage_bps / 10000), "target"
            elif i == len(bars) - 1:
                exit_price, reason = b.close * (1 - slippage_bps / 10000), "end"
            else:
                i += 1
                continue
            cash += qty * exit_price
            trades.append(Trade(entry_index, i, qty, round(entry, 4),
                                round(exit_price, 4), round(qty * (exit_price - entry), 2), reason))
            i += 1
            break
    return {"initial_cash": initial_cash, "final_equity": round(cash, 2),
            "net_pnl": round(cash - initial_cash, 2), "trades": trades}
