"""Synthetic independent refetch of broker nested OTO stop."""
from types import SimpleNamespace

from bot.paper_post_fill_confirmation import confirm_nested_filled_stops

APPROVAL = ("hds-F", "filled", "F", 2, 24, "parent-1")


def item(**kwargs):
    return SimpleNamespace(**kwargs)


def example(*, stop_status="new", stop_tif="gtc",
            parent_status="filled", shares=2):
    child = item(id="child-1", symbol="F", side="sell", type="stop",
                 qty="2", filled_qty="0", stop_price="24",
                 status=stop_status, time_in_force=stop_tif,
                 parent_order_id="parent-1", legs=[])
    parent = item(id="parent-1", client_order_id="hds-F", symbol="F",
                  side="buy", type="limit", order_class="oto",
                  status=parent_status, qty="2", filled_qty="2",
                  time_in_force="day", legs=[child])
    return parent, [item(symbol="F", qty=str(shares))]


class PaperFake:
    def __init__(self, parent, *, nested=None):
        self.parent = parent
        self.nested = nested or parent
        self.calls = []

    def get_order_by_client_id(self, client_id):
        self.calls.append(("by_client", client_id))
        return self.parent

    def get_order_by_id(self, order_id, *, filter):
        self.calls.append(("by_id", str(order_id), filter.nested))
        return self.nested

    def submit_order(self, **_kwargs):
        raise AssertionError("Never submit an order in a stop audit")

    def cancel_order_by_id(self, **_kwargs):
        raise AssertionError("Never cancel an order in a stop audit")


def test_actual_broker_get_is_required_for_current_coverage():
    p, positions = example()
    broker = PaperFake(p)
    assert confirm_nested_filled_stops(broker, positions, [APPROVAL]) == ()
    assert broker.calls == [("by_client", "hds-F"),
                            ("by_id", "parent-1", True)]


def test_unknown_approval_and_orphan_position_fail_closed():
    p, positions = example()
    assert confirm_nested_filled_stops(PaperFake(p), positions, []) == ("F",)
    assert confirm_nested_filled_stops(
        PaperFake(p), positions, [("hds-F", "unknown", "F", 2, 24, "parent-1")]) == ("F",)


def test_newly_canceled_stop_refutes_prior_list_coverage():
    p, positions = example()
    updated, _ = example(stop_status="canceled")
    broker = PaperFake(p, nested=updated)
    assert confirm_nested_filled_stops(broker, positions, [APPROVAL]) == ("F",)


def test_non_gtc_child_and_partial_fill_are_unverified():
    p, positions = example(stop_tif="day")
    assert confirm_nested_filled_stops(PaperFake(p), positions, [APPROVAL]) == ("F",)
    p, positions = example(parent_status="partially_filled")
    assert confirm_nested_filled_stops(PaperFake(p), positions, [APPROVAL]) == ("F",)


def test_client_order_id_and_parent_swap_are_rejected():
    p, positions = example()
    p.client_order_id = "surprise-order"
    assert confirm_nested_filled_stops(PaperFake(p), positions, [APPROVAL]) == ("F",)
    p, positions = example()
    p.id = "different-parent"
    assert confirm_nested_filled_stops(PaperFake(p), positions, [APPROVAL]) == ("F",)


def test_broker_errors_are_sanitized_and_never_mark_protection():
    p, positions = example()
    class Outage(PaperFake):
        def get_order_by_client_id(self, client_id):
            raise RuntimeError("SECRET_PASSWORD")
    assert confirm_nested_filled_stops(Outage(p), positions, [APPROVAL]) == ("F",)


def test_duplicate_durable_approval_never_passes():
    p, positions = example()
    assert confirm_nested_filled_stops(
        PaperFake(p), positions, [APPROVAL, APPROVAL]) == ("F",)
