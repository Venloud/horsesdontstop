"""Offline Phase 2 research statistics: NON-SCORED synthetic scaffolding.

No remote data, broker, Neon, holdout, or trading functionality. Passing
synthetic tests is not evidence of a strategy outperforming the benchmark.
"""
from __future__ import annotations

import csv
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from math import erf, isfinite, log, sqrt
from pathlib import Path
from random import Random
from statistics import NormalDist, mean, pstdev
from typing import Mapping, Sequence


PRIMARY_SLIPPAGE_BPS = 30
LEGACY_RUN_ID = 38108710763


@dataclass(frozen=True)
class DatedTradeReturn:
    day: date
    symbol: str
    net_r: float


@dataclass(frozen=True)
class DateBlockCI:
    mean_net_r: float
    lower_95_r: float
    upper_95_r: float
    trading_sessions: int
    block_sessions: int
    replicates: int


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values or not 0 <= fraction <= 1:
        raise ValueError("Invalid empirical quantile")
    pos = (len(sorted_values) - 1) * fraction
    a = int(pos)
    return sorted_values[a] + (sorted_values[min(a + 1, len(sorted_values)-1)]
                               - sorted_values[a]) * (pos - a)


def date_block_bootstrap(
    trades: Sequence[DatedTradeReturn], *,
    block_sessions: int, seed: int,
    replicates: int = 10000,
) -> DateBlockCI:
    """Moving block bootstrap of complete trading-date clusters.

    Each draw resamples contiguous blocks of whole observed trading days,
    keeping every same-day instrument/trade together. The denominator is
    the resampled number of trades, not sessions; absent/clustered days
    and serial dependence remain distinct adequacy concerns.
    """
    if (type(block_sessions) is not int or block_sessions < 1
            or type(seed) is not int or not 0 <= seed < 2**32
            or type(replicates) is not int or replicates < 10000):
        raise ValueError("Bootstrap block/seed/replicates not preregistered")
    if not trades or any(
        not isinstance(t, DatedTradeReturn)
        or type(t.day) is not date or not t.symbol
        or type(t.net_r) not in (float, int) or not isfinite(t.net_r)
        for t in trades
    ):
        raise ValueError("Invalid dated trade returns")
    days = [t.day for t in trades]
    if days != sorted(days):
        raise ValueError("Trades must be chronological")
    groups: list[list[float]] = []
    last = None
    for t in trades:
        if t.day != last:
            groups.append([])
            last = t.day
        groups[-1].append(t.net_r)
    n = len(groups)
    if n < 2 * block_sessions:
        raise ValueError("Not enough distinct trading dates for block bootstrap")
    group_sums = [sum(g) for g in groups]
    group_sizes = [len(g) for g in groups]
    rng = Random(seed)
    means = []
    for _ in range(replicates):
        seen = count = total = 0
        while seen < n:
            start = rng.randrange(n - block_sessions + 1)
            for j in range(start, min(start + block_sessions, start + n - seen)):
                total += group_sums[j]
                count += group_sizes[j]
                seen += 1
        means.append(total / count)
    means.sort()
    return DateBlockCI(
        mean_net_r=mean(t.net_r for t in trades),
        lower_95_r=_percentile(means, .025),
        upper_95_r=_percentile(means, .975),
        trading_sessions=n, block_sessions=block_sessions,
        replicates=replicates,
    )


@dataclass(frozen=True)
class MatchedExit:
    symbol: str
    entry_index: int
    exit_index: int
    whole_shares: int


@dataclass(frozen=True)
class PlaceboResult:
    strategy_mean_net_bps: float
    placebo_95th_percentile_bps: float
    trials: int
    sessions_exposed: int
    total_share_sessions: int
    # Fixed-horizon close/open placebo cannot model same-day stop-first
    # OHLC chronology or every dynamic trailing-stop exit decision.
    stop_policy_verified: bool = False


