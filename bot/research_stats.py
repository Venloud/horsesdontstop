"""Offline statistics for PREREGISTRATION section 6. No network, no broker.

- date_block_bootstrap: mean-R confidence bound resampling trading-day blocks
- placebo_test: random entries through the SAME run function (exits, stops,
  costs, cash, universe), with an exposure-match validity check
- deflated_sharpe: Bailey & Lopez de Prado (2014) with the trial count
- matched_deposit_benchmark / twr / mwr: passive comparator on the exact
  same aligned cash-flow schedule as the strategy
"""
from __future__ import annotations

import csv
import math
import random
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist, mean, median, pstdev
from typing import Callable, Mapping, Sequence

PREREG_MIN_RESAMPLES = 10_000
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


# ----------------------------------------------------------- block bootstrap

@dataclass(frozen=True)
class BootstrapResult:
    mean: float
    lower: float
    upper: float
    resamples: int
    block_length: int
    seed: int
    prereg_compliant: bool  # resamples >= 10,000


def date_block_bootstrap(session_index: Sequence[int], values: Sequence[float],
                         n_sessions: int, *, block_length: int, resamples: int,
                         seed: int, alpha: float = 0.05) -> BootstrapResult:
    """Circular block bootstrap of the mean over the SESSION calendar.

    Each trade value is tagged with its entry session (0..n_sessions-1).
    Whole blocks of consecutive sessions are resampled, so trades on the
    same or nearby days stay together and co-movement is preserved.
    Block length and seed must be committed before the run.
    """
    if len(session_index) != len(values) or not values:
        raise ValueError("need one session index per value")
    if type(block_length) is not int or not 1 <= block_length <= n_sessions:
        raise ValueError("invalid block length")
    if type(resamples) is not int or resamples < 100:
        raise ValueError("too few resamples")
    if any(type(i) is not int or not 0 <= i < n_sessions for i in session_index):
        raise ValueError("session index outside calendar")
    if any(not math.isfinite(v) for v in values):
        raise ValueError("nonfinite value")
    sums = [0.0] * n_sessions
    counts = [0] * n_sessions
    for i, v in zip(session_index, values):
        sums[i] += v
        counts[i] += 1
    # Prefix sums over a doubled calendar make each circular block O(1).
    ps, pc = [0.0], [0]
    for k in range(2 * n_sessions):
        ps.append(ps[-1] + sums[k % n_sessions])
        pc.append(pc[-1] + counts[k % n_sessions])
    rng = random.Random(seed)
    n_blocks = math.ceil(n_sessions / block_length)
    stats = []
    while len(stats) < resamples:
        s = c = 0
        for _ in range(n_blocks):
            a = rng.randrange(n_sessions)
            s += ps[a + block_length] - ps[a]
            c += pc[a + block_length] - pc[a]
        if c:
            stats.append(s / c)
    stats.sort()
    lo = stats[int(math.floor(alpha / 2 * (resamples - 1)))]
    hi = stats[int(math.ceil((1 - alpha / 2) * (resamples - 1)))]
    return BootstrapResult(mean(values), lo, hi, resamples, block_length,
                           seed, resamples >= PREREG_MIN_RESAMPLES)


# ------------------------------------------------------------------ placebo

def exposure_fraction(result: Mapping, sessions: Sequence[date]) -> float:
    """Share of sessions with an open position (entry..exit inclusive)."""
    pos = {d.isoformat(): i for i, d in enumerate(sessions)}
    held = set()
    for t in result["trades"]:
        held.update(range(pos[t["entry_day"]], pos[t["exit_day"]] + 1))
    return len(held) / len(sessions)


def mean_hold_sessions(result: Mapping, sessions: Sequence[date]) -> float:
    pos = {d.isoformat(): i for i, d in enumerate(sessions)}
    holds = [pos[t["exit_day"]] - pos[t["entry_day"]] + 1 for t in result["trades"]]
    return mean(holds) if holds else float("nan")


@dataclass(frozen=True)
class PlaceboResult:
    strategy_mean_r: float
    placebo_p95: float
    placebo_trials: int
    placebo_runs: int  # includes top-up runs for short trials
    probability: float
    strategy_exposure: float
    placebo_exposure_band: tuple  # (p05, p50, p95) per placebo run
    strategy_mean_hold: float
    placebo_hold_band: tuple  # (p05, p50, p95) of trial mean holds, report only
    strategy_trades: int
    exposure_matched: bool
    beats_p95: bool
    status: str  # pass | fail | invalid_exposure_mismatch
    prereg_compliant: bool


