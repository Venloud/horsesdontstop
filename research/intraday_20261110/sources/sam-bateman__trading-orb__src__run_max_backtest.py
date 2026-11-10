"""
The definitive stress test: run the walk-forward validated config on the full 10-year dataset.
Covers bull runs, COVID crash, meme mania, rate hike cycle, and everything in between.
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from fetch_max_data import load_max_dataset
from orb_strategy import compute_orb_features
from run_12m_optimization import add_volatility_features, generate_signals_12m
from backtester_v2 import Backtester

OUTPUT_DIR = Path(__file__).parent.parent / "backtest_max_output"
OUTPUT_DIR.mkdir(exist_ok=True)

# The walk-forward validated config
PARAMS = {
    'or_minutes': 20, 'target_mult': 0.75, 'stop_mult': 0.5,
    'vol_thresh': 1.2, 'entry_start': 10.0, 'entry_end': 11.5,
    'direction': 'both', 'risk_per_trade': 400, 'max_positions': 3,
    'min_vol_ratio': 1.2, 'max_vol_ratio': 999,
    'min_prev_day_range': 0, 'max_prev_day_range': 999,
    'min_or_range_pct': 0,
}


def main():
    """Load max history, generate signals with the validated params, run the backtest, and print a full report."""
    print("=" * 70)
    print("MAXIMUM HISTORY BACKTEST (2016-2026)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    data_raw = load_max_dataset()
    print(f"Loaded {len(data_raw)} symbols, {sum(len(df) for df in data_raw.values()):,} bars")

    # Add features
    print("Adding features...")
    for s in data_raw:
        data_raw[s] = compute_orb_features(data_raw[s])
        data_raw[s] = add_volatility_features(data_raw[s])

    # Generate signals
    print("Generating signals...")
    data = {}
    total_signals = 0
    for symbol, df in data_raw.items():
        data[symbol] = generate_signals_12m(df.copy(), PARAMS)
        sigs = (data[symbol]['signal'] != 0).sum()
        total_signals += sigs

    print(f"Total signals: {total_signals}")

    # Run backtest
    print("\nRunning backtest on full history...")
    bt = Backtester(risk_per_trade=400, max_positions=3)
    tl = bt.run(data)
    tl.to_csv(OUTPUT_DIR / 'trades_max.csv', index=False)

    if len(tl) == 0:
        print("NO TRADES")
        return

    n = len(tl)
    winners = tl[tl['pnl_net'] > 0]
    losers = tl[tl['pnl_net'] <= 0]
    longs = tl[tl['direction'] == 'LONG']
    shorts = tl[tl['direction'] == 'SHORT']

    total_pnl = tl['pnl_net'].sum()
    win_rate = len(winners) / n * 100
    avg_trade = total_pnl / n
    avg_win = winners['pnl_net'].mean() if len(winners) > 0 else 0
    avg_loss = losers['pnl_net'].mean() if len(losers) > 0 else 0
    pf = winners['pnl_net'].sum() / abs(losers['pnl_net'].sum()) if losers['pnl_net'].sum() != 0 else 0
    costs = tl['slippage_cost'].sum() + tl['commission_cost'].sum()

    cum = tl['pnl_net'].cumsum()
    max_dd = (cum - cum.cummax()).min()

    tl['trade_date'] = pd.to_datetime(tl['entry_time']).dt.date
    daily_pnl = tl.groupby('trade_date')['pnl_net'].sum()
    trading_days = len(daily_pnl)
    winning_days = (daily_pnl > 0).sum()
    sharpe = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252) if daily_pnl.std() > 0 else 0
    ann_return = (total_pnl / 100_000) / (trading_days / 252) * 100

    # Halves
    half = n // 2
    h1 = tl.iloc[:half]['pnl_net'].sum()
    h2 = tl.iloc[half:]['pnl_net'].sum()

    # Yearly breakdown
    tl['trade_year'] = pd.to_datetime(tl['entry_time']).dt.year
    yearly = tl.groupby('trade_year')['pnl_net'].agg(['sum', 'count'])

    # Monthly
    tl['trade_month'] = pd.to_datetime(tl['entry_time']).dt.to_period('M')
    monthly = tl.groupby('trade_month')['pnl_net'].sum()
    profitable_months = (monthly > 0).sum()

    # Ticker
    ticker_pnl = tl.groupby('symbol')['pnl_net'].agg(['sum', 'count', 'mean'])
    profitable_tickers = (ticker_pnl['sum'] > 0).sum()

    # Robustness
    top5 = tl.nlargest(5, 'pnl_net')['pnl_net'].sum()
    top10 = tl.nlargest(10, 'pnl_net')['pnl_net'].sum()
    extra_slip = tl['shares'] * 0.01 * 2
    pnl_2x_slip = total_pnl - extra_slip.sum()
    pnl_3x_slip = total_pnl - extra_slip.sum() * 2

    np.random.seed(42)
    mc = np.array([np.random.choice(tl['pnl_net'].values, size=n, replace=True).sum() for _ in range(10000)])
    mc_profitable = (mc > 0).mean() * 100

    long_pnl = longs['pnl_net'].sum() if len(longs) > 0 else 0
    short_pnl = shorts['pnl_net'].sum() if len(shorts) > 0 else 0

    target_hits = (tl['exit_reason'] == 'target_hit').sum()

    # Print report
    print(f"\n{'='*70}")
    print(f"10-YEAR BACKTEST RESULTS (2016-2026)")
    print(f"{'='*70}")
    print(f"  Period:               {trading_days} trading days (~{trading_days/252:.1f} years)")
    print(f"  Total Trades:         {n:,}")
    print(f"  Winners:              {len(winners):,} ({win_rate:.1f}%)")
    print(f"  Losers:               {len(losers):,} ({100-win_rate:.1f}%)")
    print(f"  NET PnL:              ${total_pnl:+,.2f}")
    print(f"  Annualized Return:    {ann_return:+.1f}%")
    print(f"  Avg Trade:            ${avg_trade:+,.2f}")
    print(f"  Avg Winner:           ${avg_win:+,.2f}")
    print(f"  Avg Loser:            ${avg_loss:+,.2f}")
    print(f"  Win/Loss Ratio:       {abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "")
    print(f"  Profit Factor:        {pf:.2f}")
    print(f"  Max Drawdown:         ${max_dd:,.2f}")
    print(f"  Sharpe Ratio:         {sharpe:.2f}")
    print(f"  Calmar Ratio:         {total_pnl / abs(max_dd):.2f}" if max_dd != 0 else "")
    print(f"  Winning Days:         {winning_days}/{trading_days} ({winning_days/trading_days*100:.0f}%)")
    print(f"  Target Hit Rate:      {target_hits/n*100:.1f}%")
    print(f"  Total Costs:          ${costs:,.2f}")

    print(f"\n--- DIRECTION ---")
    print(f"  Long:  {len(longs):,} trades, PnL ${long_pnl:+,.2f}, "
          f"Win Rate {(longs['pnl_net']>0).mean()*100:.1f}%" if len(longs) > 0 else "  Long: 0")
    print(f"  Short: {len(shorts):,} trades, PnL ${short_pnl:+,.2f}, "
          f"Win Rate {(shorts['pnl_net']>0).mean()*100:.1f}%" if len(shorts) > 0 else "  Short: 0")

    print(f"\n--- YEARLY ---")
    for year, row in yearly.iterrows():
        marker = "+" if row['sum'] > 0 else "-"
        print(f"  {year}: ${row['sum']:>+10,.2f}  ({int(row['count']):>4} trades) {marker}")
    profitable_years = (yearly['sum'] > 0).sum()
    print(f"  Profitable years: {profitable_years}/{len(yearly)}")

    print(f"\n--- MONTHLY ---")
    print(f"  Profitable months: {profitable_months}/{len(monthly)} ({profitable_months/len(monthly)*100:.0f}%)")
    print(f"  Best month:  ${monthly.max():+,.2f}")
    print(f"  Worst month: ${monthly.min():+,.2f}")

    print(f"\n--- TICKERS ---")
    for sym in ticker_pnl.sort_values('sum', ascending=False).index:
        r = ticker_pnl.loc[sym]
        print(f"  {sym:6s}: ${r['sum']:>+10,.2f}  ({int(r['count']):>4} trades)")
    print(f"  Profitable: {profitable_tickers}/{len(ticker_pnl)}")

    print(f"\n--- ROBUSTNESS ---")
    print(f"  First half:          ${h1:+,.2f}")
    print(f"  Second half:         ${h2:+,.2f}")
    print(f"  Both halves:         {'YES' if h1 > 0 and h2 > 0 else 'NO'}")
    print(f"  PnL without top 5:   ${total_pnl - top5:+,.2f}")
    print(f"  PnL without top 10:  ${total_pnl - top10:+,.2f}")
    print(f"  Outlier dependent:   {'YES' if total_pnl > 0 and (total_pnl - top5) < 0 else 'NO'}")
    print(f"  With 2x slippage:    ${pnl_2x_slip:+,.2f}")
    print(f"  With 3x slippage:    ${pnl_3x_slip:+,.2f}")
    print(f"  Monte Carlo profit:  {mc_profitable:.1f}%")

    # VERDICT
    print(f"\n{'='*70}")
    print("VERDICT (10-YEAR)")
    print(f"{'='*70}")

    checks = {
        'Net profitable': total_pnl > 0,
        'PF >= 1.2': pf >= 1.2,
        'Sharpe >= 1.0': sharpe >= 1.0,
        'Both halves profitable': h1 > 0 and h2 > 0,
        'MC > 80%': mc_profitable > 80,
        'Survives 2x slippage': pnl_2x_slip > 0,
        'Not outlier dependent': not (total_pnl > 0 and (total_pnl - top5) < 0),
        '50%+ tickers profitable': profitable_tickers >= len(ticker_pnl) * 0.5,
        '50%+ months profitable': profitable_months >= len(monthly) * 0.5,
        '50%+ years profitable': profitable_years >= len(yearly) * 0.5,
        'Both directions profitable': long_pnl > 0 and short_pnl > 0,
    }

    for check, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {check}")

    passed = sum(v for v in checks.values())
    failed = sum(not v for v in checks.values())
    print(f"\n  Score: {passed}/{len(checks)}")

    if failed == 0:
        print("\n  THIS IS A VALIDATED STRATEGY ACROSS 10 YEARS OF MARKET DATA.")
        print("  It survived bull runs, bear markets, COVID, meme stocks, rate hikes,")
        print("  and tariff volatility. Proceed to paper trading with confidence.")
    elif failed <= 2:
        print("\n  MOSTLY VALIDATED. Minor concerns but the core edge appears real.")
    else:
        print(f"\n  FAILED {failed} CHECKS. Strategy does not hold up over 10 years.")

    # Compare to 12-month results
    print(f"\n{'='*70}")
    print(f"12-MONTH vs 10-YEAR COMPARISON")
    print(f"{'='*70}")
    print(f"  {'Metric':<25} {'12 months':>12} {'10 years':>12}")
    print(f"  {'Trading days':<25} {'234':>12} {trading_days:>12}")
    print(f"  {'Total trades':<25} {'391':>12} {n:>12,}")
    print(f"  {'Net PnL':<25} {'$+5,185':>12} ${total_pnl:>+11,.0f}")
    print(f"  {'Ann. Return':<25} {'8.7%':>12} {ann_return:>+11.1f}%")
    print(f"  {'Sharpe':<25} {'5.46':>12} {sharpe:>12.2f}")
    print(f"  {'PF':<25} {'1.70':>12} {pf:>12.2f}")
    print(f"  {'Win Rate':<25} {'54.5%':>12} {win_rate:>11.1f}%")
    print(f"  {'Max DD':<25} {'$-630':>12} ${max_dd:>11,.0f}")
    print(f"  {'MC Profitable':<25} {'100%':>12} {mc_profitable:>11.0f}%")
    print(f"{'='*70}")

    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return tl


if __name__ == "__main__":
    tl = main()
