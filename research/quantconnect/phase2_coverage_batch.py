"""QuantConnect FREE cloud research notebook: ONE batch coverage check.

Paste into a QuantBook research notebook (project HorsesDontStop-Research)
and run once. READ-ONLY: history, split, dividend and delisting queries only.
No orders, no deployment, no broker keys, no data written anywhere.
Prints a compact text report; paste the whole output into
research/quantconnect/coverage_report_YYYYMMDD.txt (summary counts only,
NO raw prices) and commit that file.

UNTESTED against the live QuantConnect API as written by Claude from the
documented QuantBook interface; if a call errors, the error is printed per
symbol and the rest of the batch continues.
"""
from datetime import datetime

START = datetime(1998, 1, 1)
END = datetime(2026, 10, 1)

# Frozen sector-ETF universe from bot/phase2_research_signals.py
ETFS = ["SPY", "XLB", "XLC", "XLE", "XLF", "XLI",
        "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
# Large liquid stocks, plus once-listed names that later delisted.
STOCKS = ["AAPL", "MSFT", "JPM", "XOM", "JNJ", "GE", "INTC"]
DELISTED = ["FRC", "SIVB", "SBNY", "LEH", "WAMUQ", "ENRNQ"]

qb = QuantBook()


def add(ticker):
    try:
        try:
            return qb.add_equity(
                ticker, Resolution.DAILY,
                data_normalization_mode=DataNormalizationMode.TOTAL_RETURN
            ).symbol
        except Exception:
            return qb.add_equity(ticker, Resolution.DAILY).symbol
    except Exception as exc:
        print(f"{ticker}: add_equity failed: {exc}")
        return None


def years_report(hist):
    times = hist.index.get_level_values("time")
    counts = times.year.value_counts().sort_index()
    short = {int(y): int(c) for y, c in counts.items() if c < 240}
    return times.min().date(), times.max().date(), len(hist), short


def check(ticker, want_events=True):
    sym = add(ticker)
    if sym is None:
        return
    try:
        hist = qb.history(sym, START, END, Resolution.DAILY)
        if hist is None or len(hist) == 0:
            print(f"{ticker}: NO DAILY BARS")
            return
        first, last, n, short = years_report(hist)
        line = f"{ticker}: first={first} last={last} bars={n}"
        if short:
            line += f" years_with_<240_bars={short}"
        print(line)
    except Exception as exc:
        print(f"{ticker}: history failed: {exc}")
        return
    if not want_events:
        return
    for name, kind in (("splits", Split), ("dividends", Dividend),
                       ("delistings", Delisting)):
        try:
            ev = qb.history(kind, sym, START, END)
            print(f"   {name}: {0 if ev is None else len(ev)}")
        except Exception as exc:
            print(f"   {name}: failed: {exc}")


print("=== ETF universe (total-return mode; 10y+ needed by the 10-year rule)")
for t in ETFS:
    check(t, want_events=False)
print("=== Liquid stocks")
for t in STOCKS:
    check(t)
print("=== Names that later delisted (survivorship check)")
for t in DELISTED:
    check(t)
print("=== done; paste this whole output, summary counts only")
