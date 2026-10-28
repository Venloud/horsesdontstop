"""Independent staged service: fake brokers only, no credentials or network."""
import pytest
import requests

from bot import paper_reconciler_service as service
from bot.paper_broker_transport import make_reconciler_paper_client
from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked


def test_default_service_cannot_release_status_writes_or_start_without_credentials(monkeypatch):
    assert service.PAPER_RECONCILIATION_SERVICE_RELEASED is False
    assert service.main(["--apply-status"]) == 2
    for key in ("PAPER_RECONCILER_DSN", "ALPACA_RECONCILER_PAPER_API_KEY", "ALPACA_RECONCILER_PAPER_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert service.main([]) == 2


def test_observe_service_ticks_never_record_status(monkeypatch):
    records = []
    sleeps = []
    monkeypatch.setattr(service, "reconcile_pending_once", lambda **kw: records.append(kw["record"]))
    assert service.run_service(trading=object(), journal=object(), max_ticks=2, sleeper=sleeps.append) == 2
    assert records == [False, False] and sleeps == [30]


def test_unreleased_status_mode_stops_before_broker_or_journal(monkeypatch):
    monkeypatch.setattr(service, "reconcile_pending_once", lambda **kw: pytest.fail("Read reached"))
    with pytest.raises(TradeBlocked, match="NOT RELEASED"):
        service.run_service(trading=object(), journal=object(), apply_status=True)


def test_reconciliation_failure_alerts_and_stops_without_retry(monkeypatch):
    attempts, alerts = [], []
    def unresolved(**kw):
        attempts.append(kw)
        raise RuntimeError("SENSITIVE_CREDENTIAL")
    monkeypatch.setattr(service, "reconcile_pending_once", unresolved)
    with pytest.raises(TradeBlocked, match="unresolved") as exc:
        service.run_service(trading=object(), journal=object(), max_ticks=2,
                            alert=lambda: alerts.append(True) or True)
    assert len(attempts) == 1 and alerts == [True]
    assert "SENSITIVE_CREDENTIAL" not in str(exc.value)


def test_independent_sdk_transport_refuses_even_an_accidental_order_post(monkeypatch):
    monkeypatch.setattr(requests.Session, "request", lambda *_a, **_kw: pytest.fail("HTTP reached"))
    client = make_reconciler_paper_client("fake", "fake")
    with pytest.raises(TradeBlocked, match="GET only"):
        client._session.request("POST", PAPER_ENDPOINT + "/v2/orders")