def matched_horizon_placebo(
    open_prices: Mapping[str, Sequence[float]],
    actual: Sequence[MatchedExit], *,
    eligible_entries: Mapping[str, Sequence[int]],
    seed: int, trials: int = 10000,
    slippage_bps_per_side: int = PRIMARY_SLIPPAGE_BPS,
) -> PlaceboResult:
    """Randomize open entries with exact symbol, share count and duration.

    Preserves exit lag, sessions of exposure, whole-share exposure by
    symbol, one-at-a-time positions and adverse entry/exit costs. Cash,
    actual stop-first exits and same-notional risk must be proven in a
    separate execution-equivalent placebo before promotion.
    """
    if (type(seed) is not int or not 0 <= seed < 2**32
            or type(trials) is not int or trials < 10000
            or type(slippage_bps_per_side) is not int
            or slippage_bps_per_side != PRIMARY_SLIPPAGE_BPS):
        raise ValueError("Unpreregistered placebo input")
    if not actual:
        raise ValueError("No actual trade templates")
    lengths = {len(v) for v in open_prices.values()}
    if (len(lengths) != 1 or not lengths
            or any(len(v) < 3 or any(
                type(p) not in (int, float) or not isfinite(p) or p <= 0
                for p in v) for v in open_prices.values())):
        raise ValueError("Invalid aligned synthetic open prices")
    n = next(iter(lengths))
    previous_exit = -1
    templates = []
    for t in actual:
        if (not isinstance(t, MatchedExit)
                or t.symbol not in open_prices
                or type(t.entry_index) is not int
                or type(t.exit_index) is not int
                or type(t.whole_shares) is not int
                or t.whole_shares <= 0
                or not 1 <= t.entry_index < t.exit_index < n
                or t.entry_index <= previous_exit):
            raise ValueError("Invalid or overlapping actual trades")
        previous_exit = t.exit_index
        lag = t.exit_index - t.entry_index
        candidates = eligible_entries.get(t.symbol, ())
        if (not isinstance(candidates, (list, tuple))
                or any(type(i) is not int or not 1 <= i < n-lag
                       for i in candidates)
                or not candidates):
            raise ValueError("Missing eligible placebo entry dates")
        templates.append((t, lag, tuple(sorted(set(candidates)))))
    slip = slippage_bps_per_side / 10000

    def ret(symbol, entry, exit_):
        v = open_prices[symbol]
        return (v[exit_] * (1-slip) / (v[entry] * (1+slip)) - 1) * 10000

    observed = mean(ret(t.symbol, t.entry_index, t.exit_index) for t in actual)
    rng = Random(seed)
    null = []
    for _ in range(trials):
        placed = []
        returns = []
        for t, lag, candidates in templates:
            allowed = [i for i in candidates
                       if all(i > hi or i+lag < lo for lo, hi in placed)]
            if not allowed:
                raise ValueError("Cannot match exposure without overlapping placebo positions")
            i = rng.choice(allowed)
            placed.append((i, i+lag))
            returns.append(ret(t.symbol, i, i+lag))
        null.append(mean(returns))
    null.sort()
    return PlaceboResult(
        strategy_mean_net_bps=observed,
        placebo_95th_percentile_bps=_percentile(null, .95),
        trials=trials,
        sessions_exposed=sum(t.exit_index-t.entry_index for t in actual),
        total_share_sessions=sum(
            (t.exit_index-t.entry_index)*t.whole_shares for t in actual),
    )


def archived_legacy_trial_count(csv_path: Path) -> int:
    """Number of logged prior tests, NOT count of independent datasets."""
    with csv_path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        if not reader.fieldnames or "trial_id" not in reader.fieldnames:
            raise ValueError("Missing immutable trial IDs")
        rows = list(reader)
    ids = [r.get("trial_id") for r in rows]
    if (len(rows) < 80 or len(set(ids)) != len(ids)
            or any(not i for i in ids)
            or any(r.get("source_run_id") != str(LEGACY_RUN_ID) for r in rows)):
        raise ValueError("Incomplete or inconsistent original trial ledger")
    return len(rows)


@dataclass(frozen=True)
class DeflatedSharpe:
    nominal_probability: float
    raw_trial_count: int
    periodic_sharpe: float
    comparison_threshold: float
    promotion_valid: bool = False  # correlation/selection proof NOT supplied


def deflated_sharpe_nominal(period_returns: Sequence[float], *,
                           logged_trial_count: int) -> DeflatedSharpe:
    """Exploratory DSR using raw logged trials as a conservative count.

    This is NOT a valid promotion probability without autocorrelation,
    effective-test correlation and strategy-selection adjustments.
    Sample moments use per-period returns, not annualized quantities.
    """
    if (type(logged_trial_count) is not int or logged_trial_count < 80
            or len(period_returns) < 30
            or any(type(v) not in (float, int) or not isfinite(v)
                   for v in period_returns)):
        raise ValueError("Missing DSR evidence or prior trial count")
    n = len(period_returns)
    avg = mean(period_returns)
    sd = pstdev(period_returns)
    if sd == 0:
        raise ValueError("Zero return variance cannot establish Sharpe")
    sr = avg / sd
    skew = mean(((v - avg)/sd)**3 for v in period_returns)
    kurt = mean(((v - avg)/sd)**4 for v in period_returns)
    variance = (1 - skew*sr + (kurt-1)*sr*sr/4) / (n-1)
    if not isfinite(variance) or variance <= 0:
        raise ValueError("Unreliable deflated Sharpe standard error")
    err = sqrt(variance)
    normal = NormalDist()
    gamma = 0.5772156649015329
    if logged_trial_count == 1:
        expected_max = 0.0
    else:
        expected_max = err * (
            (1-gamma)*normal.inv_cdf(1-1/logged_trial_count)
            + gamma*normal.inv_cdf(1-1/(logged_trial_count*2.718281828459045)))
    probability = normal.cdf((sr - expected_max) / err)
    return DeflatedSharpe(
        nominal_probability=probability,
        raw_trial_count=logged_trial_count,
        periodic_sharpe=sr,
        comparison_threshold=expected_max,
    )


@dataclass(frozen=True)
class Contribution:
    day: date
    amount_usd: float


