"""Real SDK with intercepted HTTP only; never contact any broker."""
import pytest
import requests

from bot.paper_broker_transport import (
    BoundedPaperSession, assert_single_attempt_transport, make_supervised_paper_client,
)
from bot.paper_safety import PAPER_ENDPOINT, TradeBlocked


def test_actual_sdk_rate_limit_cannot_retry_post(monkeypatch):
    from alpaca.common.exceptions import APIError
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    attempts = []
    def response(_session, method, url, **kwargs):
        attempts.append((method, url, kwargs))
        result = requests.Response()
        result.status_code = 429
        result.url = url
        result._content = b'{"message":"synthetic rate limit"}'
        return result
    monkeypatch.setattr(requests.Session, "request", response)
    client = make_supervised_paper_client("fake-test-key", "fake-test-secret")
    assert_single_attempt_transport(client)
    with pytest.raises(APIError):
        client.submit_order(LimitOrderRequest(
            symbol="F", qty=1, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY, limit_price=25))
    assert len(attempts) == 1
    assert attempts[0][1] == PAPER_ENDPOINT + "/v2/orders"
    assert attempts[0][2]["timeout"] == (3, 5)
    assert attempts[0][2]["allow_redirects"] is False


@pytest.mark.parametrize("method,url", [
    ("POST", "https://api.alpaca.markets/v2/orders"),
    ("DELETE", PAPER_ENDPOINT + "/v2/orders/one"),
    ("PATCH", PAPER_ENDPOINT + "/v2/orders/one"),
    ("POST", PAPER_ENDPOINT + "/v2/positions"),
    ("GET", PAPER_ENDPOINT + "/v2/account?token=unexpected"),
])
def test_unexpected_broker_transport_operation_never_reaches_http(monkeypatch, method, url):
    monkeypatch.setattr(requests.Session, "request", lambda *_a, **_kw: pytest.fail("HTTP reached"))
    with pytest.raises(TradeBlocked):
        BoundedPaperSession().request(method, url)


def test_sdk_or_adapter_retries_and_unbounded_session_are_refused():
    client = make_supervised_paper_client("fake", "fake")
    client._retry = 3
    with pytest.raises(TradeBlocked): assert_single_attempt_transport(client)
    client._retry = 0
    client._session.mount("https://", requests.adapters.HTTPAdapter(max_retries=1))
    with pytest.raises(TradeBlocked): assert_single_attempt_transport(client)
    client._session = requests.Session()
    with pytest.raises(TradeBlocked): assert_single_attempt_transport(client)
