"""
Intraday data pipeline — fetch, clean, and enrich 5-min OHLCV bars.

Uses yfinance by default (~59 days of 5-min bars). Set POLYGON_API_KEY
to pull 1-min history from polygon.io instead. Designed to be imported,
not run directly (though running it will refresh the cache).
"""

import os
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(__file__).parent.parent / "data" / "intraday"
REPORT_DIR = Path(__file__).parent.parent / "phase2_output"

# Top trending names from Phase 1 + high-volume neutral names
DEFAULT_UNIVERSE = [
    "NVDA", "TSLA", "MSFT", "AAPL", "AMZN", "GOOGL",
    "AVGO", "AMD", "PLTR", "TSM", "ORCL", "NFLX",
    "WMT", "JPM", "XOM", "UNH", "LRCX", "AMAT", "CRM", "HOOD",
]


# ============================================================
# DATA FETCHING
# ============================================================

def fetch_intraday_yfinance(
    symbol: str,
    interval: str = "5m",
    days: int = 59,
) -> pd.DataFrame:
    """Pull intraday bars from Yahoo Finance (5m: ~59 days max, 1m: ~7 days)."""
    end = datetime.now()
    start = end - timedelta(days=days)

    df = yf.download(
        symbol,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        interval=interval,
        progress=False,
    )

    if df.empty:
        raise ValueError(f"No data for {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    df = df.reset_index()
    if 'Datetime' in df.columns:
        df = df.rename(columns={'Datetime': 'Date'})

    df['Date'] = pd.to_datetime(df['Date'])
    df['symbol'] = symbol

    return df


def fetch_intraday_polygon(
    symbol: str,
    interval: str = "1m",
    days: int = 365,
) -> pd.DataFrame:
    """Pull 1-min bars from Polygon.io. Needs POLYGON_API_KEY in env."""
    api_key = os.environ.get('POLYGON_API_KEY')
    if not api_key:
        raise ValueError("Set POLYGON_API_KEY environment variable")

    try:
        import requests
    except ImportError:
        raise ImportError("pip install requests")

    end = datetime.now()
    start = end - timedelta(days=days)

    # Polygon uses multiplier/timespan format
    multiplier = 1
    timespan = "minute"

    all_bars = []
    current_start = start

    while current_start < end:
        chunk_end = min(current_start + timedelta(days=30), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/"
               f"{multiplier}/{timespan}/"
               f"{current_start.strftime('%Y-%m-%d')}/{chunk_end.strftime('%Y-%m-%d')}"
               f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}")

        resp = requests.get(url)
        data = resp.json()

        if data.get('results'):
            for bar in data['results']:
                all_bars.append({
                    'Date': pd.Timestamp(bar['t'], unit='ms', tz='US/Eastern'),
                    'Open': bar['o'],
                    'High': bar['h'],
                    'Low': bar['l'],
                    'Close': bar['c'],
                    'Volume': bar['v'],
                })

        current_start = chunk_end + timedelta(days=1)

    if not all_bars:
        raise ValueError(f"No Polygon data for {symbol}")

    df = pd.DataFrame(all_bars)
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
    df['symbol'] = symbol
    return df


def fetch_intraday(
    symbol: str,
    interval: str = "5m",
    days: int = 59,
    source: str = "auto",
) -> pd.DataFrame:
    """Fetch from Polygon if the key is set and interval is 1m, otherwise yfinance."""
    if source == "auto":
        if os.environ.get('POLYGON_API_KEY') and interval == "1m":
            source = "polygon"
        else:
            source = "yfinance"

    if source == "polygon":
        return fetch_intraday_polygon(symbol, interval=interval, days=days)
    else:
        return fetch_intraday_yfinance(symbol, interval=interval, days=days)


# ============================================================
# DATA CLEANING
# ============================================================

def clean_intraday(df: pd.DataFrame, symbol: str = "") -> Tuple[pd.DataFrame, dict]:
    """Strip bad bars and convert to ET. Returns (cleaned_df, quality_report)."""
    report = {
        "symbol": symbol,
        "raw_bars": len(df),
        "issues": [],
    }

    df = df.copy()
    df['Date'] = pd.to_datetime(df['Date'])

    # Convert to US/Eastern timezone (market hours are ET)
    if df['Date'].dt.tz is not None:
        df['Date'] = df['Date'].dt.tz_convert('US/Eastern').dt.tz_localize(None)
    else:
        # yfinance returns UTC-naive timestamps — treat as UTC and convert
        df['Date'] = df['Date'].dt.tz_localize('UTC').dt.tz_convert('US/Eastern').dt.tz_localize(None)

    # Extract time components
    df['hour'] = df['Date'].dt.hour
    df['minute'] = df['Date'].dt.minute
    df['time_decimal'] = df['hour'] + df['minute'] / 60
    df['trading_day'] = df['Date'].dt.date

    # 1. Remove pre-market and after-hours (keep 9:30 AM - 4:00 PM ET)
    pre_count = len(df)
    df = df[(df['time_decimal'] >= 9.5) & (df['time_decimal'] < 16.0)]
    removed = pre_count - len(df)
    if removed > 0:
        report['issues'].append(f"Removed {removed} pre/post-market bars")

    # 2. Remove zero/negative volume bars
    bad_vol = (df['Volume'] <= 0).sum()
    if bad_vol > 0:
        df = df[df['Volume'] > 0]
        report['issues'].append(f"Removed {bad_vol} zero-volume bars")

    # 3. Remove bars where OHLC is nonsensical
    bad_price = ((df['High'] < df['Low']) | (df['Close'] <= 0) | (df['Open'] <= 0)).sum()
    if bad_price > 0:
        df = df[(df['High'] >= df['Low']) & (df['Close'] > 0) & (df['Open'] > 0)]
        report['issues'].append(f"Removed {bad_price} bad-price bars")

    # 4. Flag extreme outlier bars (price > 5 sigma from 20-bar Bollinger)
    bb_mid = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    z_score = abs((df['Close'] - bb_mid) / (bb_std + 1e-10))
    outliers = (z_score > 5).sum()
    if outliers > 0:
        report['issues'].append(f"WARNING: {outliers} bars > 5 sigma from 20-bar mean")

    # 5. Check for missing bars / gaps per day
    days = df['trading_day'].unique()
    report['total_trading_days'] = len(days)

    # Expected bars per day (for 5-min: 78 bars, for 1-min: 390 bars)
    # Detect interval from data
    if len(df) > 1:
        typical_gap = df['Date'].diff().median().total_seconds()
        if typical_gap < 120:
            expected_bars_per_day = 390  # 1-min
            report['interval'] = '1m'
        else:
            expected_bars_per_day = 78  # 5-min
            report['interval'] = '5m'
    else:
        expected_bars_per_day = 78
        report['interval'] = '5m'

    bars_per_day = df.groupby('trading_day').size()
    low_bar_days = bars_per_day[bars_per_day < expected_bars_per_day * 0.7]
    if len(low_bar_days) > 0:
        report['issues'].append(f"{len(low_bar_days)} days with <70% expected bars (half-days/missing data)")
        report['low_bar_days'] = [str(d) for d in low_bar_days.index]

    # 6. Check for suspiciously low volume days
    daily_vol = df.groupby('trading_day')['Volume'].sum()
    vol_median = daily_vol.median()
    low_vol_days = daily_vol[daily_vol < vol_median * 0.1]
    if len(low_vol_days) > 0:
        report['issues'].append(f"{len(low_vol_days)} days with <10% of median volume")

    report['clean_bars'] = len(df)
    report['clean_trading_days'] = len(df['trading_day'].unique())

    # Reset index
    df = df.sort_values('Date').reset_index(drop=True)

    return df, report


# ============================================================
# STORAGE
# ============================================================

def save_parquet(df: pd.DataFrame, symbol: str):
    """Write a symbol's DataFrame to the parquet cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}.parquet"
    df.to_parquet(path, index=False)


def load_parquet(symbol: str) -> Optional[pd.DataFrame]:
    """Load a symbol from the parquet cache, or None if it doesn't exist yet."""
    path = DATA_DIR / f"{symbol}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None


# ============================================================
# RESAMPLING
# ============================================================

def resample_bars(df: pd.DataFrame, timeframe: str = "15min") -> pd.DataFrame:
    """Aggregate to a higher timeframe (e.g. '15min', '30min', '1h')."""
    df = df.copy()
    df = df.set_index('Date')

    # Only resample within each trading day
    resampled = df.groupby('trading_day').resample(timeframe).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
        'symbol': 'first',
    }).dropna(subset=['Open'])

    resampled = resampled.reset_index(level='trading_day', drop=True).reset_index()
    resampled['hour'] = resampled['Date'].dt.hour
    resampled['minute'] = resampled['Date'].dt.minute
    resampled['time_decimal'] = resampled['hour'] + resampled['minute'] / 60
    resampled['trading_day'] = resampled['Date'].dt.date

    # Filter to market hours
    resampled = resampled[
        (resampled['time_decimal'] >= 9.5) & (resampled['time_decimal'] < 16.0)
    ]

    return resampled.reset_index(drop=True)


