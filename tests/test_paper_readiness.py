"""End-to-end preview tests run without Alpaca secrets or network access."""
from datetime import datetime, timezone
from types import SimpleNamespace

from bot.paper_readiness_cli import preview


class PaperReadOnlyFake:
    def __init__(self):
        self.calls = []

    def get_account(self):
        self.calls.append("get_account")
        return SimpleNamespace(equity="500", cash="500", trading_blocked=False)

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(is_open=True)

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return []

    def get_orders(self, *, filter):
        self.calls.append("get_orders")
        return []

    def submit_order(self, **kwargs):
        raise AssertionError("NO orders may be submitted in a preview")

    def cancel_orders(self, **kwargs):
        raise AssertionError("NO orders may be canceled in a preview")


def test_preview_still_blocked_when_broker_is_healthy(monkeypatch):
    fake = PaperReadOnlyFake()
    monkeypatch.setattr("bot.paper_readiness_cli.research_candidates",
                        lambda *_args, **_kwargs: [
                            {"symbol": "F", "reference_close": 15.0,
                             "prior_completed_bar_date": "2026-10-09",
                             "momentum_score": 0.3}])
    now = datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
    report = preview(fake, object(), ["F"], now=now)
    assert fake.calls == [
        "get_account", "get_clock", "get_all_positions", "get_orders"
    ]
    assert report["account_equity"] == 500
    assert report["can_submit_orders"] is False
    assert report["signals_observed"] == 1
    assert report["illustrative_whole_share_plans"][0]["plan"] is not None
    assert report["illustrative_whole_share_plans"][1]["plan"] is not None
    assert "kill_switch_active" in report["safety_blockers"]
    assert "durable_journal_not_ready" in report["safety_blockers"]
    assert "no_fresh_realtime_quote" in report["safety_blockers"]


def test_no_signal_still_publishes_read_only_status(monkeypatch):
    monkeypatch.setattr("bot.paper_readiness_cli.research_candidates",
                        lambda *_args, **_kwargs: [])
    report = preview(PaperReadOnlyFake(), object(), ["F"])
    assert report["research_candidates"] == []
    assert report["illustrative_whole_share_plans"] == []
    assert not report["can_submit_orders"]


def test_failed_broker_get_does_not_place_orders():
    class FailedBroker(PaperReadOnlyFake):
        def get_account(self):
            raise RuntimeError("broker unreachable")

    try:
        preview(FailedBroker(), object(), ["F"])
    except RuntimeError as exc:
        assert "broker unreachable" in str(exc)
    else:
        raise AssertionError("Broker failures must fail closed")