@dataclass(frozen=True)
class MatchedBenchmark:
    sessions: tuple[date, ...]
    applied_contributions: tuple[Contribution, ...]
    passive_equity: tuple[float, ...]
    passive_time_weighted_return: float
    active_time_weighted_return: float
    passive_money_weighted_annualized: float
    active_money_weighted_annualized: float
    # Total-return index units are fractional research proxies, not
    # a cash/whole-share executable SPY ETF order simulation.
    passive_fractional_index_units: bool = True


def _xirr(start: date, deposits: Sequence[Contribution], final_day: date,
          final_equity: float, initial_cash: float) -> float:
    durations = [(d.day-start).days/365.0 for d in deposits]
    horizon = (final_day-start).days/365.0
    if horizon <= 0:
        raise ValueError("Nonpositive money-weighted measurement horizon")
    def npv(rate):
        return (-initial_cash
                - sum(d.amount_usd / ((1+rate)**duration)
                      for d, duration in zip(deposits, durations))
                + final_equity / ((1+rate)**horizon))
    lo, hi = -.9999, 1.0
    while npv(hi) > 0 and hi < 1e6:
        hi = hi*2+1
    if npv(lo) < 0 or npv(hi) > 0:
        raise ValueError("Money-weighted annualized IRR not bracketed")
    for _ in range(120):
        mid=(lo+hi)/2
        if npv(mid)>0:
            lo=mid
        else:
            hi=mid
    return (lo+hi)/2


def matched_deposit_spy_benchmark(
    spy_total_return_index: Sequence[tuple[date, float]],
    active_equity_after_deposits: Sequence[tuple[date, float]],
    contributions: Sequence[Contribution], *,
    active_contributions: Sequence[Contribution],
    initial_cash: float = 500.0,
    slippage_bps_per_side: int = PRIMARY_SLIPPAGE_BPS,
) -> MatchedBenchmark:
    """Match each REAL owner's dated deposit, never invent monthly funding.

    Non-market deposits move to the immediately following observed
    session in BOTH portfolios, with a shared post-close convention.
    Active NAV must include exactly the same deposits, and active
    transaction costs are assumed already embedded in its equity.
    """
    if (type(slippage_bps_per_side) is not int
            or slippage_bps_per_side != PRIMARY_SLIPPAGE_BPS
            or type(initial_cash) not in (int, float)
            or not isfinite(initial_cash) or initial_cash <= 0
            or len(spy_total_return_index) < 2
            or len(spy_total_return_index) != len(active_equity_after_deposits)):
        raise ValueError("Invalid matched benchmark configuration")
    dates = [d for d, _ in spy_total_return_index]
    if (any(type(d) is not date for d in dates)
            or dates != sorted(set(dates))
            or dates != [d for d, _ in active_equity_after_deposits]
            or any(type(p) not in (float,int) or not isfinite(p) or p <= 0
                   for _, p in spy_total_return_index)
            or any(type(v) not in (float,int) or not isfinite(v) or v <= 0
                   for _, v in active_equity_after_deposits)):
        raise ValueError("Unaligned total-return and active equity sessions")
    if (not isinstance(contributions, (list,tuple))
            or not isinstance(active_contributions, (list,tuple))
            or tuple(contributions) != tuple(active_contributions)):
        raise ValueError("Active/passive real deposit schedule mismatch")
    applied = []
    prev = None
    for c in contributions:
        if (not isinstance(c, Contribution)
                or type(c.day) is not date
                or type(c.amount_usd) not in (float,int)
                or not isfinite(c.amount_usd) or c.amount_usd <= 0
                or c.day <= dates[0] or c.day > dates[-1]
                or (prev is not None and c.day < prev)):
            raise ValueError("Unverified or out-of-window owner deposit")
        prev = c.day
        day = dates[bisect_left(dates, c.day)]
        applied.append(Contribution(day, c.amount_usd))
    deposits_by_day = {}
    for c in applied:
        deposits_by_day[c.day] = deposits_by_day.get(c.day, 0) + c.amount_usd
    slip = slippage_bps_per_side / 10000
    units = initial_cash / (spy_total_return_index[0][1] * (1+slip))
    passive = []
    for d, ix in spy_total_return_index:
        if d in deposits_by_day:
            units += deposits_by_day[d] / (ix*(1+slip))
        passive.append(units * ix*(1-slip))

    def twr(equity):
        previous = initial_cash
        growth = 1.0
        for d, nav in zip(dates, equity):
            flow = deposits_by_day.get(d, 0)
            if nav-flow <= 0 or previous <= 0:
                raise ValueError("Invalid deposit-adjusted equity")
            growth *= (nav-flow) / previous
            previous = nav
        return growth-1

    return MatchedBenchmark(
        sessions=tuple(dates), applied_contributions=tuple(applied),
        passive_equity=tuple(passive),
        passive_time_weighted_return=twr(passive),
        active_time_weighted_return=twr(
            [v for _, v in active_equity_after_deposits]),
        passive_money_weighted_annualized=_xirr(
            dates[0], applied, dates[-1], passive[-1], initial_cash),
        active_money_weighted_annualized=_xirr(
            dates[0], applied, dates[-1],
            active_equity_after_deposits[-1][1], initial_cash),
    )
