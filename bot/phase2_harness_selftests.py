"""Offline synthetic harness diagnostics, NOT a scored Phase 3 backtest.

Never loads a market feed or transmits an Alpaca/Neon request. These simple
open-to-open one-share probes test the accounting/signaling primitives only,
not portfolio position limits, stop chronology, dividends or real fills.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean
from typing import Sequence


PRIMARY_SLIPPAGE_BPS = 30
LEGACY_RUN_ID = 38108710763


class FutureInformationLeak(ValueError):
    """A decision cites information unavailable before its entry."""
    

@dataclass(frozen=True)
class SyntheticDecision:
    entry_index: int
    exit_index: int
    last_known_index: int


@dataclass(frozen=True)
class SyntheticTrade:
    entry_index: int
    exit_index: int
    net_return: float
    net_return_bps: float


def synthetic_open_to_open(
    session_opens: Sequence[float],
    decisions: Sequence[SyntheticDecision],
    *,
    slippage_bps_per_side: int = PRIMARY_SLIPPAGE_BPS,
) -> tuple[SyntheticTrade, ...]:
    """Test entry AFTER last known index, adverse costs on BOTH sides.

    Session open observations are arbitrary synthetic test prices.
    A real market replay must use independent provenance and next-event
    timestamps, not merely this test's declared last_known_index.
    """
    if (type(slippage_bps_per_side) is not int
            or slippage_bps_per_side not in (10, 30, 60)):
        raise ValueError("Only preregistered slippage scenarios allowed")
    if len(session_opens) < 3 or any(
        type(p) not in (int, float) or not isfinite(p) or p <= 0
        for p in session_opens
    ):
        raise ValueError("Invalid synthetic session prices")
    slip = slippage_bps_per_side / 10000.0
    result = []
    previous_exit = -1
    for d in decisions:
        if not isinstance(d, SyntheticDecision) or any(
            type(v) is not int for v in (
                d.entry_index, d.exit_index, d.last_known_index)
        ):
            raise ValueError("Invalid synthetic decision")
        if (d.last_known_index < 0 or d.last_known_index >= d.entry_index):
            raise FutureInformationLeak(
                "future_information_used_for_entry")
        if (d.entry_index < 1 or d.entry_index >= len(session_opens)
                or d.exit_index <= d.entry_index
                or d.exit_index >= len(session_opens)):
            raise ValueError("Invalid synthetic entry/exit indices")
        if d.entry_index <= previous_exit:
            raise ValueError("Overlapping positions in synthetic probe")
        previous_exit = d.exit_index
        buy = session_opens[d.entry_index] * (1 + slip)
        sell = session_opens[d.exit_index] * (1 - slip)
        net = sell / buy - 1
        result.append(SyntheticTrade(
            d.entry_index, d.exit_index, net, net * 10000))
    return tuple(result)


def synthetic_mean_net_bps(trades: Sequence[SyntheticTrade]) -> float:
    if not trades:
        raise ValueError("No trades to assess")
    return mean(t.net_return_bps for t in trades)


def legacy_replay_prerequisites(
    *, original_market_bars_sha256: str | None,
    archived_market_bars_sha256: str | None,
    original_code_commit_sha: str | None,
) -> tuple[str, ...]:
    """Explicit replay blocker. A ledger/report alone is NOT replay data.

    The 2026-10-11 job emitted a report, not a repository-pinned copy
    of the IEX bar snapshot. Never pass a replay on output-only numbers.
    """
    import re

    blockers = []
    if (not isinstance(original_market_bars_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", original_market_bars_sha256)):
        blockers.append("original_market_data_hash_missing")
    if (not isinstance(archived_market_bars_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", archived_market_bars_sha256)):
        blockers.append("archived_market_data_hash_missing")
    if (not isinstance(original_code_commit_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", original_code_commit_sha)):
        blockers.append("original_code_commit_missing")
    if not blockers and original_market_bars_sha256 != archived_market_bars_sha256:
        blockers.append("market_data_hash_mismatch")
    return tuple(blockers)
