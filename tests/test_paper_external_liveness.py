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
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


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
    seen=[]
    def opener(request, timeout):
        seen.append((request,timeout))
        return Reply()
    assert send_liveness(
        url=HOST, token=TOKEN, worker_id=WORKER, sequence=3,
        status="closed_flat_idle", opener=opener) is True
    request,timeout=seen[0]
    assert timeout==5
    assert request.get_method()=="POST"
    assert request.headers["Authorization"]=="Bearer "+TOKEN
    body=json.loads(request.data)
    assert body=={"worker_id":WORKER,"sequence":3,"status":"closed_flat_idle"}
    assert TOKEN not in str(body)
    assert "broker" not in str(body).lower()


@pytest.mark.parametrize("data", [
    {"worker_id":"paper-invalid"},
    {"worker_id":"paper-"+"x"*32},
    {"sequence":True},
    {"sequence":0},
    {"sequence":"1"},
    {"sequence":2147483648},
    {"status":"healthy_to_trade"},
    {"status":None},
])
def test_malformed_beacons_refused_before_network(data):
    values={"url":HOST,"token":TOKEN,"worker_id":WORKER,
            "sequence":3,"status":"read_only_poll_ok"}
    values.update(data)
    def forbidden(*args,**kwargs):
        raise AssertionError("No HTTP call")
    with pytest.raises(TradeBlocked):
        send_liveness(**values,opener=forbidden)


def test_transport_exceptions_sanitized_and_not_reported_delivered():
    def leak(*args,**kwargs):
        raise RuntimeError("postgresql://SENSITIVE_PASSWORD")
    with pytest.raises(TradeBlocked,match="delivery failed") as exc:
        send_liveness(url=HOST,token=TOKEN,worker_id=WORKER,
                      sequence=1,status="poll_failed",opener=leak)
    assert "SENSITIVE_PASSWORD" not in str(exc.value)


def test_unacknowledged_non_204_fails_closed():
    with pytest.raises(TradeBlocked,match="not acknowledged"):
        send_liveness(url=HOST,token=TOKEN,worker_id=WORKER,
                      sequence=1,status="poll_failed",
                      opener=lambda *_,**__:Reply(200))


def test_liveness_source_does_not_import_broker_clients_or_submit_orders():
    import bot.paper_external_liveness as module
    src=Path(module.__file__).read_text()
    assert "TradingClient(" not in src
    assert ".submit_order(" not in src
    assert ".cancel_order(" not in src
    assert "PAPER_ORDER_SUBMISSIONS_RELEASED = True" not in src