# ============================================================
# DERIVED COLUMNS
# ============================================================

def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Compute intraday VWAP, reset each day."""
    df = df.copy()
    df['typical_price'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['tp_vol'] = df['typical_price'] * df['Volume']

    df['cum_tp_vol'] = df.groupby('trading_day')['tp_vol'].cumsum()
    df['cum_vol'] = df.groupby('trading_day')['Volume'].cumsum()
    df['vwap'] = df['cum_tp_vol'] / (df['cum_vol'] + 1e-10)

    df = df.drop(columns=['typical_price', 'tp_vol', 'cum_tp_vol', 'cum_vol'])
    return df


def add_cumulative_volume(df: pd.DataFrame) -> pd.DataFrame:
    """Add a running volume total that resets each day."""
    df = df.copy()
    df['cum_volume'] = df.groupby('trading_day')['Volume'].cumsum()
    return df


def add_time_bucket(df: pd.DataFrame) -> pd.DataFrame:
    """Label each bar with its 15-minute bucket (e.g. '10:00')."""
    df = df.copy()
    df['time_bucket'] = df['Date'].dt.floor('15min').dt.strftime('%H:%M')
    return df


def add_opening_range(df: pd.DataFrame, minutes: int = 30) -> pd.DataFrame:
    """Compute the first N-minute high/low for each day and join it back in."""
    df = df.copy()

    cutoff_time = 9.5 + (minutes / 60)  # 9:30 + N minutes

    # Get opening range per day
    or_data = df[df['time_decimal'] < cutoff_time].groupby('trading_day').agg(
        or_high=('High', 'max'),
        or_low=('Low', 'min'),
    )

    df = df.merge(or_data, on='trading_day', how='left')
    df['or_range'] = df['or_high'] - df['or_low']
    df['or_range_pct'] = df['or_range'] / ((df['or_high'] + df['or_low']) / 2) * 100
    df['above_or'] = (df['Close'] > df['or_high']).astype(int)
    df['below_or'] = (df['Close'] < df['or_low']).astype(int)
    df['dist_from_or_high'] = (df['Close'] - df['or_high']) / df['Close'] * 100
    df['dist_from_or_low'] = (df['Close'] - df['or_low']) / df['Close'] * 100

    return df


def add_prev_day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Add prior session's high, low, close, and volume to every bar."""
    df = df.copy()

    daily = df.groupby('trading_day').agg(
        prev_high=('High', 'max'),
        prev_low=('Low', 'min'),
        prev_close=('Close', 'last'),
        prev_volume=('Volume', 'sum'),
    ).reset_index()

    daily['prev_high'] = daily['prev_high'].shift(1)
    daily['prev_low'] = daily['prev_low'].shift(1)
    daily['prev_close'] = daily['prev_close'].shift(1)
    daily['prev_volume'] = daily['prev_volume'].shift(1)

    df = df.merge(daily[['trading_day', 'prev_high', 'prev_low', 'prev_close', 'prev_volume']],
                  on='trading_day', how='left')

    return df


