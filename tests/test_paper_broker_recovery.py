"""Mocked PAPER broker client-ID and nested OTO stop lifecycle."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from bot.paper_broker_recovery import inspect_nested_oto, recover_by_client_id
from bot.paper_safety import TradeBlocked, plan_paper_entry


PLAN = plan_paper_entry("F", date(2026, 10, 12), 25, 500, 500, .01)


def parent(*, status="filled", fill=None, qty=None, legs=None,
           cid=None, symbol="F", pid="safe-paper-parent",
           kind="limit", klass="oto"):
    return SimpleNamespace(
        id=pid,
        client_order_id=cid or PLAN.client_order_id, symbol=symbol,
        order_class=klass, side="buy", type=kind, time_in_force="day",
        status=status, qty=str(qty if qty is not None else PLAN.whole_shares),
        filled_qty=str(fill if fill is not None else PLAN.whole_shares),
        limit_price=str(PLAN.estimated_entry), legs=legs,
    )


def stop(*, status="new", qty=None, filled=0, trigger=None, side="sell",
         kind="stop", tif="gtc"):
    return SimpleNamespace(
        id="safe-paper-stop", symbol=PLAN.symbol, side=side, type=kind,
        qty=str(qty if qty is not None else PLAN.whole_shares),
        filled_qty=str(filled), status=status, time_in_force=tif,
        stop_price=str(trigger if trigger is not None else PLAN.protective_stop),
    )


def test_fully_filled_parent_has_verified_active_stop():
    p = parent(legs=[stop()])
    result = inspect_nested_oto(PLAN, p, position_qty=PLAN.whole_shares)
    assert result.blockers == ()
    assert result.has_verified_protective_stop is True
    assert result.paper_exposure_shares == PLAN.whole_shares
    assert result.broker_writes == 0


@pytest.mark.parametrize("stop_status", [
    "held", "canceled", "rejected", "expired", "pending_cancel", "filled"
])
def test_stop_not_live_flags_unprotected_position(stop_status):
    p = parent(legs=[stop(status=stop_status)])
    result = inspect_nested_oto(PLAN, p, position_qty=PLAN.whole_shares)
    assert not result.has_verified_protective_stop
    assert "unprotected_open_paper_position" in result.blockers


def test_no_legs_on_filled_parent_flags_emergency_attention():
    report = inspect_nested_oto(
        PLAN, parent(legs=None), position_qty=PLAN.whole_shares)
    assert "unprotected_open_paper_position" in report.blockers


def test_partial_fill_cannot_be_assumed_safe_even_if_child_shown():
    p = parent(status="partially_filled", fill=1, legs=[stop(qty=1)])
    result = inspect_nested_oto(PLAN, p, position_qty=1)
    assert "partially_filled_entry_may_lack_stop" in result.blockers


def test_pending_entry_with_zero_fills_is_not_an_open_position():
    result = inspect_nested_oto(
        PLAN, parent(status="new", fill=0, legs=None), position_qty=0)
    assert result.status == "new"
    assert result.blockers == ()
    assert not result.has_verified_protective_stop


def test_close_by_filled_stop_when_paper_position_is_flat():
    result = inspect_nested_oto(
        PLAN, parent(legs=[stop(status="filled",
                                 filled=PLAN.whole_shares)]),
        position_qty=0)
    assert result.blockers == ()
    assert not result.has_verified_protective_stop


def test_filled_entry_with_flat_position_but_no_stop_exit_flags_review():
    result = inspect_nested_oto(
        PLAN, parent(legs=[stop(status="canceled")]), position_qty=0)
    assert "filled_entry_flat_without_verified_stop_exit" in result.blockers


@pytest.mark.parametrize("leg", [
    stop(qty=1), stop(trigger=PLAN.protective_stop - .1),
    stop(side="buy"), stop(kind="stop_limit"), stop(status="held"),
])
def test_inadequate_stop_does_not_protect_entire_position(leg):
    result = inspect_nested_oto(
        PLAN, parent(legs=[leg]), position_qty=PLAN.whole_shares)
    assert not result.has_verified_protective_stop
    assert "unprotected_open_paper_position" in result.blockers


def test_wrong_broker_parent_identity_and_excess_position_block():
    report = inspect_nested_oto(
        PLAN, parent(cid="different", kind="market",
                     klass="simple", symbol="AAPL"),
        position_qty=PLAN.whole_shares + 1)
    assert "broker_parent_identity_or_type_mismatch" in report.blockers
    assert "broker_position_exceeds_authorized_shares" in report.blockers


def test_unknown_parent_state_is_not_accepted():
    report = inspect_nested_oto(
        PLAN, parent(status="pending_replace", legs=[stop()]),
        position_qty=PLAN.whole_shares)
    assert "unknown_broker_parent_state" in report.blockers


class FakePaperTrading:
    def __init__(self, order, *, nested=None, failure=None):
        self.order = order
        self.nested = nested or order
        self.failure = failure
        self.calls = []

    def get_order_by_client_id(self, cid):
        self.calls.append(("get_order_by_client_id", cid))
        if self.failure:
            raise self.failure
        return self.order

    def get_order_by_id(self, pid, *, filter):
        self.calls.append(("get_order_by_id", pid, filter.nested))
        return self.nested

    def submit_order(self, *_a, **_kw):
        raise AssertionError("Recovery MUST NOT submit orders")

    def cancel_orders(self, *_a, **_kw):
        raise AssertionError("Recovery MUST NOT cancel orders")


def test_recovery_queries_exact_client_id_and_nested_order():
    trading = FakePaperTrading(parent(legs=[stop()]))
    audit = recover_by_client_id(
        trading, PLAN, position_qty=PLAN.whole_shares)
    assert not audit.blockers
    assert trading.calls == [
        ("get_order_by_client_id", PLAN.client_order_id),
        ("get_order_by_id", "safe-paper-parent", True),
    ]


def test_404_is_unresolved_not_permission_to_resubmit():
    fake_http = SimpleNamespace(response=SimpleNamespace(status_code=404))
    missing = APIError('{"message":"not found"}', http_error=fake_http)
    trading = FakePaperTrading(None, failure=missing)
    audit = recover_by_client_id(trading, PLAN, position_qty=0)
    assert audit.status == "not_found_unresolved"
    assert "broker_client_order_not_found_not_safe_to_retry" in audit.blockers
    assert trading.calls == [
        ("get_order_by_client_id", PLAN.client_order_id)]


def test_500_error_is_uncertain_and_no_secret_string_in_error():
    fake_http = SimpleNamespace(response=SimpleNamespace(status_code=500))
    err = APIError('{"message":"postgresql://sensitive-private-token"}',
                   http_error=fake_http)
    trading = FakePaperTrading(None, failure=err)
    with pytest.raises(TradeBlocked) as captured:
        recover_by_client_id(trading, PLAN, position_qty=0)
    assert "sensitive-private" not in str(captured.value)


def test_nested_parent_id_swap_fails_closed():
    trading = FakePaperTrading(
        parent(), nested=parent(pid="changed-while-fetching"))
    with pytest.raises(TradeBlocked, match="ID changed"):
        recover_by_client_id(trading, PLAN, position_qty=0)


def test_live_endpoint_refused_before_any_broker_call():
    trading = FakePaperTrading(parent())
    with pytest.raises(TradeBlocked, match="PAPER"):
        recover_by_client_id(
            trading, PLAN, position_qty=0,
            paper_endpoint="https://api.alpaca.markets")
    assert trading.calls == []


def test_day_only_stop_never_claimed_overnight_safe():
    result = inspect_nested_oto(
        PLAN, parent(legs=[stop(tif="day")]),
        position_qty=PLAN.whole_shares)
    assert "overnight_protective_stop_gtc_not_verified" in result.blockers
    assert result.has_verified_protective_stop is False


def test_missing_child_time_in_force_is_unverified():
    child = stop()
    delattr(child, "time_in_force")
    result = inspect_nested_oto(
        PLAN, parent(legs=[child]),
        position_qty=PLAN.whole_shares)
    assert "overnight_protective_stop_gtc_not_verified" in result.blockers
    assert result.has_verified_protective_stop is False


def test_valid_stop_cannot_override_mismatched_broker_parent():
    p = parent(cid="unrelated-id", legs=[stop()])
    outcome = inspect_nested_oto(PLAN, p, position_qty=PLAN.whole_shares)
    assert "broker_parent_identity_or_type_mismatch" in outcome.blockers
    assert outcome.has_verified_protective_stop is False


def test_parent_partially_filled_does_not_claim_protection_when_position_exceeds_fills():
    p = parent(status="partially_filled", fill=1, legs=[stop()])
    outcome = inspect_nested_oto(PLAN, p, position_qty=PLAN.whole_shares)
    assert "broker_position_exceeds_parent_filled_shares" in outcome.blockers
    assert outcome.has_verified_protective_stop is False


def test_nested_stop_with_wrong_reported_parent_id_is_rejected():
    child = stop()
    child.parent_order_id = "other-oto-parent"
    outcome = inspect_nested_oto(
        PLAN, parent(legs=[child]), position_qty=PLAN.whole_shares)
    assert "unprotected_open_paper_position" in outcome.blockers
    assert outcome.has_verified_protective_stop is False


def test_stop_larger_than_durable_intent_does_not_prove_protection():
    child = stop(qty=PLAN.whole_shares + 1)
    outcome = inspect_nested_oto(
        PLAN, parent(legs=[child]), position_qty=PLAN.whole_shares)
    assert "unprotected_open_paper_position" in outcome.blockers
    assert outcome.has_verified_protective_stop is False


def test_nested_child_without_broker_order_id_is_not_proof():
    child = stop()
    child.id = None
    outcome = inspect_nested_oto(
        PLAN, parent(legs=[child]), position_qty=PLAN.whole_shares)
    assert "unprotected_open_paper_position" in outcome.blockers
    assert outcome.has_verified_protective_stop is False


def test_second_active_sell_child_disqualifies_otherwise_valid_stop():
    good = stop()
    duplicate = stop(trigger=PLAN.protective_stop - 2)
    duplicate.id = "second-sell-child"
    audit = inspect_nested_oto(
        PLAN, parent(legs=[good, duplicate]),
        position_qty=PLAN.whole_shares)
    assert "unexpected_active_sell_exit_count" in audit.blockers
    assert audit.has_verified_protective_stop is False


def test_different_active_sell_type_also_disqualifies_oto_stop():
    good = stop()
    competing = stop(kind="stop_limit")
    competing.id = "competing-exit"
    audit = inspect_nested_oto(
        PLAN, parent(legs=[good, competing]),
        position_qty=PLAN.whole_shares)
    assert "unexpected_active_sell_exit_count" in audit.blockers
    assert audit.has_verified_protective_stop is False


def test_terminal_sell_child_does_not_count_as_active_competitor():
    good = stop()
    closed = stop(status="canceled")
    closed.id = "old-canceled-child"
    audit = inspect_nested_oto(
        PLAN, parent(legs=[good, closed]),
        position_qty=PLAN.whole_shares)
    assert "unexpected_active_sell_exit_count" not in audit.blockers
    assert audit.has_verified_protective_stop is True
