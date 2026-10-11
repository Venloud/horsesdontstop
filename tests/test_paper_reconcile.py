"""Offline broker-versus-journal reconciliation tests; no API credentials."""
from types import SimpleNamespace

import pytest

from bot.paper_reconcile import reconcile_observation


def order(order_id, symbol="F", *, side="buy", kind="market",
          qty=1, status="new", cid=None, legs=(), parent=None,
          tif="day", stop_price=None, filled=0, klass="simple"):
    return SimpleNamespace(
        id=order_id, symbol=symbol, side=side,
        type=kind, qty=str(qty), filled_qty=str(filled),
        status=status, time_in_force=tif, stop_price=stop_price,
        order_class=klass, client_order_id=cid,
        legs=list(legs), parent_order_id=parent,
    )


def position(symbol="F", qty=1):
    return SimpleNamespace(symbol=symbol, qty=str(qty))


def test_empty_broker_and_empty_neon_journal_reconcile():
    report = reconcile_observation([], [], [], order_fetch_complete=True)
    assert report.consistent
    assert report.blockers == ()
    assert report.open_broker_orders == 0


def test_truncated_broker_list_fails_closed():
    report = reconcile_observation([], [], [], order_fetch_complete=False)
    assert not report.consistent
    assert "broker_order_history_incomplete" in report.blockers


def test_unprotected_position_detected_even_with_open_buy():
    report = reconcile_observation(
        [position("F", 2)],
        [order("one", "F", qty=2, cid="hds-a")],
        [("hds-a", "submitted", "F", 2)],
        order_fetch_complete=True)
    assert "position_missing_verified_gtc_oto_stop" in report.blockers
    assert "F" in report.uncovered_symbols
    assert not report.consistent


def test_matching_live_stop_diagnoses_coverage_but_blocks_new_entries():
    stop = order("stop-leg", "F", side="sell", kind="stop", qty=2,
                 status="new", parent="parent-1", tif="gtc",
                 stop_price=24)
    parent = order("parent-1", "F", side="buy", kind="limit", qty=2,
                   status="filled", cid="hds-c", legs=(stop,),
                   klass="oto")
    report = reconcile_observation(
        [position("F", 2)], [parent], [("hds-c", "filled", "F", 2)],
        order_fetch_complete=True)
    assert not report.uncovered_symbols
    assert "open_broker_positions" in report.blockers
    assert "open_broker_orders" in report.blockers
    assert not report.consistent


def test_inadequate_partial_stop_is_not_coverage():
    stop = order("stop-leg", "F", side="sell", kind="stop_limit", qty=1,
                 parent="parent-1")
    parent = order("parent-1", "F", qty=2, status="filled",
                   cid="hds-c", legs=[stop])
    report = reconcile_observation(
        [position("F", 2)], [parent], [],
        order_fetch_complete=True)
    assert "position_missing_verified_gtc_oto_stop" in report.blockers


@pytest.mark.parametrize("stop_kwargs", [
    {"tif": "day"},
    {"tif": None},
    {"kind": "stop_limit"},
    {"status": "held"},
    {"status": "pending_cancel"},
    {"stop_price": None},
    {"stop_price": 0},
    {"qty": 1},
    {"filled": 2},
    {"parent": "unknown-parent"},
])
def test_stop_that_is_not_active_gtc_linked_and_complete_blocks(stop_kwargs):
    opts = dict(side="sell", kind="stop", qty=2, status="new",
                parent="parent-1", tif="gtc", stop_price=24, filled=0)
    opts.update(stop_kwargs)
    stop = order("stop-leg", "F", **opts)
    parent = order("parent-1", "F", side="buy", kind="limit", qty=2,
                   status="filled", cid="hds-c", legs=(stop,),
                   klass="oto")
    report = reconcile_observation(
        [position("F", 2)], [parent],
        [("hds-c", "filled", "F", 2)],
        order_fetch_complete=True)
    assert "position_missing_verified_gtc_oto_stop" in report.blockers
    assert "F" in report.uncovered_symbols
    assert not report.consistent


def test_unrelated_manual_sell_stop_cannot_verify_paper_protection():
    stop = order("stranger-stop", "F", side="sell", kind="stop",
                 tif="gtc", stop_price=24, qty=2, parent=None)
    report = reconcile_observation(
        [position("F", 2)], [stop],
        [], order_fetch_complete=True)
    assert "position_missing_verified_gtc_oto_stop" in report.blockers
    assert "F" in report.uncovered_symbols


def test_parent_wrong_order_class_does_not_verify_oto_child():
    stop = order("stop-leg", "F", side="sell", kind="stop",
                 parent="parent-1", tif="gtc", qty=2, stop_price=24)
    parent = order("parent-1", "F", side="buy", kind="market",
                   klass="simple", qty=2, status="filled",
                   cid="hds-c", legs=(stop,))
    report = reconcile_observation(
        [position("F", 2)], [parent],
        [("hds-c", "filled", "F", 2)],
        order_fetch_complete=True)
    assert "position_missing_verified_gtc_oto_stop" in report.blockers


def test_unknown_broker_order_blocks():
    report = reconcile_observation(
        [], [order("manual", cid="someone-else")], [],
        order_fetch_complete=True)
    assert "open_order_not_in_durable_journal" in report.blockers


def test_reserved_or_unknown_intents_never_auto_clear():
    for status in ("reserved", "unknown", "submitted", "accepted"):
        report = reconcile_observation(
            [], [], [("hds-F", status, "F", 1)],
            order_fetch_complete=True)
        assert not report.consistent
        assert report.unresolved_journal_intents == 1
        assert "unresolved_order_intent_requires_broker_lookup" in report.blockers


def test_duplicate_intents_or_malformed_order_tree_fail_closed():
    report = reconcile_observation(
        [], [], [("hds-F", "filled", "F", 1),
                 ("hds-F", "filled", "F", 1)],
        order_fetch_complete=True)
    assert "invalid_or_duplicate_journal_id" in report.blockers
    bad = order("good")
    bad.id = None
    report = reconcile_observation(
        [], [bad], [], order_fetch_complete=True)
    assert "malformed_broker_order_tree" in report.blockers


def test_broker_order_with_terminal_state_not_counted_open():
    report = reconcile_observation(
        [], [order("closed", status="canceled")], [],
        order_fetch_complete=True)
    assert report.consistent
    assert report.open_broker_orders == 0
