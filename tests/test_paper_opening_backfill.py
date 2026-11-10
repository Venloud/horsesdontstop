from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from bot.paper_opening_backfill import choose_opening_equity

DAY = date(2026, 11, 10)
OPEN = int(datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc).timestamp())


def test_uses_alpacas_exact_0930_bar():
    at, eq = choose_opening_equity([OPEN - 60, OPEN, OPEN + 60], [1, 500.0, 2],
                                   session_day=DAY, last_equity="500", fills_today=0)
    assert at == datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc) and eq == Decimal("500")


def test_missing_0930_bar_is_refused():
    with pytest.raises(ValueError, match="missing"):
        choose_opening_equity([OPEN + 60], [500], session_day=DAY, last_equity="500", fills_today=0)


def test_flat_day_bar_must_match_previous_close():
    with pytest.raises(ValueError, match="differs"):
        choose_opening_equity([OPEN], [650], session_day=DAY, last_equity="500", fills_today=0)


def test_overnight_position_may_gap_when_fills_exist():
    _, eq = choose_opening_equity([OPEN], [480], session_day=DAY, last_equity="500", fills_today=1)
    assert eq == Decimal("480")


def test_non_positive_equity_refused():
    with pytest.raises(ValueError, match="positive"):
        choose_opening_equity([OPEN], [0], session_day=DAY, last_equity="0", fills_today=0)
