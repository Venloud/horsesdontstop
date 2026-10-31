from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.paper_pilot_approval import render_owner_approval_sql
from bot.paper_safety import TradeBlocked


NOW = datetime(2026, 11, 2, 14, 35, tzinfo=timezone.utc)


@dataclass
class Prepared:
    plan: object
    preview: object
    request_sha256: str = "a" * 64
    account_id_sha256: str = "b" * 64
    fee_reserve_usd: float = .50


def prepared(**changes):
    plan = SimpleNamespace(
        client_order_id="hds-20261102-F-012345abcdef", symbol="F",
        whole_shares=3, time_in_force="gtc", order_class="oto",
    )
    preview = SimpleNamespace(
        client_order_id=plan.client_order_id, symbol="F",
        buy_limit_usd=1.51, stop_usd=1.46,
        max_limit_purchase_usd=4.53,
    )
    result = Prepared(plan, preview)
    for name, value in changes.items():
        if name.startswith("plan_"):
            setattr(plan, name[5:], value)
        elif name.startswith("preview_"):
            setattr(preview, name[8:], value)
        else:
            setattr(result, name, value)
    return result


def test_renders_exact_owner_only_short_lived_approval_call():
    sql = render_owner_approval_sql(prepared(), now=NOW)
    assert "paper_approve_bounded_pilot" in sql
    assert "'hds-20261102-F-012345abcdef', 'F', 3" in sql
    assert "1.510000, 1.460000" in sql
    assert "'" + "a" * 64 + "', '" + "b" * 64 + "'" in sql
    assert "2026-11-02T14:37:00.000000+00:00" in sql
    assert "OWNER ACTION" in sql
    assert "does not enable dispatch" in sql


@pytest.mark.parametrize("changes", [
    {"plan_client_order_id": "x'); DROP TABLE paper_intents; --"},
    {"plan_symbol": "F'; SELECT true; --"},
    {"preview_max_limit_purchase_usd": 5.01},
    {"preview_buy_limit_usd": 2.00},
    {"fee_reserve_usd": 0.49},
    {"request_sha256": "x" * 64},
    {"account_id_sha256": "b" * 63},
    {"plan_time_in_force": "day"},
    {"preview_symbol": "A"},
])
def test_rejects_malformed_or_changed_approval_terms(changes):
    with pytest.raises(TradeBlocked):
        render_owner_approval_sql(prepared(**changes), now=NOW)


def test_requires_timezone_aware_approval_artifact_clock():
    with pytest.raises(TradeBlocked, match="timezone-aware"):
        render_owner_approval_sql(prepared(), now=datetime(2026, 11, 2, 14, 35))
