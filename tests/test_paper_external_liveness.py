"""Offline safety tests for out-of-band Cloudflare PAPER liveness POST."""
from pathlib import Path

import pytest

from bot.paper_external_liveness import check_liveness_settings, send_liveness
from bot.paper_safety import TradeBlocked

HOST = "https://horsesdontstop-liveness-staging.example.workers.dev/v1/heartbeat"
TOKEN = "a" * 64
WORKER = "paper-" + "b" * 32


class Reply:
    def __init__(self, status=204):
        self.status_code = status


def test_valid_liveness_endpoint_requires_private_token():
    assert check_liveness_settings(HOST, TOKEN) == (HOST, TOKEN)


@pytest.mark.parametrize("url", [
    "http://horsesdontstop-liveness-staging.example.workers.dev/v1/heartbeat",
    "https://evil.example/v1/heartbeat",
    "https://example.workers.dev/v1/heartbeat",
    "https://user:secret@foo.bar.workers.dev/v1/heartbeat",
    "https://foo.bar.workers.dev/v1/heartbeat?redirect=https://evil",
    "https://foo.bar.workers.dev/v1/heartbeat#foo",
    "https://foo.bar.workers.dev:8443/v1/heartbeat",
    "https://foo.bar.workers.dev/v1/heartbeat/extra",
    "https://foo.bar.workers.dev@evil.com/v1/heartbeat",
    "https://foo.bar.workers.dev/v1/heartbeat?host=evil.com",
])
def test_rejects_insecure_or_non_cloudflare_urls(url):
    with pytest.raises(TradeBlocked):
        check_liveness_settings(url, TOKEN)


def test_rejects_secret_that_is_short_or_missing():
    for token in ("short", "", None, "bad value", "x"*300):
        with pytest.raises(TradeBlocked):
            check_liveness_settings(HOST, token)


def test_send_liveness_stages_fixed_minimal_payload_and_hides_token():
    import json
    seen = []
    def post(url, *, data, headers, timeout, allow_redirects):
        seen.append((url, data, headers, timeout, allow_redirects))
        return Reply()
    assert send_liveness(
        url=HOST, token=TOKEN, worker_id=WORKER, sequence=3,
        status="closed_flat_idle", post=post) is True
    url, data, headers, timeout, allow_redirects = seen[0]
    assert url == HOST
    assert timeout == 5
    assert allow_redirects is False
    assert headers["Authorization"] == "Bearer " + TOKEN
    assert headers["Content-Type"] == "application/json"
    body = json.loads(data)
    assert body == {"worker_id": WORKER, "sequence": 3, "status": "closed_flat_idle"}
    assert TOKEN not in str(body)
    assert "broker" not in str(body).lower()


def test_default_liveness_sender_uses_requests(monkeypatch):
    import bot.paper_external_liveness as module
    seen = []
    def fake_requests_post(url, *, data, headers, timeout, allow_redirects):
        seen.append((url, data, headers, timeout, allow_redirects))
        return Reply()
    monkeypatch.setattr(module.requests, "post", fake_requests_post)
    assert send_liveness(url=HOST, token=TOKEN, worker_id=WORKER,
                         sequence=1, status="read_only_poll_ok") is True
    assert len(seen) == 1
    assert seen[0][4] is False


@pytest.mark.parametrize("data", [
    {"worker_id": "paper-invalid"},
    {"worker_id": "paper-"+"x"*32},
    {"sequence": True},
    {"sequence": 0},
    {"sequence": "1"},
    {"sequence": 2147483648},
    {"status": "healthy_to_trade"},
    {"status": None},
])
def test_malformed_beacons_refused_before_network(data):
    values={"url":HOST,"token":TOKEN,"worker_id":WORKER,
            "sequence":3,"status":"read_only_poll_ok"}
    values.update(data)
    def forbidden(*args, **kwargs):
        raise AssertionError("No HTTP call")
    with pytest.raises(TradeBlocked):
        send_liveness(**values, post=forbidden)


def test_transport_exceptions_sanitized_and_not_reported_delivered():
    def leak(*args, **kwargs):
        raise RuntimeError("postgresql://SENSITIVE_PASSWORD")
    with pytest.raises(TradeBlocked, match="delivery failed") as exc:
        send_liveness(url=HOST,token=TOKEN,worker_id=WORKER,
                      sequence=1,status="poll_failed",post=leak)
    assert "SENSITIVE_PASSWORD" not in str(exc.value)


@pytest.mark.parametrize("status", [200, 301, 400, 401, 403, 409, 503])
def test_unacknowledged_non_204_fails_closed(status):
    with pytest.raises(TradeBlocked, match="not acknowledged"):
        send_liveness(url=HOST,token=TOKEN,worker_id=WORKER,
                      sequence=1,status="poll_failed",
                      post=lambda *_,**__: Reply(status))


def test_liveness_source_does_not_import_broker_clients_or_submit_orders():
    import bot.paper_external_liveness as module
    src = Path(module.__file__).read_text()
    assert "TradingClient(" not in src
    assert ".submit_order(" not in src
    assert ".cancel_order(" not in src
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = True" not in src
