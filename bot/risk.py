def risk_budget(equity: float, fraction: float = 0.01) -> float:
    if equity <= 0 or not 0 < fraction <= 0.02:
        raise ValueError("Invalid equity or risk fraction")
    return round(equity * fraction, 2)

def max_shares(equity: float, entry: float, stop: float, fraction: float = 0.01) -> int:
    if not 0 < stop < entry:
        raise ValueError("Long stop must be below entry and positive")
    return min(int(risk_budget(equity, fraction) / (entry - stop)), int(equity / entry))

def daily_limit_hit(open_equity: float, equity: float) -> bool:
    if open_equity <= 0:
        raise ValueError("Invalid opening equity")
    return equity <= open_equity * 0.97
