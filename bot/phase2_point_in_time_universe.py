"""Historical point-in-time universe selection, OFFLINE and fail-closed.

This module accepts VENDOR-VERIFIED, COMPLETE AS-OF SNAPSHOTS of the
universe eligible at the preceding session. It cannot verify that a
historical vendor actually supplied the universe in real time; a
trusted point-in-time membership source and independent dataset review
are still required before any scored Phase 3 run.

Ticker strings are DISPLAY LABELS ONLY; joins and deduplication use
stable security_id (such as a genuine security master ID). This module
does not connect to QuantConnect, Alpaca, Neon or any trading service.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MAX_SNAPSHOT_ROWS = 200_000

# Do not accept "first eligible / last eligible" ranges assembled with
# later knowledge of a ticker's delisting. Every session must supply an
# independent, COMPLETE membership snapshot with its knowledge cutoff.
REQUIRED_FIELDS = frozenset({
    "snapshot_day", "known_at_utc", "snapshot_id", "security_id",
    "symbol", "eligible", "source_id", "complete",
})
IDENTITY = re.compile(r"^[a-zA-Z0-9_.:\-]{1,96}$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


class UniverseBlocked(ValueError):
    """A historical security cannot be selected with verified information."""


@dataclass(frozen=True)
class UniverseMember:
    security_id: str
    symbol: str
    eligible: bool


@dataclass(frozen=True)
class UniverseSelection:
    snapshot_day: str
    known_at_utc: str
    snapshot_id: str
    source_id: str
    decision_at_utc: str
    members: tuple[UniverseMember, ...]

    @property
    def eligible_security_ids(self) -> tuple[str, ...]:
        return tuple(x.security_id for x in self.members if x.eligible)

    @property
    def eligible_symbols(self) -> tuple[str, ...]:
        # Display only. Use security_id for any bar join or action.
        return tuple(x.symbol for x in self.members if x.eligible)


def _utc_instant(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise UniverseBlocked("Missing or malformed as-of knowledge timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise UniverseBlocked("Missing or malformed as-of knowledge timestamp") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise UniverseBlocked("Timezone-less historical knowledge timestamps prohibited")
    return dt.astimezone(timezone.utc)


def _day(value: str) -> date:
    if not isinstance(value, str) or not value:
        raise UniverseBlocked("Historical snapshot date missing")
    try:
        value_date = date.fromisoformat(value)
    except ValueError:
        raise UniverseBlocked("Historical snapshot day invalid") from None
    if value_date.isoformat() != value:
        raise UniverseBlocked("Historical snapshot day not canonical ISO date")
    return value_date


def _identity(value: str, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise UniverseBlocked("Historical " + field + " missing or ambiguous")
    return value


def parse_snapshot_csv(csv_text: str) -> tuple[dict[str, str], ...]:
    """Read snapshot rows without evaluating code or fetching external data."""
    if not isinstance(csv_text, str):
        raise UniverseBlocked("As-of snapshot CSV content is missing")
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""))
        if (reader.fieldnames is None
                or len(reader.fieldnames) != len(set(reader.fieldnames))
                or not REQUIRED_FIELDS.issubset(reader.fieldnames)):
            raise UniverseBlocked("As-of CSV requires complete point-in-time fields")
        rows = []
        for record in reader:
            if len(rows) >= MAX_SNAPSHOT_ROWS:
                raise UniverseBlocked("Historical membership file exceeds safety limit")
            if not isinstance(record, dict) or any(
                k is None or v is None for k, v in record.items()
            ):
                raise UniverseBlocked("Malformed historical membership CSV")
            rows.append(record)
    except (csv.Error, UnicodeError, ValueError) as exc:
        if isinstance(exc, UniverseBlocked):
            raise
        raise UniverseBlocked("Could not safely parse historical membership CSV") from None
    if not rows:
        raise UniverseBlocked("No historical universe snapshots supplied")
    return tuple(rows)


def read_snapshot_csv(path: Path) -> tuple[dict[str, str], ...]:
    """Local-only reader. File/source permissions validated by manifest gate."""
    try:
        # Large files should be placed in a private governed data store;
        # do not allow unbounded blobs into a scoring runner.
        if path.stat().st_size > 35_000_000:
            raise UniverseBlocked("Historical universe dataset exceeds safety limit")
        return parse_snapshot_csv(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        raise UniverseBlocked("As-of membership data file unavailable") from None


def select_historical_universe(
    records: Iterable[dict[str, str]],
    *,
    snapshot_day: str,
    decision_at_utc: datetime,
) -> UniverseSelection:
    """Select from ONE complete pre-decision snapshot; never infer membership.

    Caller must explicitly provide the prior complete *trading session*
    day, obtained from a separately verified NYSE calendar. Never select
    today's after-close constituents for today's market-open decision.

    Unmatched session, future knowledge timestamp, conflicting IDs,
    changing ticker within a snapshot, missing source or an incomplete
    universe aborts. No fallbacks to a current live ticker list.
    """
    day = _day(snapshot_day)
    if not isinstance(decision_at_utc, datetime) or (
        decision_at_utc.tzinfo is None
        or decision_at_utc.utcoffset() is None
    ):
        raise UniverseBlocked("Decision time must be timezone-aware")
    cutoff = decision_at_utc.astimezone(timezone.utc)
    decision_day = cutoff.astimezone(NY).date()
    if day >= decision_day:
        raise UniverseBlocked("Same-day or future universe snapshot cannot select an entry")
    # A stale snapshot must not be silently reused after extended
    # outages. The exact previous market session still comes from a
    # separately validated NYSE calendar; this is only a max-age gate.
    if (decision_day - day).days > 4:
        raise UniverseBlocked("Historical universe snapshot is too stale")

    filtered = []
    total = 0
    for row in records:
        total += 1
        if total > MAX_SNAPSHOT_ROWS:
            raise UniverseBlocked("Historical membership file exceeds safety limit")
        if not isinstance(row, dict):
            raise UniverseBlocked("Malformed historical membership row")
        record_day = _day(row.get("snapshot_day"))
        # Validate *all* rows' dates even when not used, but only
        # read the requested prior-session snapshot as input.
        if record_day == day:
            filtered.append(row)
    if not filtered:
        raise UniverseBlocked("Expected complete historical session snapshot absent")

    signature = None
    by_id: dict[str, UniverseMember] = {}
    by_symbol: dict[str, str] = {}
    for row in filtered:
        if row.get("complete") != "true":
            raise UniverseBlocked("Historical universe was not a complete snapshot")
        known_at = _utc_instant(row.get("known_at_utc"))
        if known_at > cutoff:
            raise UniverseBlocked("Historical universe includes knowledge from the future")
        # The source must attest to the membership as known by this
        # timestamp. A claim in CSV alone is not source verification.
        sid = _identity(row.get("security_id"), "stable security ID")
        snap_id = _identity(row.get("snapshot_id"), "snapshot ID")
        src = _identity(row.get("source_id"), "data source ID")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise UniverseBlocked("Invalid historical display ticker")
        eligible_text = row.get("eligible")
        if eligible_text not in {"true", "false"}:
            raise UniverseBlocked("Unknown eligibility (not strictly true/false)")
        row_signature = (snap_id, src, known_at)
        if signature is None:
            signature = row_signature
        elif signature != row_signature:
            raise UniverseBlocked("Conflicting or multiple snapshots for historical session")
        if sid in by_id:
            raise UniverseBlocked("Duplicate historical security ID")
        if symbol in by_symbol:
            raise UniverseBlocked("Ambiguous historical ticker maps to multiple IDs")
        by_id[sid] = UniverseMember(sid, symbol, eligible_text == "true")
        by_symbol[symbol] = sid

    if signature is None:
        raise UniverseBlocked("No complete snapshot accepted")
    snap_id, src, available_at = signature
    members = tuple(sorted(by_id.values(), key=lambda x: x.security_id))
    return UniverseSelection(
        snapshot_day=day.isoformat(),
        known_at_utc=available_at.isoformat(),
        snapshot_id=snap_id,
        source_id=src,
        decision_at_utc=cutoff.isoformat(),
        members=members,
    )
