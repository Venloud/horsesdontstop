"""
Event-driven backtester — processes bars one at a time, no lookahead.

Fills happen at the next bar's open. Slippage is $0.01/share each way,
commission $0.005/share. Fixed dollar risk sizing, max 3 positions, one
trade per ticker per day, $600 daily loss limit, flatten by 3:50 PM ET.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass, field

OUTPUT_DIR = Path(__file__).parent.parent / "phase4_output"


@dataclass
class Trade:
    symbol: str
    direction: int          # 1 = long, -1 = short
    entry_time: object
    entry_price: float
    shares: int
    target_price: float
    stop_price: float
    or_range: float         # For trailing stop calculation
    # Filled on exit
    exit_time: object = None
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_gross: float = 0.0
    pnl_net: float = 0.0
    slippage_cost: float = 0.0
    commission_cost: float = 0.0
    hold_bars: int = 0
    # Trailing stop state
    breakeven_stop_active: bool = False


@dataclass
class DayState:
    """Tracks per-day state."""
    date: object
    traded_symbols: set = field(default_factory=set)
    daily_pnl: float = 0.0
    daily_trades: int = 0
    halted: bool = False


class Backtester:
    """Bar-by-bar backtester. Fills on next open, realistic costs."""

    def __init__(
        self,
        initial_capital: float = 100_000,
        risk_per_trade: float = 200,
        max_positions: int = 3,
        max_notional: float = 50_000,
        max_pct_of_account: float = 0.05,
        daily_loss_limit: float = 600,
        slippage_per_share: float = 0.01,
        commission_per_share: float = 0.005,
        no_trade_first_minutes: int = 5,
        no_trade_last_minutes: int = 10,
        flatten_time: float = 15.833,    # 3:50 PM = 15 + 50/60
    ):
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.max_positions = max_positions
        self.max_notional = max_notional
        self.max_pct = max_pct_of_account
        self.daily_loss_limit = daily_loss_limit
        self.slippage = slippage_per_share
        self.commission = commission_per_share
        self.no_trade_start = 9.5 + (no_trade_first_minutes / 60)  # 9:35 AM
        self.no_trade_end = 16.0 - (no_trade_last_minutes / 60)    # 3:50 PM
        self.flatten_time = flatten_time

        # State
        self.capital = initial_capital
        self.open_positions: List[Trade] = []
        self.closed_trades: List[Trade] = []
        self.pending_entries: List[dict] = []
        self.day_state: Optional[DayState] = None
        self.equity_curve: List[dict] = []

    def _reset(self):
        self.capital = self.initial_capital
        self.open_positions = []
        self.closed_trades = []
        self.pending_entries = []
        self.day_state = None
        self.equity_curve = []

    def _new_day(self, date):
        """Reset per-day state at the start of each trading day."""
        self.day_state = DayState(date=date)
        self.pending_entries = []

    def _calc_position_size(self, entry_price: float, stop_price: float) -> int:
        """Size the position so we risk exactly risk_per_trade dollars, capped by notional limits."""
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return 0

        # Size from risk
        shares = int(self.risk_per_trade / risk_per_share)

        # Cap by max notional
        max_shares_notional = int(self.max_notional / entry_price)
        shares = min(shares, max_shares_notional)

        # Cap by % of account
        max_shares_pct = int((self.capital * self.max_pct) / entry_price)
        shares = min(shares, max_shares_pct)

        # Must be at least 1 share
        return max(shares, 0)

    def _apply_slippage(self, price: float, direction: int, is_entry: bool) -> float:
        """Shift price by slippage amount — always in the direction that hurts us."""
        if is_entry:
            return price + (self.slippage * direction)  # Buy higher, sell lower
        else:
            return price - (self.slippage * direction)  # Exit: reverse

    def _close_position(self, trade: Trade, exit_price: float, exit_time, reason: str, bar_idx: int):
        """Apply exit slippage, compute gross/net PnL, update capital and day state."""
        # Apply slippage to exit
        fill_price = self._apply_slippage(exit_price, trade.direction, is_entry=False)

        trade.exit_time = exit_time
        trade.exit_price = fill_price
        trade.exit_reason = reason
        trade.hold_bars = bar_idx  # Will be set properly by caller

        # PnL calculation
        price_diff = (fill_price - trade.entry_price) * trade.direction
        trade.pnl_gross = price_diff * trade.shares

        # Costs
        trade.slippage_cost = self.slippage * 2 * trade.shares  # Entry + exit
        trade.commission_cost = self.commission * 2 * trade.shares  # Entry + exit
        total_costs = trade.slippage_cost + trade.commission_cost

        trade.pnl_net = trade.pnl_gross - total_costs

        self.capital += trade.pnl_net
        self.day_state.daily_pnl += trade.pnl_net
        self.day_state.daily_trades += 1

        self.closed_trades.append(trade)

    def _check_exits(self, bar: pd.Series, bar_idx: int):
        """Check time exit, stop loss, target, and breakeven trail for every open position."""
        to_close = []

        for i, trade in enumerate(self.open_positions):
            if trade.symbol != bar.get('symbol', ''):
                continue

            current_high = bar['High']
            current_low = bar['Low']
            current_close = bar['Close']
            current_time = bar['time_decimal']

            # 1. TIME EXIT: Flatten by 3:50 PM
            if current_time >= self.flatten_time:
                to_close.append((i, current_close, bar['Date'], 'time_exit'))
                continue

            # 2. STOP LOSS
            if trade.direction == 1 and current_low <= trade.stop_price:
                exit_price = trade.stop_price  # Assume stop fills at stop price
                to_close.append((i, exit_price, bar['Date'], 'stop_loss'))
                continue
            elif trade.direction == -1 and current_high >= trade.stop_price:
                exit_price = trade.stop_price
                to_close.append((i, exit_price, bar['Date'], 'stop_loss'))
                continue

            # 3. TARGET HIT
            if trade.direction == 1 and current_high >= trade.target_price:
                exit_price = trade.target_price  # Assume fills at target
                to_close.append((i, exit_price, bar['Date'], 'target_hit'))
                continue
            elif trade.direction == -1 and current_low <= trade.target_price:
                exit_price = trade.target_price
                to_close.append((i, exit_price, bar['Date'], 'target_hit'))
                continue

            # 4. TRAILING STOP: Move stop to breakeven after 1x OR profit
            if not trade.breakeven_stop_active and trade.or_range > 0:
                unrealized = (current_close - trade.entry_price) * trade.direction
                if unrealized >= trade.or_range:
                    trade.stop_price = trade.entry_price + (0.01 * trade.direction)
                    trade.breakeven_stop_active = True

        # Close in reverse order to maintain indices
        for i, exit_price, exit_time, reason in sorted(to_close, reverse=True):
            trade = self.open_positions.pop(i)
            self._close_position(trade, exit_price, exit_time, reason, bar_idx)

    def _check_entries(self, bar: pd.Series):
        """Fill any pending orders at this bar's open, subject to all risk filters."""
        if not self.pending_entries:
            return

        to_remove = []
        for i, entry in enumerate(self.pending_entries):
            # Check if this is for the same symbol
            if entry['symbol'] != bar.get('symbol', ''):
                continue

            to_remove.append(i)

            # Check daily halt
            if self.day_state.halted:
                continue

            # Check daily loss limit
            if self.day_state.daily_pnl <= -self.daily_loss_limit:
                self.day_state.halted = True
                continue

            # Check max positions
            if len(self.open_positions) >= self.max_positions:
                continue

            # Check already traded this symbol today
            if entry['symbol'] in self.day_state.traded_symbols:
                continue

            # Check time restrictions
            time = bar['time_decimal']
            if time < self.no_trade_start or time > self.no_trade_end:
                continue

            # Fill at this bar's open with slippage
            fill_price = self._apply_slippage(bar['Open'], entry['direction'], is_entry=True)

            # Recalculate position size with actual fill price
            shares = self._calc_position_size(fill_price, entry['stop_price'])
            if shares <= 0:
                continue

            # Adjust target/stop relative to actual fill
            price_diff = fill_price - entry['signal_price']
            target = entry['target_price'] + price_diff
            stop = entry['stop_price'] + price_diff

            trade = Trade(
                symbol=entry['symbol'],
                direction=entry['direction'],
                entry_time=bar['Date'],
                entry_price=fill_price,
                shares=shares,
                target_price=target,
                stop_price=stop,
                or_range=entry.get('or_range', 0),
            )

            self.open_positions.append(trade)
            self.day_state.traded_symbols.add(entry['symbol'])

        # Remove processed entries (reverse to preserve indices)
        for i in sorted(to_remove, reverse=True):
            self.pending_entries.pop(i)

    def _queue_entry(self, signal_bar: pd.Series):
        """Stage an entry to be filled at the next bar's open."""
        self.pending_entries.append({
            'symbol': signal_bar.get('symbol', ''),
            'direction': int(signal_bar['signal']),
            'signal_price': signal_bar['Close'],
            'target_price': signal_bar['target_price'],
            'stop_price': signal_bar['stop_price'],
            'or_range': signal_bar.get('or_range', 0),
        })

    def run(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Run the backtest. Pass in a dict of symbol -> DataFrame (with signal columns).
        Returns a DataFrame of all closed trades.
        """
        self._reset()

        # Merge all symbols into one timeline, sorted by date
        all_bars = []
        for symbol, df in data.items():
            bars = df.copy()
            if 'symbol' not in bars.columns:
                bars['symbol'] = symbol
            all_bars.append(bars)

        combined = pd.concat(all_bars).sort_values('Date').reset_index(drop=True)

        print(f"Running backtest: {len(combined)} bars, {len(data)} symbols, "
              f"{len(combined['trading_day'].unique())} days")
        print(f"Capital: ${self.initial_capital:,.0f}, Risk/trade: ${self.risk_per_trade}")

        current_day = None

        for idx in range(len(combined)):
            bar = combined.iloc[idx]
            bar_day = bar['trading_day']

            # New day?
            if bar_day != current_day:
                # Flatten any remaining positions from previous day (shouldn't happen)
                if current_day is not None:
                    for trade in list(self.open_positions):
                        prev_bar = combined.iloc[idx - 1]
                        if trade.symbol == prev_bar.get('symbol', ''):
                            self.open_positions.remove(trade)
                            self._close_position(trade, prev_bar['Close'], prev_bar['Date'],
                                                 'end_of_day', idx)

                current_day = bar_day
                self._new_day(bar_day)

                # Record equity
                open_pnl = sum(
                    (bar['Close'] - t.entry_price) * t.direction * t.shares
                    for t in self.open_positions
                    if t.symbol == bar.get('symbol', '')
                )
                self.equity_curve.append({
                    'date': bar_day,
                    'equity': self.capital + open_pnl,
                    'daily_pnl': self.day_state.daily_pnl if self.day_state else 0,
                })

            # 1. Process pending entries (fill at this bar's open)
            self._check_entries(bar)

            # 2. Check exits on current bar
            self._check_exits(bar, idx)

            # 3. Check for new signals
            if bar.get('signal', 0) != 0 and not pd.isna(bar.get('target_price')):
                self._queue_entry(bar)

        # Close any remaining positions
        if self.open_positions:
            last_bar = combined.iloc[-1]
            for trade in list(self.open_positions):
                self.open_positions.remove(trade)
                self._close_position(trade, last_bar['Close'], last_bar['Date'],
                                     'end_of_backtest', len(combined))

        # Build trade log
        trades = []
        for t in self.closed_trades:
            trades.append({
                'symbol': t.symbol,
                'direction': 'LONG' if t.direction == 1 else 'SHORT',
                'entry_time': t.entry_time,
                'entry_price': round(t.entry_price, 2),
                'exit_time': t.exit_time,
                'exit_price': round(t.exit_price, 2),
                'shares': t.shares,
                'exit_reason': t.exit_reason,
                'pnl_gross': round(t.pnl_gross, 2),
                'pnl_net': round(t.pnl_net, 2),
                'slippage_cost': round(t.slippage_cost, 2),
                'commission_cost': round(t.commission_cost, 2),
                'hold_bars': t.hold_bars,
            })

        trade_df = pd.DataFrame(trades)
        return trade_df

    def get_equity_curve(self) -> pd.DataFrame:
        return pd.DataFrame(self.equity_curve)


# ============================================================
# VISUALIZATION: Individual trade inspection
# ============================================================

def inspect_trades(trade_log: pd.DataFrame, data: Dict[str, pd.DataFrame], n: int = 10,
                   category: str = "random_winners"):
    """
    Plot a sample of individual trades overlaid on the day's price action.
    category: 'random_winners', 'random_losers', or 'worst'
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    OUTPUT_DIR.mkdir(exist_ok=True)

    if category == 'random_winners':
        pool = trade_log[trade_log['pnl_net'] > 0]
        title_prefix = "WINNING"
    elif category == 'random_losers':
        pool = trade_log[trade_log['pnl_net'] <= 0]
        title_prefix = "LOSING"
    elif category == 'worst':
        pool = trade_log.nsmallest(n, 'pnl_net')
        title_prefix = "WORST"
    else:
        pool = trade_log

    if len(pool) == 0:
        print(f"  No {category} trades to plot")
        return

    sample = pool.sample(min(n, len(pool)), random_state=42) if category != 'worst' else pool.head(n)

    fig, axes = plt.subplots(min(n, len(sample)), 1, figsize=(14, 4 * min(n, len(sample))))
    if min(n, len(sample)) == 1:
        axes = [axes]

    for ax_idx, (_, trade) in enumerate(sample.iterrows()):
        if ax_idx >= len(axes):
            break
        ax = axes[ax_idx]
        symbol = trade['symbol']

        if symbol not in data:
            continue

        df = data[symbol]
        entry_time = pd.to_datetime(trade['entry_time'])
        exit_time = pd.to_datetime(trade['exit_time'])
        trade_day = entry_time.date()

        day_df = df[df['trading_day'] == trade_day]
        if len(day_df) == 0:
            continue

        # Plot price
        x = range(len(day_df))
        ax.plot(x, day_df['Close'].values, 'k-', linewidth=1)

        # Plot VWAP
        if 'vwap' in day_df.columns:
            ax.plot(x, day_df['vwap'].values, 'b--', linewidth=0.7, alpha=0.5)

        # Plot OR
        or_high = day_df['or_high'].iloc[0]
        or_low = day_df['or_low'].iloc[0]
        if not pd.isna(or_high):
            ax.axhline(y=or_high, color='green', linestyle=':', linewidth=0.8, alpha=0.5)
            ax.axhline(y=or_low, color='red', linestyle=':', linewidth=0.8, alpha=0.5)
            ax.axhspan(or_low, or_high, alpha=0.08, color='blue')

        # Mark entry and exit
        entry_mask = abs(day_df['Date'] - entry_time).dt.total_seconds() < 300
        exit_mask = abs(day_df['Date'] - exit_time).dt.total_seconds() < 300

        if entry_mask.any():
            entry_idx = day_df[entry_mask].index[0] - day_df.index[0]
            color = 'green' if trade['direction'] == 'LONG' else 'red'
            marker = '^' if trade['direction'] == 'LONG' else 'v'
            ax.scatter(entry_idx, trade['entry_price'], color=color, marker=marker, s=120, zorder=5)

        if exit_mask.any():
            exit_idx = day_df[exit_mask].index[0] - day_df.index[0]
            ax.scatter(exit_idx, trade['exit_price'], color='black', marker='x', s=100, zorder=5)

        pnl_color = 'green' if trade['pnl_net'] > 0 else 'red'
        ax.set_title(f"{title_prefix}: {symbol} {trade['direction']} | "
                     f"PnL: ${trade['pnl_net']:+.2f} | "
                     f"Exit: {trade['exit_reason']} | "
                     f"Costs: ${trade['slippage_cost'] + trade['commission_cost']:.2f}",
                     fontsize=9, color=pnl_color)

        time_labels = day_df['Date'].dt.strftime('%H:%M').values
        tick_pos = range(0, len(day_df), max(1, len(day_df) // 8))
        ax.set_xticks(list(tick_pos))
        ax.set_xticklabels([time_labels[i] for i in tick_pos], rotation=45, fontsize=7)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / f'trades_{category}.png', dpi=150)
    plt.close()
    print(f"  Saved trades_{category}.png")


# ============================================================
# MAIN
# ============================================================

def run_phase4():
    """Load signals, run the backtester, print stats, and save trade charts."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from intraday_data import load_dataset, DEFAULT_UNIVERSE
    from orb_strategy import generate_orb_signals, compute_orb_features

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("PHASE 4: REALISTIC BACKTESTING")
    print("=" * 70)

    # Load data
    data = load_dataset(DEFAULT_UNIVERSE)
    print(f"Loaded {len(data)} symbols")

    # Generate signals
    print("Generating signals...")
    for symbol in data:
        data[symbol] = compute_orb_features(data[symbol])
        data[symbol] = generate_orb_signals(data[symbol])

    total_signals = sum((df['signal'] != 0).sum() for df in data.values())
    print(f"Total signals: {total_signals}")

    # Run backtest
    print("\nRunning backtest...")
    bt = Backtester()
    trade_log = bt.run(data)

    if len(trade_log) == 0:
        print("NO TRADES EXECUTED. Check signal generation and entry logic.")
        return

    # Save trade log
    trade_log.to_csv(OUTPUT_DIR / 'trade_log.csv', index=False)
    print(f"\nTrade log: {len(trade_log)} trades saved")

    # Quick stats (before Phase 5 deep analysis)
    winners = trade_log[trade_log['pnl_net'] > 0]
    losers = trade_log[trade_log['pnl_net'] <= 0]

    print(f"\n{'='*50}")
    print(f"QUICK STATS (full analysis in Phase 5)")
    print(f"{'='*50}")
    print(f"Total trades:     {len(trade_log)}")
    print(f"Winners:          {len(winners)} ({len(winners)/len(trade_log)*100:.1f}%)")
    print(f"Losers:           {len(losers)} ({len(losers)/len(trade_log)*100:.1f}%)")
    print(f"Gross PnL:        ${trade_log['pnl_gross'].sum():+,.2f}")
    print(f"Total costs:      ${(trade_log['slippage_cost'].sum() + trade_log['commission_cost'].sum()):,.2f}")
    print(f"Net PnL:          ${trade_log['pnl_net'].sum():+,.2f}")
    print(f"Avg winner:       ${winners['pnl_net'].mean():+,.2f}" if len(winners) > 0 else "")
    print(f"Avg loser:        ${losers['pnl_net'].mean():+,.2f}" if len(losers) > 0 else "")
    print(f"Profit factor:    {winners['pnl_net'].sum() / abs(losers['pnl_net'].sum()):.2f}" if len(losers) > 0 and losers['pnl_net'].sum() != 0 else "")
    print(f"Final capital:    ${bt.capital:,.2f}")
    print(f"{'='*50}")

    # Exit reason breakdown
    print(f"\nExit reasons:")
    for reason, count in trade_log['exit_reason'].value_counts().items():
        avg_pnl = trade_log[trade_log['exit_reason'] == reason]['pnl_net'].mean()
        print(f"  {reason:20s}: {count:4d} trades, avg PnL ${avg_pnl:+,.2f}")

    # Inspect trades visually (as required by Phase 4)
    print(f"\nGenerating trade charts...")
    inspect_trades(trade_log, data, n=10, category='random_winners')
    inspect_trades(trade_log, data, n=10, category='random_losers')
    inspect_trades(trade_log, data, n=5, category='worst')

    print(f"\nAll outputs saved to: {OUTPUT_DIR}")

    return trade_log, bt, data


if __name__ == "__main__":
    trade_log, bt, data = run_phase4()
