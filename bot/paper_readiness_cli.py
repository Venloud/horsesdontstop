"""Read-only paper-trading readiness and momentum-candidate preview.

Uses PAPER account credentials for read-only GETs. NEVER submits, cancels or
modifies an Alpaca order. Signals use completed IEX daily bars, NOT executable
quotes. The kill switch remains on; preview is NOT an entry instruction.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest
from dotenv import load_dotenv

from .paper_safety import (
    PAPER_ENDPOINT, PaperSafetyState, TradeBlocked, execution_blockers,
    plan_paper_entry,
)
from .robustness_cli import BROADER_SYMBOLS
from .strategies import DailyBar, signal_score


def research_candidates(market_data_client, symbols, now=None):
    """Return signal candidates based on prior completed UTC-calendar day.

    This is deliberately NOT market-open real-time data. Before order submission,
    fresh consolidated or sufficiently accurate quotes and market-session
    checks will be required.
    """
    now = now or datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=125)
    request = StockBarsRequest(
        symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
        start=start, end=end, feed=DataFeed.IEX)
    response = market_data_client.get_stock_bars(request)
    candidates = []
    for symbol in symbols:
        raw = sorted(response.data.get(symbol, []), key=lambda row: row.timestamp)
        series = [
            DailyBar(b.timestamp.date(), float(b.open), float(b.high),
                     float(b.low), float(b.close), float(b.volume))
            for b in raw if b.timestamp < end
        ]
        score = signal_score("momentum_volume", series)
        if score is not None and score > 0:
            candidates.append({
                "symbol": symbol,
                "prior_completed_bar_date": series[-1].day.isoformat(),
                "reference_close": round(series[-1].close, 4),
                "momentum_score": round(score, 5),
            })
    return sorted(candidates, key=lambda row: (-row["momentum_score"], row["symbol"]))


def preview(client, data_client, symbols, *, now=None):
    now = now or datetime.now(timezone.utc)
    account = client.get_account()
    clock = client.get_clock()
    positions = client.get_all_positions()
    open_orders = client.get_orders(
        filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    state = PaperSafetyState(
        equity=float(account.equity), available_cash=float(account.cash),
        # Missing peak/daily equity from a DURABLE store must remain blockers.
        day_open_equity=None, recorded_peak_equity=None,
        open_positions=len(positions), open_orders=len(open_orders),
        market_open=bool(clock.is_open),
        trading_blocked=bool(getattr(account, "trading_blocked", True)),
        # Daily IEX bar close != fresh executable quote.
        recent_quote_seconds=None, broker_endpoint=PAPER_ENDPOINT,
    )
    candidates = research_candidates(data_client, symbols, now=now)
    scenarios = []
    for candidate in candidates[:8]:
        day = now.date()
        for fraction in (.01, .02):
            try:
                plan = plan_paper_entry(
                    symbol=candidate["symbol"], day=day,
                    reference_price=candidate["reference_close"],
                    equity=state.equity, available_cash=state.available_cash,
                    risk_fraction=fraction)
                scenarios.append({
                    **candidate, "risk_pct": int(fraction * 100),
                    "plan": plan.to_dict(), "executable": False,
                })
            except TradeBlocked as exc:
                scenarios.append({
                    **candidate, "risk_pct": int(fraction * 100),
                    "plan": None, "blocked_reason": str(exc),
                    "executable": False,
                })
    return {
        "mode": "READ_ONLY_PREVIEW",
        "generated_at_utc": now.isoformat(),
        "paper_endpoint": PAPER_ENDPOINT,
        "account_equity": round(state.equity, 2),
        "account_cash": round(state.available_cash, 2),
        "market_open": state.market_open,
        "existing_position_count": state.open_positions,
        "existing_open_order_count": state.open_orders,
        "strategies_previewed": ["momentum_volume"],
        "signals_observed": len(candidates),
        "safety_blockers": execution_blockers(state),
        "can_submit_orders": False,
        "research_candidates": candidates,
        "illustrative_whole_share_plans": scenarios,
        "warnings": [
            "READ ONLY. Never invokes submit_order or changes broker state.",
            "Prices are historical completed IEX closes, not current quotes.",
            "No persistent journal, reconciliation, or live watchdog is configured.",
            "Stop-loss order prices do not guarantee fills during gaps.",
            "Profits increase future risk budgets immediately; so do losses.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(BROADER_SYMBOLS))
    parser.add_argument("--output", default=".state/paper-readiness.json")
    args = parser.parse_args()
    symbols = tuple(s.strip().upper() for s in args.symbols.split(","))
    if (not 1 <= len(symbols) <= 20 or len(set(symbols)) != len(symbols)
            or any(not s.isalpha() or len(s) > 5 for s in symbols)):
        parser.error("Provide 1-20 distinct uppercase stock symbols")
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        parser.error("Set paper credentials through GitHub Actions secrets")
    # Explicit, hardcoded PAPER URL. No option for live endpoint.
    client = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    data_client = StockHistoricalDataClient(key, secret)
    result = preview(client, data_client, symbols)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"PAPER equity: ${result['account_equity']:.2f}; "
          f"cash: ${result['account_cash']:.2f}; "
          f"market open: {result['market_open']}")
    print(f"Completed-day momentum signals: {result['signals_observed']}")
    print(f"Safety gates still blocking orders: "
          f"{', '.join(result['safety_blockers'])}")
    for row in result["illustrative_whole_share_plans"][:8]:
        if row["plan"]:
            p = row["plan"]
            print(f"PREVIEW {p['symbol']} {row['risk_pct']}%: "
                  f"{p['whole_shares']} whole shares, reference ${row['reference_close']:.2f}, "
                  f"planned stop ${p['protective_stop']:.2f} [NO ORDER]")
        else:
            print(f"PREVIEW {row['symbol']} {row['risk_pct']}%: "
                  f"{row['blocked_reason']} [NO ORDER]")
    print(f"Read-only readiness report saved at {dest}. ZERO broker orders sent.")


if __name__ == "__main__":
    main()
