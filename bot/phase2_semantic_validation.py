"""Offline semantic checks for hashes-pinned Phase 2 research CSV artifacts.

A CSV with valid columns and SHA-256 is not automatically valid market data.
Checks below reject common impossible quotes, duplicate sessions, unsorted
observations, malformed point-in-time snapshots, and unaudited cash amounts.
They do NOT certify source licensing, PIT membership truth or corporate actions.

NO broker API, Neon connection, scored backtest, or order submission.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _day(raw: str) -> date:
    day = date.fromisoformat(raw)
    if raw != day.isoformat():
        raise ValueError("Noncanonical date")
    return day


def _number(raw: str) -> Decimal:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ValueError("Missing or whitespace padded numeric field")
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid decimal data") from exc
    if not value.is_finite():
        raise ValueError("Nonfinite decimal data")
    return value


def _utc_instant(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise ValueError("Missing as-of timestamp")
    instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("As-of timestamp must be timezone-aware")
    return instant.astimezone(timezone.utc)


def _rows(path: Path, required: set[str]):
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source, strict=True)
        if (not reader.fieldnames
                or len(reader.fieldnames) != len(set(reader.fieldnames))
                or not required.issubset(reader.fieldnames)):
            raise ValueError("Invalid CSV field names")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Missing or excess CSV field")
            yield row


def _symbol(raw: str) -> str:
    # Stable identifier, not proof that the ticker belonged to the market.
    import re
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", raw):
        raise ValueError("Invalid research ticker")
    return raw


def _bars(path: Path) -> set[date]:
    recent: dict[str, date] = {}
    spy_days: set[date] = set()
    for row in _rows(path, {"symbol", "day", "open", "high", "low", "close", "volume"}):
        symbol = _symbol(row["symbol"])
        day = _day(row["day"])
        if symbol in recent and day <= recent[symbol]:
            raise ValueError("Duplicate or unordered symbol session")
        recent[symbol] = day
        o, h, l, c = (_number(row[n]) for n in ("open", "high", "low", "close"))
        v = _number(row["volume"])
        if (min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c)
                or h < l or v < 0 or v != v.to_integral_value()):
            raise ValueError("Impossible stock OHLCV")
        if symbol == "SPY":
            spy_days.add(day)
    if not recent or not spy_days:
        raise ValueError("Missing SPY benchmark price series")
    return spy_days


def _universe(path: Path) -> None:
    previous: dict[str, date] = {}
    seen: set[tuple[str, str]] = set()
    observed = 0
    for row in _rows(path, {
        "snapshot_day", "known_at_utc", "snapshot_id", "security_id",
        "symbol", "eligible", "source_id", "complete",
    }):
        snapshot_day = _day(row["snapshot_day"])
        known_at = _utc_instant(row["known_at_utc"])
        snapshot = row["snapshot_id"]
        security = row["security_id"]
        if not snapshot or not security or not row["source_id"]:
            raise ValueError("Missing point-in-time source identity")
        _symbol(row["symbol"])
        if row["eligible"] not in {"true", "false"}:
            raise ValueError("Ambiguous membership eligibility")
        if row["complete"] != "true":
            raise ValueError("Incomplete point-in-time snapshot")
        key = (snapshot, security)
        if key in seen:
            raise ValueError("Duplicate security in membership snapshot")
        seen.add(key)
        # A future-dated eligibility row cannot pretend to have been
        # known before the snapshot date. No calendar/timezone guarantees
        # about tradability at the same day's opening are made here.
        if known_at.date() < snapshot_day:
            raise ValueError("Snapshot known timestamp precedes snapshot date")
        if snapshot in previous and previous[snapshot] != snapshot_day:
            raise ValueError("Snapshot ID reused for different dates")
        previous[snapshot] = snapshot_day
        observed += 1
    if observed == 0:
        raise ValueError("Empty point-in-time universe")


def _benchmark(path: Path) -> set[date]:
    prev: date | None = None
    days: set[date] = set()
    for row in _rows(path, {"day", "total_return_index"}):
        day = _day(row["day"])
        value = _number(row["total_return_index"])
        if value <= 0 or (prev is not None and day <= prev):
            raise ValueError("Invalid or duplicate total-return session")
        prev = day
        days.add(day)
    if not days:
        raise ValueError("Empty total-return benchmark")
    return days


def _contributions(path: Path) -> None:
    # Header-only is VALID when there were NO actual owner transfers.
    # This module does not claim those transfers were independently audited.
    prev: date | None = None
    for row in _rows(path, {"day", "amount_usd"}):
        day = _day(row["day"])
        amount = _number(row["amount_usd"])
        if prev is not None and day <= prev:
            raise ValueError("Duplicate or unordered owner contribution date")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Owner contribution must be an exact cent amount")
        prev = day


def validate_semantic_artifacts(paths: dict[str, Path]) -> list[str]:
    """Fail only with stable non-sensitive codes, not CSV or credential data.

    Called AFTER SHA-256 and header checks. Independent human verification
    of historical source truth, cash transfers, survival bias and embargo
    is still required before scored Phase 3 research.
    """
    problems: list[str] = []
    checked = {}
    validators = {
        "bars": _bars,
        "universe_membership": _universe,
        "benchmark_total_return": _benchmark,
        "contributions": _contributions,
    }
    for name, validator in validators.items():
        path = paths.get(name)
        if path is None:
            continue
        try:
            checked[name] = validator(path)
        except (ValueError, TypeError, OSError, UnicodeError, csv.Error,
                ArithmeticError):
            problems.append("asset_semantic_invalid:" + name)
    if "bars" in checked and "benchmark_total_return" in checked:
        if not checked["bars"].intersection(checked["benchmark_total_return"]):
            problems.append("spy_benchmark_dates_do_not_overlap")
    return problems
