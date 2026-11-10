"""
Opening Range Breakout signal generation.

Hypothesis: stocks that break the first 30-min range with volume behind
them tend to keep going. Entry on close above/below OR with 1.2x rel vol,
target 1.5x OR range, stop 0.75x (2:1 R:R). Morning session only.
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "phase3_output"


# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_orb_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tag each bar with a signal if it triggers an ORB entry.

    Expects the standard intraday pipeline columns (or_high, or_low,
    or_range, rel_volume, time_decimal, trading_day). Returns the same
    DataFrame with signal, signal_type, target_price, stop_price, and
    entry_reason columns added.
    """
    df = df.copy()

    # Initialize signal columns
    df['signal'] = 0
    df['signal_type'] = None
    df['target_price'] = np.nan
    df['stop_price'] = np.nan
    df['entry_reason'] = None

    # Parameters
    OR_MINUTES = 30           # Opening range period
    VOLUME_THRESHOLD = 1.2    # Minimum relative volume for confirmation
    ENTRY_START = 10.0        # Earliest entry (10:00 AM ET)
    ENTRY_END = 14.5          # Latest entry (2:30 PM ET)
    TARGET_MULT = 1.5         # Target = 1.5x OR range from entry
    STOP_MULT = 0.75          # Stop = 0.75x OR range from entry
    CLOSE_TIME = 15.75        # Flatten by 3:45 PM ET

    for day in df['trading_day'].unique():
        day_mask = df['trading_day'] == day
        day_df = df[day_mask]

        if len(day_df) < 10:
            continue

        # Get OR for this day
        or_high = day_df['or_high'].iloc[0]
        or_low = day_df['or_low'].iloc[0]
        or_range = day_df['or_range'].iloc[0]

        if pd.isna(or_high) or pd.isna(or_low) or or_range <= 0:
            continue

        # Skip if OR range is too narrow (likely low-vol day)
        or_range_pct = or_range / ((or_high + or_low) / 2) * 100
        if or_range_pct < 0.3:
            continue

        long_triggered = False
        short_triggered = False

        for idx in day_df.index:
            row = df.loc[idx]
            time = row['time_decimal']

            # Only look for entries in the allowed window
            if time < ENTRY_START or time > ENTRY_END:
                continue

            # Skip if we already triggered this direction today
            rel_vol = row.get('rel_volume', 1.0)
            if pd.isna(rel_vol):
                rel_vol = 1.0

            # LONG BREAKOUT: Close above OR high with volume
            if not long_triggered and row['Close'] > or_high and rel_vol >= VOLUME_THRESHOLD:
                entry_price = row['Close']
                target = entry_price + (or_range * TARGET_MULT)
                stop = entry_price - (or_range * STOP_MULT)

                df.loc[idx, 'signal'] = 1
                df.loc[idx, 'signal_type'] = 'long_breakout'
                df.loc[idx, 'target_price'] = target
                df.loc[idx, 'stop_price'] = stop
                df.loc[idx, 'entry_reason'] = (
                    f"Close ${entry_price:.2f} > OR High ${or_high:.2f}, "
                    f"RelVol {rel_vol:.1f}x, OR Range {or_range_pct:.1f}%"
                )
                long_triggered = True

            # SHORT BREAKOUT: Close below OR low with volume
            if not short_triggered and row['Close'] < or_low and rel_vol >= VOLUME_THRESHOLD:
                entry_price = row['Close']
                target = entry_price - (or_range * TARGET_MULT)
                stop = entry_price + (or_range * STOP_MULT)

                df.loc[idx, 'signal'] = -1
                df.loc[idx, 'signal_type'] = 'short_breakout'
                df.loc[idx, 'target_price'] = target
                df.loc[idx, 'stop_price'] = stop
                df.loc[idx, 'entry_reason'] = (
                    f"Close ${entry_price:.2f} < OR Low ${or_low:.2f}, "
                    f"RelVol {rel_vol:.1f}x, OR Range {or_range_pct:.1f}%"
                )
                short_triggered = True

    return df


