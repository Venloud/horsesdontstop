"""Offline broker-versus-journal reconciliation tests; no API credentials."""
from types import SimpleNamespace

from bot.paper_reconcile import reconcile_observation


def order(order_id, symbol="F", *, side="buy", kind="market",
          qty=1, status="new", cid=None, legs=(), parent=None):
    return SimpleNamespace(
        id=order_id, symbol=symbol, side=side,
        type=kind, qty=str(qty), status=status,
        client_order_id=cid, legs=list(legs), parent_order_id=parent,
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
    assert "position_missing_live_protective_stop" in report.blockers
    assert "F" in report.uncovered_symbols
    assert not report.consistent


def test_matching_live_stop_diagnoses_coverage_but_blocks_new_entries():
    stop = order("stop-leg", "F", side="sell", kind="stop", qty=2,
                 status="new", parent="parent-1")
    parent = order("parent-1", "F", side="buy", kind="market", qty=2,
                   status="filled", cid="hds-c", legs=(stop,))
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
    assert "position_missing_live_protective_stop" in report.blockers


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
