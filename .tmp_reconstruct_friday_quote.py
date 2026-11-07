import json
import os
from datetime import date, datetime, timedelta

from alpaca.data.enums import DataFeed, Sort
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockQuotesRequest

from bot.paper_readiness_cli import research_candidates
from bot.robustness_cli import BROADER_SYMBOLS


def main():
    at = datetime.fromisoformat("2026-11-06T14:46:07.878028+00:00")
    client = StockHistoricalDataClient(
        os.environ["ALPACA_PAPER_API_KEY"],
        os.environ["ALPACA_PAPER_SECRET_KEY"],
    )
    candidates = research_candidates(
        client, BROADER_SYMBOLS, now=at, required_session=date(2026, 11, 5)
    )
    rows = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        request = StockQuotesRequest(
            symbol_or_symbols=symbol,
            start=at - timedelta(seconds=30),
            end=at + timedelta(seconds=30),
            limit=10000,
            sort=Sort.ASC,
            feed=DataFeed.IEX,
        )
        try:
            quotes = client.get_stock_quotes(request).data.get(symbol, [])
            nearest = min(quotes, key=lambda q: abs((q.timestamp - at).total_seconds())) if quotes else None
            quote = None
            if nearest is not None:
                midpoint = (nearest.bid_price + nearest.ask_price) / 2
                quote = {
                    "timestamp_utc": nearest.timestamp.isoformat(),
                    "age_seconds_at_executor": round((at - nearest.timestamp).total_seconds(), 3),
                    "bid": str(nearest.bid_price),
                    "ask": str(nearest.ask_price),
                    "bid_size": nearest.bid_size,
                    "ask_size": nearest.ask_size,
                    "spread_bps": round(float((nearest.ask_price - nearest.bid_price) / midpoint * 10000), 2),
                }
            rows.append({"candidate": candidate, "quote_count": len(quotes), "nearest_quote": quote})
        except Exception as exc:
            rows.append({"candidate": candidate, "query_error_type": type(exc).__name__})
    print(json.dumps({
        "as_of_utc": at.isoformat(),
        "required_session": "2026-11-05",
        "candidate_count": len(candidates),
        "candidates": rows,
        "configured_max_spread_bps": 75,
    }, indent=2))


if __name__ == "__main__":
    main()
