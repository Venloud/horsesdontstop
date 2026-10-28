"""Bounded PAPER SDK transport for the UNRELEASED single-attempt gateway.

No request is sent by construction. No CLI, credential storage or release flag.
"""
from urllib.parse import urlsplit

import requests

from .paper_safety import PAPER_ENDPOINT, TradeBlocked


class BoundedPaperSession(requests.Session):
    def request(self, method, url, **kwargs):
        parsed = urlsplit(str(url))
        if (parsed.scheme != "https" or parsed.netloc != "paper-api.alpaca.markets"
                or parsed.query or parsed.fragment or parsed.username or parsed.password
                or method.upper() not in ("GET", "POST")
                or method.upper() == "POST" and parsed.path != "/v2/orders"):
            raise TradeBlocked("Unexpected PAPER transport operation")
        kwargs["timeout"] = (3, 5)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def make_supervised_paper_client(key, secret):
    from alpaca.trading.client import TradingClient
    client = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    client._retry = 0  # alpaca-py otherwise retries 429/504, including POST.
    client._session.close()
    client._session = BoundedPaperSession()
    return client


def assert_single_attempt_transport(trading):
    if (type(getattr(trading, "_retry", None)) is not int or trading._retry != 0
            or not isinstance(getattr(trading, "_session", None), BoundedPaperSession)
            or any(adapter.max_retries.total != 0 for adapter in trading._session.adapters.values())):
        raise TradeBlocked("PAPER requires bounded transport with all HTTP retries disabled")