def _pct(sorted_vals, q):
    return sorted_vals[int(math.ceil(q * (len(sorted_vals) - 1)))]


def placebo_test(strategy_score: Callable,
                 run_with_score: Callable[[Callable], Mapping],
                 make_random_score: Callable[[int, float], Callable],
                 r_of: Callable[[Mapping], Sequence[float]],
                 sessions: Sequence[date], *, trials: int, seed: int,
                 pilot: int = 20, max_runs_per_trial: int = 10,
                 p_bounds: tuple = (1e-4, 1.0)) -> PlaceboResult:
    """Strategy mean R must exceed the 95th percentile of random-entry means.

    The strategy itself is run as run_with_score(strategy_score), and every
    placebo as run_with_score(random_score), so exits, stops, costs, cash
    and universe are identical by construction; only the entry signal differs.
    1. The random entry rate is calibrated on pilot seeds so the placebo's
       median EXPOSURE (share of sessions in a position) equals the
       strategy's. (Matching trade count does not match exposure.)
    2. Each placebo trial holds exactly the strategy's trade count N: a run
       with more trades is subsampled, a run with fewer is topped up from
       further seeded runs. So placebo means carry the same sampling noise.
    INVALID, never a pass, if the strategy's exposure lies outside the
    placebo's 5-95% exposure band (the rate could not be matched). Holding
    periods are reported, not gated: a real edge can change them.
    r_of(result) must return R in the same order as result["trades"].
    """
    strategy_result = run_with_score(strategy_score)
    strategy_r = list(r_of(strategy_result))
    if not strategy_r:
        raise ValueError("strategy has no trades")
    n = len(strategy_r)
    pos = {d.isoformat(): i for i, d in enumerate(sessions)}
    s_exp = exposure_fraction(strategy_result, sessions)
    s_hold = mean_hold_sessions(strategy_result, sessions)

    def median_exposure(p):
        return median(exposure_fraction(
            run_with_score(make_random_score(seed + 10_000_000 + k, p)), sessions)
            for k in range(pilot))
    lo, hi = p_bounds
    for _ in range(14):  # bisection on log-probability
        mid = math.sqrt(lo * hi)
        if median_exposure(mid) < s_exp:
            lo = mid
        else:
            hi = mid
    p = math.sqrt(lo * hi)
    rng = random.Random(seed)
    means, holds, exposures, runs, short = [], [], [], 0, 0
    extra = seed + 20_000_000
    for k in range(trials):
        pool = []
        for attempt in range(max_runs_per_trial):
            if attempt == 0:
                res = run_with_score(make_random_score(seed + k, p))
                exposures.append(exposure_fraction(res, sessions))
            else:
                res = run_with_score(make_random_score(extra, p))
                extra += 1
            runs += 1
            rs = list(r_of(res))
            pool += [(r, pos[t["exit_day"]] - pos[t["entry_day"]] + 1)
                     for r, t in zip(rs, res["trades"])]
            if len(pool) >= n:
                break
        if len(pool) < n:
            short += 1
            continue
        pick = rng.sample(pool, n)
        means.append(mean(r for r, _ in pick))
        holds.append(mean(h for _, h in pick))
    for v in (means, holds, exposures):
        v.sort()
    if short or not means:
        nan = float("nan")
        return PlaceboResult(mean(strategy_r), nan, trials, runs, p, s_exp,
                             tuple(_pct(exposures, q) for q in (0.05, 0.5, 0.95)),
                             s_hold, (nan, nan, nan), n, False, False,
                             "invalid_exposure_mismatch",
                             trials >= PREREG_MIN_RESAMPLES)
    e_band = tuple(_pct(exposures, q) for q in (0.05, 0.5, 0.95))
    h_band = tuple(_pct(holds, q) for q in (0.05, 0.5, 0.95))
    exp_ok = e_band[0] <= s_exp <= e_band[2]
    p95 = _pct(means, 0.95)
    beats = mean(strategy_r) > p95
    status = ("invalid_exposure_mismatch" if not exp_ok
              else "pass" if beats else "fail")
    return PlaceboResult(mean(strategy_r), p95, trials, runs, p, s_exp, e_band,
                         s_hold, h_band, n, exp_ok, beats, status,
                         trials >= PREREG_MIN_RESAMPLES)


