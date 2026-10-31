from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.paper_pilot_approval import render_owner_approval_sql
from bot.paper_pilot_approval_cli import approval_sql_from_document
from bot.live_quotes import preview_live_paper_entry, build_live_protected_oto_request
from bot.paper_supervised_execution import (
    PreparedPaperOrder, _plan_from_quoted_preview, _request_sha256,
)
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
    quote = SimpleNamespace(
        bid_price=1.50, ask_price=1.51, bid_size=50, ask_size=50,
        timestamp=NOW,
    )
    preview = preview_live_paper_entry(
        symbol="F", quote=quote, equity=500, available_cash=500,
        now=NOW, market_open=True)
    plan = _plan_from_quoted_preview(preview)
    result = Prepared(plan, preview,
                      _request_sha256(build_live_protected_oto_request(preview)),
                      "b" * 64)
    for name, value in changes.items():
        if name.startswith("plan_"):
            result.plan = replace(plan, **{name[5:]: value})
        elif name.startswith("preview_"):
            result.preview = replace(preview, **{name[8:]: value})
        else:
            setattr(result, name, value)
    return result


def test_renders_exact_owner_only_short_lived_approval_call():
    sql = render_owner_approval_sql(prepared(), now=NOW)
    p = prepared().preview
    assert "paper_approve_bounded_pilot" in sql
    assert f"'{p.client_order_id}', 'F', {p.shares}" in sql
    assert f"{p.buy_limit_usd:.6f}, {p.stop_usd:.6f}" in sql
    approved = prepared()
    assert f"'{approved.request_sha256}', '{approved.account_id_sha256}'" in sql
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
    {"request_sha256": "c" * 64},
])
def test_rejects_malformed_or_changed_approval_terms(changes):
    with pytest.raises(TradeBlocked):
        render_owner_approval_sql(prepared(**changes), now=NOW)


def test_requires_timezone_aware_approval_artifact_clock():
    with pytest.raises(TradeBlocked, match="timezone-aware"):
        render_owner_approval_sql(prepared(), now=datetime(2026, 11, 2, 14, 35))


def test_cli_approval_handoff_binds_summary_to_full_quote_and_sdk_request():
    p = prepared()
    proposal = PreparedPaperOrder(
        plan=p.plan, preview=p.preview, request_sha256=p.request_sha256,
        account_id_sha256=p.account_id_sha256,
    ).to_dict()
    sql = approval_sql_from_document(proposal)
    assert p.plan.client_order_id in sql
    assert "paper_approve_bounded_pilot" in sql
    assert f"Exact order: BUY {p.plan.whole_shares} F" in sql

    proposal["side"] = "sell"
    with pytest.raises(ValueError):
        approval_sql_from_document(proposal)
    proposal["side"] = "buy"

    proposal["quote_preview"]["buy_limit_usd"] += .01
    with pytest.raises((ValueError, TradeBlocked)):
        approval_sql_from_document(proposal)


def test_refuses_silent_numeric_rounding_and_inconsistent_cost_summary():
    base = prepared()
    price = base.preview.buy_limit_usd + .0000004
    precise_preview = replace(
        base.preview, buy_limit_usd=price,
        max_limit_purchase_usd=price * base.preview.shares,
    )
    precise_plan = _plan_from_quoted_preview(precise_preview)
    precise = Prepared(
        precise_plan, precise_preview,
        _request_sha256(build_live_protected_oto_request(precise_preview)),
        base.account_id_sha256,
    )
    with pytest.raises(TradeBlocked, match="bounded pilot limits"):
        render_owner_approval_sql(precise, now=NOW)

    inconsistent_preview = replace(
        base.preview,
        max_limit_purchase_usd=base.preview.max_limit_purchase_usd + .01,
    )
    inconsistent = Prepared(
        _plan_from_quoted_preview(inconsistent_preview), inconsistent_preview,
        base.request_sha256, base.account_id_sha256,
    )
    with pytest.raises(TradeBlocked, match="bounded pilot limits"):
        render_owner_approval_sql(inconsistent, now=NOW)