# ============================================================
# FEATURE SET
# ============================================================

def compute_orb_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add ORB-relevant features (VWAP distance, momentum, vol surge, gap %).
    Mostly for analysis; could be used for ML filtering down the road."""
    df = df.copy()

    # Distance from VWAP (mean reversion pressure)
    if 'vwap' in df.columns:
        df['dist_from_vwap_pct'] = (df['Close'] - df['vwap']) / df['vwap'] * 100

    # Distance from OR boundaries
    df['dist_from_or_high_pct'] = (df['Close'] - df['or_high']) / df['Close'] * 100
    df['dist_from_or_low_pct'] = (df['Close'] - df['or_low']) / df['Close'] * 100

    # How much of the OR range has been consumed
    df['or_consumed_pct'] = (df['Close'] - df['or_low']) / (df['or_range'] + 1e-10) * 100

    # Momentum into breakout (5-bar rate of change)
    df['momentum_5bar'] = df['Close'].pct_change(5) * 100

    # Volume surge (current bar vs 5-bar average)
    df['vol_surge'] = df['Volume'] / (df['Volume'].rolling(5).mean() + 1)

    # Gap from previous close
    if 'prev_close' in df.columns:
        df['gap_pct'] = np.nan
        for day in df['trading_day'].unique():
            mask = df['trading_day'] == day
            first_idx = df[mask].index[0]
            prev_close = df.loc[first_idx, 'prev_close']
            if not pd.isna(prev_close) and prev_close > 0:
                open_price = df.loc[first_idx, 'Open']
                df.loc[mask, 'gap_pct'] = (open_price - prev_close) / prev_close * 100

    # ATR proxy (5-bar range)
    df['range_5bar'] = (df['High'].rolling(5).max() - df['Low'].rolling(5).min()) / df['Close'] * 100

    return df


# ============================================================
# VISUAL EXAMPLES
# ============================================================

def plot_signal_examples(df: pd.DataFrame, symbol: str, n_examples: int = 3):
    """Save a few example signal charts to disk for a quick sanity check."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    OUTPUT_DIR.mkdir(exist_ok=True)

    signal_bars = df[df['signal'] != 0]
    if len(signal_bars) == 0:
        print(f"  No signals for {symbol}")
        return

    # Pick examples: first, middle, last
    indices = [0, len(signal_bars) // 2, len(signal_bars) - 1]
    indices = [min(i, len(signal_bars) - 1) for i in indices]

    fig, axes = plt.subplots(len(indices), 1, figsize=(14, 5 * len(indices)))
    if len(indices) == 1:
        axes = [axes]

    for ax_idx, sig_idx in enumerate(indices):
        sig_row = signal_bars.iloc[sig_idx]
        day = sig_row['trading_day']

        # Get full day data
        day_df = df[df['trading_day'] == day].copy()
        if len(day_df) < 5:
            continue

        ax = axes[ax_idx]

        # Plot price
        x = range(len(day_df))
        ax.plot(x, day_df['Close'].values, 'k-', linewidth=1, label='Price')

        # Plot VWAP
        if 'vwap' in day_df.columns:
            ax.plot(x, day_df['vwap'].values, 'b--', linewidth=0.8, alpha=0.6, label='VWAP')

        # Plot OR range
        or_high = day_df['or_high'].iloc[0]
        or_low = day_df['or_low'].iloc[0]
        if not pd.isna(or_high):
            ax.axhline(y=or_high, color='green', linestyle=':', linewidth=1, alpha=0.7, label='OR High')
            ax.axhline(y=or_low, color='red', linestyle=':', linewidth=1, alpha=0.7, label='OR Low')
            ax.axhspan(or_low, or_high, alpha=0.1, color='blue', label='Opening Range')

        # Mark signal
        sig_bar_idx = day_df.index.get_loc(signal_bars.index[sig_idx])
        entry_price = sig_row['Close']
        target = sig_row['target_price']
        stop = sig_row['stop_price']

        color = 'green' if sig_row['signal'] == 1 else 'red'
        marker = '^' if sig_row['signal'] == 1 else 'v'
        ax.scatter(sig_bar_idx, entry_price, color=color, marker=marker, s=150, zorder=5, label='Entry')

        if not pd.isna(target):
            ax.axhline(y=target, color='green', linestyle='--', linewidth=0.8, alpha=0.5, label=f'Target ${target:.2f}')
        if not pd.isna(stop):
            ax.axhline(y=stop, color='red', linestyle='--', linewidth=0.8, alpha=0.5, label=f'Stop ${stop:.2f}')

        # X-axis labels
        time_labels = day_df['Date'].dt.strftime('%H:%M').values
        tick_positions = range(0, len(day_df), max(1, len(day_df) // 8))
        ax.set_xticks(list(tick_positions))
        ax.set_xticklabels([time_labels[i] for i in tick_positions], rotation=45)

        direction = 'LONG' if sig_row['signal'] == 1 else 'SHORT'
        ax.set_title(f"{symbol} — {day} — {direction} Breakout\n{sig_row['entry_reason']}", fontsize=10)
        ax.legend(fontsize=7, loc='best')
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / f'{symbol}_orb_examples.png', dpi=150)
    plt.close()
    print(f"  Saved {symbol}_orb_examples.png")


# ============================================================
# MAIN
# ============================================================

def run_phase3(symbols: list = None):
    """Generate ORB signals for all symbols and save example charts."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from intraday_data import load_dataset, DEFAULT_UNIVERSE

    if symbols is None:
        symbols = DEFAULT_UNIVERSE

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("PHASE 3: OPENING RANGE BREAKOUT — SIGNAL GENERATION")
    print("=" * 70)

    data = load_dataset(symbols)
    print(f"Loaded {len(data)} symbols\n")

    all_signals = []
    signal_summary = []

    for symbol, df in data.items():
        # Generate signals
        df = compute_orb_features(df)
        df = generate_orb_signals(df)

        # Count signals
        longs = (df['signal'] == 1).sum()
        shorts = (df['signal'] == -1).sum()
        total = longs + shorts
        days = len(df['trading_day'].unique())

        signal_summary.append({
            'symbol': symbol,
            'trading_days': days,
            'long_signals': longs,
            'short_signals': shorts,
            'total_signals': total,
            'signals_per_day': round(total / days, 2) if days > 0 else 0,
        })

        print(f"  {symbol}: {longs} longs, {shorts} shorts ({total} total, "
              f"{total/days:.1f}/day)")

        # Save signal data
        signal_bars = df[df['signal'] != 0]
        if len(signal_bars) > 0:
            all_signals.append(signal_bars)

        # Plot examples
        if total > 0:
            plot_signal_examples(df, symbol)

        # Save enriched data
        data[symbol] = df

    # Summary
    summary_df = pd.DataFrame(signal_summary)
    summary_df.to_csv(OUTPUT_DIR / 'signal_summary.csv', index=False)

    total_signals = summary_df['total_signals'].sum()
    total_days = summary_df['trading_days'].max()

    print(f"\n{'='*70}")
    print(f"SIGNAL SUMMARY")
    print(f"{'='*70}")
    print(f"Total signals across all symbols: {total_signals}")
    print(f"Average signals per symbol per day: {summary_df['signals_per_day'].mean():.2f}")
    print(f"Long signals: {summary_df['long_signals'].sum()}")
    print(f"Short signals: {summary_df['short_signals'].sum()}")

    # Save all signal bars for inspection
    if all_signals:
        all_sig_df = pd.concat(all_signals)
        all_sig_df.to_csv(OUTPUT_DIR / 'all_signals.csv', index=False)
        print(f"\nAll signals saved to {OUTPUT_DIR / 'all_signals.csv'}")

    print(f"{'='*70}")

    return data, summary_df


if __name__ == "__main__":
    data, summary = run_phase3()