# --------------------------------------------------------- deflated Sharpe

def _moments(x: Sequence[float]):
    m = mean(x)
    sd = pstdev(x)
    if sd == 0:
        raise ValueError("zero-variance returns")
    skew = sum(((v - m) / sd) ** 3 for v in x) / len(x)
    kurt = sum(((v - m) / sd) ** 4 for v in x) / len(x)  # normal = 3
    return m, sd, skew, kurt


def lag1_autocorrelation(x: Sequence[float]) -> float:
    m = mean(x)
    den = sum((v - m) ** 2 for v in x)
    return sum((x[i] - m) * (x[i - 1] - m) for i in range(1, len(x))) / den


def expected_max_sharpe(n_trials: int, var_trial_sharpes: float) -> float:
    """E[max] of N independent trial Sharpes under a zero-skill null."""
    if n_trials < 1 or var_trial_sharpes < 0:
        raise ValueError("invalid trial inputs")
    if n_trials == 1:
        return 0.0
    return math.sqrt(var_trial_sharpes) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def probabilistic_sharpe(sr: float, benchmark_sr: float, t: float,
                         skew: float, kurt: float) -> float:
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if t <= 1 or denom <= 0:
        raise ValueError("cannot evaluate PSR")
    return _N.cdf((sr - benchmark_sr) * math.sqrt(t - 1) / math.sqrt(denom))


@dataclass(frozen=True)
class DeflatedSharpe:
    sharpe: float
    sr0: float
    n_trials: int
    effective_t: float
    lag1_autocorrelation: float
    probability: float
    passes_95: bool


def deflated_sharpe(returns: Sequence[float], *, n_trials: int,
                    var_trial_sharpes: float,
                    adjust_autocorrelation: bool = True) -> DeflatedSharpe:
    """Non-annualized Sharpe of `returns`, deflated for N trials.

    All Sharpes (this one and var_trial_sharpes) must use the SAME return
    frequency. Positive lag-1 autocorrelation shrinks the effective sample
    size T * (1 - rho) / (1 + rho); negative rho is not rewarded.
    """
    if len(returns) < 30:
        raise ValueError("too few returns for a deflated Sharpe")
    m, sd, skew, kurt = _moments(returns)
    sr = m / sd
    rho = lag1_autocorrelation(returns)
    t = float(len(returns))
    if adjust_autocorrelation and rho > 0:
        t = t * (1 - rho) / (1 + rho)
    sr0 = expected_max_sharpe(n_trials, var_trial_sharpes)
    p = probabilistic_sharpe(sr, sr0, t, skew, kurt)
    return DeflatedSharpe(sr, sr0, n_trials, t, rho, p, p >= 0.95)


def ledger_trial_count(ledger_csv: str, new_trial_ids: Sequence[str] = ()) -> int:
    """Logged ledger trials plus new, distinct trial IDs. Never fewer."""
    with open(ledger_csv, newline="") as f:
        ids = [r["trial_id"] for r in csv.DictReader(f)]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate trial IDs in ledger")
    extra = set(new_trial_ids)
    if extra & set(ids):
        raise ValueError("new trial ID already in ledger")
    if len(extra) != len(new_trial_ids):
        raise ValueError("duplicate new trial IDs")
    return len(ids) + len(extra)


def per_trade_sharpes(rows: Sequence[Mapping], hard_stop_pct: float = 0.03):
    """Per-trade R Sharpe of each archived trial row with >= 2 trades."""
    out = []
    for row in rows:
        rs = [(t["exit"] - t["entry"]) / (t["entry"] * hard_stop_pct)
              for t in row["trades"]]
        if len(rs) >= 2 and pstdev(rs) > 0:
            out.append(mean(rs) / pstdev(rs))
    return out


# ------------------------------------------------ matched-deposit benchmark

