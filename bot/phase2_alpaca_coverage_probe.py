"""Small Alpaca Market Data GET-only historical-coverage smoke probe.

Never instantiates Alpaca TradingClient; never sends orders, changes Neon,
saves price datasets, logs secrets or prints raw market prices. This is
a *coverage sample*, NOT proof of point-in-time universe, delisting,
SIP/NBBO completeness or a validated 10-year history.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOST = "https://data.alpaca.markets/v2/stocks/bars"

# Each window is a small fixed historical slice. Only symbol/date counts
# reach GitHub CI logs, NEVER the actual prices or volume.
WINDOWS = (
    ("oct2016", "2016-10-01", "2016-11-01", ("SPY", "AAPL", "XLK")),
    ("bear2022", "2022-06-01", "2022-07-01", ("SPY", "AAPL", "XLK")),
    ("sep2026", "2026-09-01", "2026-10-01", ("SPY", "AAPL", "XLK")),
    ("historically_delisted_2022", "2022-06-01", "2022-07-01", ("FRC",)),
)
MAX_PAGES = 5


def _read_page(key: str, secret: str, symbols, start: str, end: str,
               *, asof: str, token: str | None = None,
               opener=urlopen) -> dict:
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day", "start": start, "end": end,
        "feed": "iex", "adjustment": "raw", "limit": 10000,
        "asof": asof,
    }
    if token:
        params["page_token"] = token
    url = HOST + "?" + urlencode(params)
    req = Request(url, headers={
        "Accept": "application/json",
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }, method="GET")
    try:
        with opener(req, timeout=25) as response:
            data = response.read(6_000_001)
    except HTTPError as exc:
        # HTTP status is public; URL, response body and headers are not.
        code = exc.code if exc.code in (401, 403, 404, 422, 429,
                                         500, 502, 503, 504) else 0
        raise RuntimeError(f"alpaca_marketdata_http_{code}") from None
    except (URLError, OSError, ValueError):
        # Never log the underlying response, URL or connection exception.
        raise RuntimeError("alpaca_marketdata_get_failed") from None
    if len(data) > 6_000_000:
        raise RuntimeError("alpaca_marketdata_response_too_large")
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        raise RuntimeError("alpaca_marketdata_invalid_json") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
        raise RuntimeError("alpaca_marketdata_unexpected_schema")
    return payload


def probe_window(key: str, secret: str, label: str, start: str, end: str,
                 symbols, *, fetch=_read_page) -> dict:
    requested = tuple(symbols)
    if (not requested or len(set(requested)) != len(requested)
            or not all(s.isalpha() and 1 <= len(s) <= 5 for s in requested)):
        raise ValueError("invalid fixed probe symbols")
    if not date.fromisoformat(start) < date.fromisoformat(end):
        raise ValueError("invalid probe dates")

    summary = {s: {"bars": 0, "first_day": None, "last_day": None}
               for s in requested}
    seen = set()
    token = None
    # Alpaca pages are sorted by symbol, so a single page does NOT
    # guarantee all requested symbols were observed.
    for _ in range(MAX_PAGES):
        payload = fetch(key, secret, requested, start, end, asof=end,
                        token=token)
        for symbol, items in payload["bars"].items():
            if symbol not in summary or not isinstance(items, list):
                raise RuntimeError("alpaca_marketdata_unexpected_bars")
            for item in items:
                if not isinstance(item, dict):
                    raise RuntimeError("alpaca_marketdata_invalid_bar")
                ts = item.get("t")
                if not isinstance(ts, str) or len(ts) < 10:
                    raise RuntimeError("alpaca_marketdata_invalid_timestamp")
                day = ts[:10]
                # Alpaca documents BOTH start and end as INCLUSIVE.
                if not start <= day <= end:
                    raise RuntimeError("alpaca_marketdata_out_of_window")
                try:
                    calendar_day = date.fromisoformat(day)
                except ValueError:
                    raise RuntimeError("alpaca_marketdata_invalid_date") from None
                if calendar_day.isoformat() != day:
                    raise RuntimeError("alpaca_marketdata_invalid_date")
                summary[symbol]["bars"] += 1
                lo = summary[symbol]["first_day"]
                hi = summary[symbol]["last_day"]
                if lo is None or day < lo:
                    summary[symbol]["first_day"] = day
                if hi is None or day > hi:
                    summary[symbol]["last_day"] = day
        next_token = payload.get("next_page_token")
        if next_token is None or next_token == "":
            return {
                "window": label, "from_inclusive": start, "to_inclusive": end,
                "feed": "IEX", "price_adjustment": "raw",
                "complete_pagination": True, "symbols": summary,
            }
        if not isinstance(next_token, str) or next_token in seen:
            raise RuntimeError("alpaca_marketdata_invalid_pagination")
        seen.add(next_token)
        token = next_token
    raise RuntimeError("alpaca_marketdata_incomplete_pagination")


def main() -> int:
    key = os.environ.get("ALPACA_PAPER_API_KEY")
    secret = os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        print("COVERAGE BLOCKED: paper market-data credentials missing.")
        return 2
    reports = []
    for label, start, end, symbols in WINDOWS:
        try:
            report = probe_window(key, secret, label, start, end, symbols)
        except Exception as exc:
            # Only codes originating from our own finite error namespace
            # are printed; arbitrary HTTP/JSON/exceptions may contain keys.
            reason = str(exc)
            safe = reason if (isinstance(exc, RuntimeError)
                              and reason.startswith("alpaca_marketdata_")
                              and reason.replace("_", "").isalnum()
                              and len(reason) < 72) else "retrieval_invalid"
            print(f"COVERAGE BLOCKED: {label} ({safe}); "
                  "no broker orders or datasets saved.")
            return 2
        reports.append(report)
        print("COVERAGE SAMPLE " + json.dumps(report, sort_keys=True))
    print("PROBE COMPLETE: counts/dates only; sample is NOT "
          "a ten-year, survivorship-free or total-return validation; "
          "ZERO broker orders, ZERO Neon writes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