def add_relative_volume(df: pd.DataFrame, lookback_days: int = 20) -> pd.DataFrame:
    """Compare each bar's volume to its 20-day rolling average at that same time of day."""
    df = df.copy()

    # Get average volume by time bucket over the lookback
    df['time_key'] = df['Date'].dt.strftime('%H:%M')
    avg_vol_by_time = df.groupby('time_key')['Volume'].transform(
        lambda x: x.rolling(lookback_days, min_periods=5).mean()
    )
    df['rel_volume'] = df['Volume'] / (avg_vol_by_time + 1)

    df = df.drop(columns=['time_key'])
    return df


def add_all_derived(df: pd.DataFrame, or_minutes: int = 30) -> pd.DataFrame:
    """Run all the enrichment steps in one shot."""
    df = add_vwap(df)
    df = add_cumulative_volume(df)
    df = add_time_bucket(df)
    df = add_opening_range(df, minutes=or_minutes)
    df = add_prev_day_levels(df)
    df = add_relative_volume(df)
    return df


# ============================================================
# BATCH PIPELINE
# ============================================================

def build_dataset(
    symbols: List[str] = DEFAULT_UNIVERSE,
    interval: str = "5m",
    days: int = 59,
    force_refresh: bool = False,
) -> Tuple[Dict[str, pd.DataFrame], list]:
    """
    Full pipeline for a list of symbols: fetch, clean, enrich, save.
    Returns (data_dict, quality_reports). Uses cache unless force_refresh=True.
    """

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    data = {}
    reports = []

    print(f"Building intraday dataset for {len(symbols)} symbols ({interval} bars)...\n")

    for symbol in symbols:
        # Check cache
        if not force_refresh:
            cached = load_parquet(symbol)
            if cached is not None and len(cached) > 100:
                data[symbol] = cached
                print(f"  {symbol}: {len(cached)} bars (cached)")
                reports.append({"symbol": symbol, "cached": True, "bars": len(cached)})
                continue

        try:
            # Fetch
            raw = fetch_intraday(symbol, interval=interval, days=days)

            # Clean
            cleaned, report = clean_intraday(raw, symbol)

            # Add derived columns
            enriched = add_all_derived(cleaned)

            # Save
            save_parquet(enriched, symbol)
            data[symbol] = enriched

            issues = report.get('issues', [])
            print(f"  {symbol}: {report['clean_bars']} bars, {report['clean_trading_days']} days"
                  + (f" ({len(issues)} issues)" if issues else ""))
            reports.append(report)

        except Exception as e:
            print(f"  {symbol}: FAILED - {e}")
            reports.append({"symbol": symbol, "error": str(e)})

    # Save validation report
    report_df = pd.DataFrame(reports)
    report_df.to_csv(REPORT_DIR / 'data_quality_report.csv', index=False)

    # Print summary
    print(f"\n{'='*50}")
    print(f"Dataset built: {len(data)} symbols")
    failed = [r for r in reports if 'error' in r]
    if failed:
        print(f"Failed: {[r['symbol'] for r in failed]}")

    issues_total = sum(len(r.get('issues', [])) for r in reports)
    print(f"Total data issues flagged: {issues_total}")

    # Flag critical issues
    for r in reports:
        if 'error' in r:
            continue
        for issue in r.get('issues', []):
            if 'WARNING' in issue or '5 sigma' in issue:
                print(f"  CRITICAL - {r['symbol']}: {issue}")

    print(f"{'='*50}")

    return data, reports