def align_cash_flows(flows: Sequence[tuple[date, float]],
                     sessions: Sequence[date]) -> dict[date, float]:
    """Map each owner deposit to the same session for BOTH comparators.

    A flow on a non-session day moves to the NEXT session. Flows after the
    last session are rejected rather than dropped. Amounts must be > 0
    (withdrawals are out of scope here).
    """
    if list(sessions) != sorted(set(sessions)):
        raise ValueError("sessions must be sorted and unique")
    out: dict[date, float] = {}
    for d, amount in flows:
        if not isinstance(d, date) or not (isinstance(amount, (int, float))
                                           and math.isfinite(amount) and amount > 0):
            raise ValueError("invalid cash flow")
        i = bisect_left(sessions, d)
        if i == len(sessions):
            raise ValueError("cash flow after the last session")
        if i == 0 and d < sessions[0]:
            raise ValueError("cash flow before the first session")
        out[sessions[i]] = out.get(sessions[i], 0.0) + float(amount)
    return out


@dataclass(frozen=True)
class BenchmarkResult:
    days: tuple
    equity: tuple
    flow_events: tuple  # (day, value just before the flow, amount)
    total_deposits: float
    twr: float
    total_return_complete: bool


def matched_deposit_benchmark(bars: Sequence, initial_cash: float,
                              aligned_flows: Mapping[date, float], *,
                              dividends: Mapping[date, float] | None,
                              fee_bps_per_side: float = 0.0,
                              fractional: bool = True) -> BenchmarkResult:
    """Buy-and-hold SPY on the strategy's exact cash schedule.

    Initial cash and each aligned flow buy at that session's OPEN, the same
    convention as run_portfolio(cash_flows=...); equity is marked at the
    close. Dividends (per share, keyed by ex-date) are reinvested at that
    close. dividends=None means price-only and is flagged incomplete.
    Whole-share mode leaves the remainder in cash (cash drag).
    """
    if initial_cash <= 0 or not 0 <= fee_bps_per_side <= 100:
        raise ValueError("invalid benchmark inputs")
    days = [b.day for b in bars]
    if days != sorted(set(days)):
        raise ValueError("bars must be sorted and unique")
    if set(aligned_flows) - set(days):
        raise ValueError("flows must be pre-aligned to sessions")
    fee = fee_bps_per_side / 10000
    shares = cash = 0.0
    equity, events = [], []

    def buy(amount, price):
        nonlocal shares, cash
        cash += amount
        unit = price * (1 + fee)
        qty = cash / unit if fractional else math.floor(cash / unit)
        shares += qty
        cash -= qty * unit

    for i, b in enumerate(bars):
        if i == 0:
            buy(initial_cash, b.open)
        amount = aligned_flows.get(b.day, 0.0)
        if amount:
            events.append((b.day, cash + shares * b.open, amount))
            buy(amount, b.open)
        if dividends and dividends.get(b.day):
            buy(shares * dividends[b.day], b.close)
        equity.append(cash + shares * b.close)
    twr = time_weighted_return(initial_cash, [(v, a) for _, v, a in events],
                               equity[-1])
    return BenchmarkResult(tuple(days), tuple(equity), tuple(events),
                           initial_cash + sum(aligned_flows.values()), twr,
                           dividends is not None)


def time_weighted_return(start_value: float,
                         events: Sequence[tuple[float, float]],
                         end_value: float) -> float:
    """Exact TWR from valuations taken immediately before each flow.

    events: (portfolio value just before the flow, flow amount), in order.
    """
    if start_value <= 0:
        raise ValueError("invalid start value")
    growth, base = 1.0, start_value
    for value, amount in events:
        growth *= value / base
        base = value + amount
    return growth * end_value / base - 1


def money_weighted_return(cash_flows: Sequence[tuple[date, float]],
                          final_day: date, final_value: float) -> float:
    """Annualized IRR (ACT/365). Contributions are positive amounts in."""
    if not cash_flows or final_value <= 0:
        raise ValueError("invalid flows")
    t0 = min(d for d, _ in cash_flows)

    def npv(r):
        v = final_value / (1 + r) ** ((final_day - t0).days / 365)
        return v - sum(a / (1 + r) ** ((d - t0).days / 365) for d, a in cash_flows)
    lo, hi = -0.9999, 100.0
    if npv(lo) * npv(hi) > 0:
        raise ValueError("IRR not bracketed")
    for _ in range(200):
        mid = (lo + hi) / 2
        if (npv(lo) > 0) == (npv(mid) > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
