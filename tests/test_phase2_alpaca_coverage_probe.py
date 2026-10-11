"""Synthetic-only Alpaca historical coverage checks: GETs, no order APIs."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from bot import phase2_alpaca_coverage_probe as probe


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, length):
        assert length <= 6_000_001
        return self.payload


def bar(day):
    return {"t": day + "T00:00:00Z", "o": 100, "h": 101,
            "l": 99, "c": 100, "v": 123}


def test_probe_uses_https_iex_get_and_never_shows_prices_or_keys():
    seen = []

    def opener(request, timeout):
        assert timeout == 25
        seen.append(request)
        assert request.get_method() == "GET"
        url = urlparse(request.full_url)
        assert url.scheme == "https"
        assert url.hostname == "data.alpaca.markets"
        assert url.path == "/v2/stocks/bars"
        q = parse_qs(url.query)
        assert q["symbols"] == ["SPY,AAPL"]
        assert q["feed"] == ["iex"]
        assert q["adjustment"] == ["raw"]
        assert q["timeframe"] == ["1Day"]
        assert q["start"] == ["2022-06-01"]
        assert q["asof"] == ["2022-07-01"]
        assert request.get_header("Apca-api-key-id") == "TEST_ONLY_KEY"
        assert request.get_header("Apca-api-secret-key") == "TEST_ONLY_SECRET"
        return FakeHttpResponse({"bars": {"SPY": [bar("2022-06-15")]},
                                 "next_page_token": None})

    result = probe._read_page(
        "TEST_ONLY_KEY", "TEST_ONLY_SECRET", ("SPY", "AAPL"),
        "2022-06-01", "2022-07-01",
        asof="2022-07-01", opener=opener,
    )
    assert len(seen) == 1
    assert result["bars"]["SPY"][0]["c"] == 100
    assert "TEST_ONLY_SECRET" not in seen[0].full_url


def test_multiple_pages_cover_symbols_not_seen_on_first_page():
    calls = []

    def fetch(_key, _secret, symbols, start, end, *, asof, token):
        calls.append(token)
        if token is None:
            return {
                "bars": {"SPY": [bar("2016-10-03"),
                                 bar("2016-10-04")]},
                "next_page_token": "page_two",
            }
        assert token == "page_two"
        return {
            "bars": {"AAPL": [bar("2016-10-04")]},
            "next_page_token": None,
        }

    result = probe.probe_window(
        "key", "secret", "oct2016", "2016-10-01",
        "2016-11-01", ("SPY", "AAPL"), fetch=fetch)
    assert calls == [None, "page_two"]
    assert result["symbols"]["SPY"]["bars"] == 2
    assert result["symbols"]["AAPL"]["bars"] == 1
    assert result["symbols"]["AAPL"]["first_day"] == "2016-10-04"
    assert "o" not in json.dumps(result)
    assert result["complete_pagination"] is True


def test_empty_delisted_symbol_is_unknown_not_false_claim_of_survivorship():
    result = probe.probe_window(
        "key", "secret", "delisted", "2022-06-01",
        "2022-07-01", ("FRC",),
        fetch=lambda *_args, **_kw: {
            "bars": {}, "next_page_token": None,
        })
    assert result["symbols"]["FRC"]["bars"] == 0
    assert result["symbols"]["FRC"]["first_day"] is None


@pytest.mark.parametrize("payload", [
    {"bars": {"SPY": [bar("2021-12-01")]}},
    {"bars": {"SPY": [bar("2022-07-01")]}},
    {"bars": {"SPY": [{"t": "2022-06-xxT00:00:00Z"}]}},
    {"bars": {"HIDDEN": [bar("2022-06-10")]}},
    {"bars": {"SPY": {"t": "2022-06-10"}}},
    {"bars": {"SPY": [None]}},
])
def test_rejects_unexpected_marketdata_without_logging(payload):
    with pytest.raises(RuntimeError):
        probe.probe_window(
            "key", "secret", "bear2022", "2022-06-01",
            "2022-07-01", ("SPY",),
            fetch=lambda *_args, **_kw: payload)


def test_duplicate_or_endless_pagination_is_rejected():
    with pytest.raises(RuntimeError, match="invalid_pagination"):
        probe.probe_window(
            "key", "secret", "bear2022", "2022-06-01",
            "2022-07-01", ("SPY",),
            fetch=lambda *_args, **_kw: {
                "bars": {}, "next_page_token": "same_token",
            })


def test_url_error_message_containing_sensitive_text_never_leaks():
    def failing(request, timeout):
        raise URLError("PAPER_API_KEY_SUPER_SECRET")
    with pytest.raises(RuntimeError) as error:
        probe._read_page(
            "PRIVATE_KEY", "PRIVATE_SECRET", ("SPY",),
            "2022-06-01", "2022-07-01",
            asof="2022-07-01", opener=failing)
    assert "SUPER_SECRET" not in str(error.value)
    assert "PRIVATE_SECRET" not in str(error.value)


def test_oversized_response_is_blocked():
    class Oversize(FakeHttpResponse):
        def read(self, length):
            return b"x" * (6_000_000 + 1)

    with pytest.raises(RuntimeError, match="too_large"):
        probe._read_page(
            "key", "secret", ("SPY",),
            "2022-06-01", "2022-07-01",
            asof="2022-07-01",
            opener=lambda _request, timeout: Oversize({}))


def test_missing_secrets_exits_before_network(monkeypatch, capsys):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET_KEY", raising=False)
    assert probe.main() == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_probe_has_no_broker_or_database_writes():
    from pathlib import Path
    path = Path(probe.__file__)
    text = path.read_text()
    assert "import alpaca" not in text
    assert "TradingClient(" not in text
    assert ".submit_order(" not in text
    assert ".execute(" not in text
    assert "Neon" not in text.split("def main()")[1]
    assert "save_price" not in text