# ============================================================
# CONVENIENCE
# ============================================================

def load_dataset(symbols: List[str] = DEFAULT_UNIVERSE) -> Dict[str, pd.DataFrame]:
    """Load the full symbol set from parquet. Skips anything not yet cached."""
    data = {}
    for symbol in symbols:
        df = load_parquet(symbol)
        if df is not None:
            data[symbol] = df
    return data


def get_symbol_data(
    symbol: str,
    timeframe: str = "5min",
    with_derived: bool = True,
) -> pd.DataFrame:
    """Load one symbol from cache, optionally resampled and re-enriched."""
    df = load_parquet(symbol)
    if df is None:
        raise ValueError(f"No cached data for {symbol}. Run build_dataset() first.")

    if timeframe != "5min" and timeframe != "5m":
        df = resample_bars(df, timeframe)
        if with_derived:
            df = add_all_derived(df)

    return df


if __name__ == "__main__":
    data, reports = build_dataset(force_refresh=True)

    # Show sample for one symbol
    if data:
        symbol = list(data.keys())[0]
        df = data[symbol]
        print(f"\nSample columns for {symbol}:")
        print(f"  {list(df.columns)}")
        print(f"\nLast 5 rows:")
        print(df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume',
                   'vwap', 'or_high', 'or_low', 'rel_volume']].tail())
