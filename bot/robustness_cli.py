"""Second-stage stress tests on fixed, previously examined strategies.

All portfolios start with a *separate* $500 virtual allocation per window and
compound realized profits immediately within that window. No deposited cash
and no orders. The windows are consecutive and nonoverlapping, but strategy
selection was informed by the earlier holdout, so these are NOT fresh
prospective out-of-sample results.
"""
import argparse
import json
import os

from alpaca.data.historical import StockHistoricalDataClient
from dotenv import load_dotenv

from .portfolio_research import run_portfolio
from .strategy_research_cli import load_bars
from .strategies import STRATEGIES


# Fixed, predeclared broader set of liquid, surviving, large/mid-cap stocks.
# Does NOT remove survivorship, selection, or corporate-action biases.
BROADER_SYMBOLS = (
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA",
    "JPM", "BAC", "XOM", "CVX", "KO",
    "WMT", "DIS", "F", "PFE", "INTC",
)


def evaluation_windows(total: int, count: int = 4, width: int = 105,
                       min_warmup: int = 260):
    if count < 1 or width < 20 or min_warmup < 253:
        raise ValueError("invalid window configuration")
    first = total - count * width
    if first < min_warmup:
        raise ValueError(
            f"Need at least {min_warmup + count * width} sessions, received {total}")
    return [(first + i * width, first + (i + 1) * width)
            for i in range(count)]


def compare_robustness(data, symbols, *, count=4, width=105):
    windows = evaluation_windows(len(data["SPY"]), count=count, width=width)
    output = []
    for window_number, (lo, hi) in enumerate(windows, 1):
        for strategy in STRATEGIES:
            for risk in (0.01, 0.02):
                for slip in (10.0, 30.0):
                    row = run_portfolio(
                        data, strategy, symbols,
                        risk_fraction=risk, start_index=lo, end_index=hi,
                        starting_cash=500.0, slippage_bps=slip)
                    row["window"] = window_number
                    row["slippage_bps"] = slip
                    # Avoid printing 100s of trade records to public CI logs.
                    output.append(row)
                    compact = {k: v for k, v in row.items() if k != "trades"}
                    print(json.dumps(compact, sort_keys=True))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--symbols", default=",".join(BROADER_SYMBOLS))
    parser.add_argument("--output", default=".state/robustness-report.json")
    args = parser.parse_args()
    symbols = tuple(x.strip().upper() for x in args.symbols.split(","))
    if (not 5 <= len(symbols) <= 20
            or len(set(symbols)) != len(symbols)
            or any(not x.isalpha() or len(x) > 5 or x == "SPY"
                   for x in symbols)):
        parser.error("Use 5-20 unique uppercase stock symbols (SPY reserved)")
    if not 730 <= args.days <= 1095:
        parser.error("--days must be between 730 and 1095")
    load_dotenv()
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        parser.error("Paper-market data credentials required")
    client = StockHistoricalDataClient(key, secret)
    start, end, data = load_bars(client, symbols, args.days)
    for symbol in (*symbols, "SPY"):
        if not data.get(symbol):
            raise SystemExit("No data for required symbol " + symbol)
    rows = compare_robustness(data, symbols)
    # Unlike the separate window experiments, these portfolios retain their
    # entire compounding history from the first eligible entry to the end.
    # There is NO reset at $1,000, at year boundaries, or after a profit.
    continuous = []
    for strategy in STRATEGIES:
        for risk in (.01, .02):
            for slip in (10.0, 30.0):
                result = run_portfolio(data, strategy, symbols,
                                       risk_fraction=risk, start_index=260,
                                       starting_cash=500.0, slippage_bps=slip)
                result["evaluation"] = "continuous_no_resets"
                result["slippage_bps"] = slip
                continuous.append(result)
                print("CONTINUOUS " + json.dumps(
                    {k: v for k, v in result.items() if k != "trades"},
                    sort_keys=True))
    summary = []
    for strategy in STRATEGIES:
        for risk in (0.01, 0.02):
            for slip in (10.0, 30.0):
                chosen = [r for r in rows if r["strategy"] == strategy
                          and r["risk_pct"] == risk * 100
                          and r["slippage_bps"] == slip]
                summary.append({
                    "strategy": strategy,
                    "risk_pct": risk * 100,
                    "slippage_bps": slip,
                    "positive_windows": sum(r["net_pnl"] > 0 for r in chosen),
                    "window_count": len(chosen),
                    "total_trades": sum(r["trade_count"] for r in chosen),
                    "mean_window_return_pct": round(
                        sum(r["return_pct"] for r in chosen) / len(chosen), 2),
                    "worst_window_return_pct": min(r["return_pct"] for r in chosen),
                    "worst_daily_close_drawdown_pct": max(
                        r["max_daily_close_drawdown_pct"] for r in chosen),
                    "first_1000_equity_day": next(
                        (r["first_1000_equity_day"] for r in chosen
                         if r["first_1000_equity_day"] is not None), None),
                })
    report = {
        "title": "Exploratory robustness and compounding audit, not proof of profits",
        "from_utc": start.isoformat(),
        "to_utc_exclusive": end.isoformat(),
        "data_source": "Alpaca IEX only, split-adjusted daily bars",
        "selected_symbols": list(symbols),
        "reinvest_profits_immediately": True,
        "compounding_starts_at_equity": 500,
        "first_1000_is_not_a_compounding_threshold": True,
        "monthly_deposits_simulated": 0,
        "continuous_test_from_equity": 500,
        "continuous_test_compounds_without_resets": True,
        "monthly_optional_contribution_cap_usd": 100,
        "window_design": (
            "Four consecutive, nonoverlapping ~105-session research windows "
            "with preceding history available for indicators. Each window "
            "restarts at $500 for comparability; within-window realized "
            "profits/losses change the next trade's size immediately. "
            "The same real portfolio would NOT restart every window."
        ),
        "known_limitations": [
            "These are repeated historical checks after inspecting earlier strategy results, NOT new unseen data.",
            "Selected surviving stocks, SPY price-only reference and IEX-only trade volumes bias conclusions.",
            "No additional cash/deposits, no fractional shares or dividend reinvestment.",
            "No event/earnings surprise feed, so earnings drift remains excluded.",
            "Each window is independent; returns and ending balances must not be added together.",
            "No taxes, brokerage fees, market impact, split-lot conversion or real order confirmation.",
            "Daily OHLC fills/stop gaps are simplified; short intraday drawdowns can exceed reported daily close drawdown.",
        ],
        "summary": summary,
        "continuous_portfolios": continuous,
        "runs": rows,
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    print(f"Saved {len(rows)} independent window simulations, "
          f"{len(continuous)} continuously compounded simulations, "
          f"{len(summary)} summary rows. No orders sent.")


if __name__ == "__main__":
    main()
