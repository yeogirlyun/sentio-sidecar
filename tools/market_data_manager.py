#!/usr/bin/env python3
"""
Market Data Manager - Append-Only Historical Market Data Database

A comprehensive tool for managing historical market data with the following guarantees:
1. **Never truncates existing data** - only expands the database
2. **No gaps except market holidays** - ensures data continuity
3. **Perfect 391-bar alignment** - every trading day has exactly 391 bars (9:30 AM - 4:00 PM ET)
4. **Auto date range adjustment** - extends requested range to align with trading days
5. **Intelligent merging** - combines new data with existing data seamlessly

This is a read-only database that keeps expanding as new market data becomes available
or historical data is backfilled for research purposes.

Usage:
    # Download new data (appends to existing)
    python3 market_data_manager.py --symbols TQQQ SQQQ --start 2025-10-20 --end 2025-10-24

    # Backfill historical data
    python3 market_data_manager.py --symbols TQQQ SQQQ --start 2025-09-01 --end 2025-09-30

    # Check database status
    python3 market_data_manager.py --status --symbols TQQQ SQQQ

    # List all symbols in database
    python3 market_data_manager.py --list
"""

import os
import argparse
import requests
import pandas as pd
import pandas_market_calendars as mcal
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# === Constants ===
RTH_START = "09:30"
RTH_END = "16:00"
NY_TIMEZONE = "America/New_York"
POLYGON_API_BASE = "https://api.polygon.io"
BARS_PER_DAY = 391  # 9:30 AM to 4:00 PM = 390 minutes + 1 initial bar
FILE_SUFFIX = "_RTH_NH"  # Regular Trading Hours, No Holidays


class MarketDataDB:
    """
    Manages append-only market data storage with perfect bar alignment.
    """

    def __init__(self, data_dir: str = "data/equities"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.nyse_calendar = mcal.get_calendar('NYSE')

    def _get_file_paths(self, symbol: str) -> Tuple[Path, Path]:
        """Returns (csv_path, bin_path) for a given symbol."""
        prefix = f"{symbol.upper()}{FILE_SUFFIX}"
        return (
            self.data_dir / f"{prefix}.csv",
            self.data_dir / f"{prefix}.bin"
        )

    def read_existing_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Reads existing data from CSV file if it exists.
        Returns None if file doesn't exist or is empty.
        """
        csv_path, _ = self._get_file_paths(symbol)

        if not csv_path.exists():
            return None

        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                return None

            # Parse the timestamp and set as index
            df['ts_utc'] = pd.to_datetime(df['ts_utc'])
            df.set_index('ts_utc', inplace=True)
            df.index = df.index.tz_convert(NY_TIMEZONE)

            print(f"✓ Existing data loaded: {len(df)} bars ({len(df)//BARS_PER_DAY} days)")
            return df

        except Exception as e:
            print(f"⚠ Warning: Could not read existing data for {symbol}: {e}")
            return None

    def get_date_range(self, df: Optional[pd.DataFrame]) -> Optional[Tuple[datetime, datetime]]:
        """Returns (start_date, end_date) of existing data, or None if no data."""
        if df is None or df.empty:
            return None
        return (df.index.min().date(), df.index.max().date())

    def fetch_from_polygon(self, symbol: str, start_date: str, end_date: str,
                          api_key: str, timespan: str = "minute", multiplier: int = 1) -> Optional[pd.DataFrame]:
        """
        Fetches aggregate bars from Polygon.io API with pagination support.
        """
        print(f"📡 Fetching '{symbol}' from Polygon.io ({start_date} to {end_date})...")

        url = (
            f"{POLYGON_API_BASE}/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/"
            f"{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
        )

        headers = {"Authorization": f"Bearer {api_key}"}
        all_bars = []

        while url:
            try:
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                data = response.json()

                if "results" in data:
                    all_bars.extend(data["results"])
                    print(f"   Fetched {len(all_bars)} bars...", end="\r")

                url = data.get("next_url")

            except requests.exceptions.RequestException as e:
                print(f"\n❌ API Error: {e}")
                return None
            except Exception as e:
                print(f"\n❌ Unexpected error: {e}")
                return None

        print(f"\n   ✓ Total bars fetched: {len(all_bars)}")

        if not all_bars:
            return None

        # Convert to DataFrame
        df = pd.DataFrame(all_bars)
        df.rename(columns={
            't': 'timestamp_utc_ms',
            'o': 'open',
            'h': 'high',
            'l': 'low',
            'c': 'close',
            'v': 'volume'
        }, inplace=True)

        return df

    def filter_and_align(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filters for RTH, removes holidays, and ensures perfect 391-bar alignment.
        """
        if df is None or df.empty:
            return None

        print("🔧 Processing: RTH filter + holiday removal + 391-bar alignment...")

        # Convert to NY timezone
        df['timestamp_utc_ms'] = pd.to_datetime(df['timestamp_utc_ms'], unit='ms', utc=True)
        df.set_index('timestamp_utc_ms', inplace=True)
        df.index = df.index.tz_convert(NY_TIMEZONE)

        # Filter for RTH (9:30 AM to 4:00 PM)
        df = df.between_time(RTH_START, RTH_END)

        # Remove market holidays
        holidays = self.nyse_calendar.holidays().holidays
        df = df[~df.index.normalize().isin(holidays)]

        print(f"   → {len(df)} bars after RTH/holiday filtering")

        # Create perfect 391-bar grid
        trading_days = df.index.normalize().unique()
        complete_index = pd.DatetimeIndex([], tz=NY_TIMEZONE)

        for day in trading_days:
            day_start = day.replace(hour=9, minute=30, second=0, microsecond=0)
            day_end = day.replace(hour=16, minute=0, second=0, microsecond=0)
            day_range = pd.date_range(start=day_start, end=day_end, freq='1min', tz=NY_TIMEZONE)
            complete_index = complete_index.union(day_range)

        # Reindex and forward-fill
        df_aligned = df.reindex(complete_index)
        df_aligned = df_aligned.ffill().bfill()

        # Verify alignment
        bars_per_day = df_aligned.groupby(df_aligned.index.date).size()
        misaligned_days = [date for date, count in bars_per_day.items() if count != BARS_PER_DAY]

        if misaligned_days:
            print(f"   ⚠ WARNING: {len(misaligned_days)} days with incorrect bar count:")
            for date in misaligned_days[:5]:  # Show first 5
                print(f"      {date}: {bars_per_day[date]} bars (expected {BARS_PER_DAY})")
        else:
            print(f"   ✓ Perfect alignment: {len(trading_days)} days × {BARS_PER_DAY} bars = {len(df_aligned)} bars")

        # Add required columns
        # ts_utc_str: ET-formatted timestamp string (for readability)
        df_aligned['ts_utc'] = df_aligned.index.strftime('%Y-%m-%dT%H:%M:%S%z').str.replace(
            r'([+-])(\d{2})(\d{2})', r'\1\2:\3', regex=True
        )
        # ts_epoch_utc: true UTC epoch seconds (for engine correctness)
        df_aligned['ts_epoch_utc'] = df_aligned.index.tz_convert('UTC').astype('int64') // 10**9

        return df_aligned

    def merge_data(self, existing: Optional[pd.DataFrame], new: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, int]]:
        """
        Intelligently merges new data with existing data.

        Returns:
            (merged_df, stats_dict) where stats contains:
                - 'existing_bars': Number of bars in existing data
                - 'new_bars': Number of bars in new fetch
                - 'added_bars': Number of bars actually added
                - 'overlapping_bars': Number of bars that already existed
        """
        stats = {
            'existing_bars': len(existing) if existing is not None else 0,
            'new_bars': len(new),
            'added_bars': 0,
            'overlapping_bars': 0,
            'start_date': None,
            'end_date': None
        }

        if existing is None or existing.empty:
            print("📥 No existing data - using all new data")
            stats['added_bars'] = len(new)
            stats['start_date'] = new.index.min().date()
            stats['end_date'] = new.index.max().date()
            return new, stats

        # Keep only OHLCV columns for merging (drop ts_utc/ts_nyt_epoch - will regenerate)
        ohlcv_cols = ['open', 'high', 'low', 'close', 'volume']
        existing_clean = existing[ohlcv_cols].copy() if existing is not None else None
        new_clean = new[ohlcv_cols].copy()

        # Combine and remove duplicates (keep new data for overlaps)
        combined = pd.concat([existing_clean, new_clean])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined = combined.sort_index()

        stats['added_bars'] = len(combined) - len(existing)
        stats['overlapping_bars'] = len(existing) + len(new) - len(combined)
        stats['start_date'] = combined.index.min().date()
        stats['end_date'] = combined.index.max().date()

        print(f"🔀 Merge complete:")
        print(f"   Existing: {stats['existing_bars']} bars")
        print(f"   Fetched:  {stats['new_bars']} bars")
        print(f"   Added:    {stats['added_bars']} bars")
        print(f"   Overlap:  {stats['overlapping_bars']} bars (updated)")
        print(f"   Total:    {len(combined)} bars")
        print(f"   Range:    {stats['start_date']} to {stats['end_date']}")

        return combined, stats

    def save_data(self, df: pd.DataFrame, symbol: str):
        """
        Saves DataFrame to both CSV and binary format.
        """
        csv_path, bin_path = self._get_file_paths(symbol)

        # Ensure ts_utc and ts_epoch_utc columns exist (regenerate if needed after merge)
        if 'ts_utc' not in df.columns or 'ts_epoch_utc' not in df.columns:
            df['ts_utc'] = df.index.strftime('%Y-%m-%dT%H:%M:%S%z').str.replace(
                r'([+-])(\d{2})(\d{2})', r'\1\2:\3', regex=True
            )
            df['ts_epoch_utc'] = df.index.tz_convert('UTC').astype('int64') // 10**9

        # Save CSV
        print(f"💾 Saving CSV to {csv_path}...")
        csv_columns = ['ts_utc', 'ts_epoch_utc', 'open', 'high', 'low', 'close', 'volume']
        df_to_save = df[csv_columns].copy()
        df_to_save.to_csv(csv_path, index=False)

        # Save binary
        print(f"💾 Saving binary to {bin_path}...")
        self._save_binary(df, bin_path)

        print(f"   ✓ Saved {len(df)} bars ({len(df)//BARS_PER_DAY} days)")

    def _save_binary(self, df: pd.DataFrame, path: Path):
        """
        Saves to C++ compatible binary format.
        """
        with open(path, 'wb') as f:
            # Write total bar count
            num_bars = len(df)
            f.write(struct.pack('<Q', num_bars))

            # Pack format: q (int64 UTC epoch), 4×d (double), Q (uint64)
            bar_struct = struct.Struct('<qddddQ')

            for row in df.itertuples():
                # Variable-length timestamp string
                ts_bytes = row.ts_utc.encode('utf-8')
                f.write(struct.pack('<I', len(ts_bytes)))
                f.write(ts_bytes)

                # Fixed-size OHLCV data
                packed = bar_struct.pack(
                    row.ts_epoch_utc,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    int(row.volume)
                )
                f.write(packed)

    def get_status(self, symbol: str) -> Dict:
        """Returns status information about a symbol's data."""
        csv_path, bin_path = self._get_file_paths(symbol)

        if not csv_path.exists():
            return {
                'symbol': symbol,
                'exists': False,
                'bars': 0,
                'days': 0,
                'start_date': None,
                'end_date': None,
                'csv_size_kb': 0,
                'bin_size_kb': 0
            }

        df = self.read_existing_data(symbol)

        return {
            'symbol': symbol,
            'exists': True,
            'bars': len(df),
            'days': len(df) // BARS_PER_DAY,
            'start_date': df.index.min().date() if df is not None else None,
            'end_date': df.index.max().date() if df is not None else None,
            'csv_size_kb': csv_path.stat().st_size // 1024,
            'bin_size_kb': bin_path.stat().st_size // 1024 if bin_path.exists() else 0
        }

    def list_all_symbols(self) -> List[str]:
        """Returns list of all symbols in the database."""
        symbols = []
        for csv_file in self.data_dir.glob(f"*{FILE_SUFFIX}.csv"):
            symbol = csv_file.stem.replace(FILE_SUFFIX, '')
            symbols.append(symbol)
        return sorted(symbols)

    def list_trading_days(self, symbol: str = None) -> List[str]:
        """
        Returns list of trading days (dates) available in the database.
        If symbol is specified, uses that symbol's data.
        If symbol is None, uses the first available symbol.
        Returns dates in YYYY-MM-DD format, sorted ascending.
        """
        if symbol is None:
            # Use first available symbol
            symbols = self.list_all_symbols()
            if not symbols:
                return []
            symbol = symbols[0]

        df = self.read_existing_data(symbol)
        if df is None or df.empty:
            return []

        # Get unique trading days
        trading_days = df.index.normalize().unique()
        dates = [day.strftime('%Y-%m-%d') for day in sorted(trading_days)]

        return dates

    def verify_integrity(self, symbols: List[str]) -> bool:
        """Checks that all symbols have identical day counts and bar counts (391 per day)."""
        ok = True
        reference_days = None
        reference_bars = None
        for symbol in symbols:
            df = self.read_existing_data(symbol)
            if df is None or df.empty:
                print(f"⚠ Integrity: {symbol} has no data")
                ok = False
                continue
            bars = len(df)
            days = bars // BARS_PER_DAY
            if bars != days * BARS_PER_DAY:
                print(f"⚠ Integrity: {symbol} bars {bars} not equal to {days}×{BARS_PER_DAY}")
                ok = False
            if reference_days is None:
                reference_days = days
                reference_bars = bars
            else:
                if days != reference_days or bars != reference_bars:
                    print(f"⚠ Integrity: {symbol} days/bars mismatch (days={days}, bars={bars}) vs ref (days={reference_days}, bars={reference_bars})")
                    ok = False
        if ok:
            print(f"✓ Integrity OK: {len(symbols)} symbols aligned to {reference_days} days × {BARS_PER_DAY} bars = {reference_bars} bars")
        return ok

    def _read_binary_file(self, bin_path: Path) -> Optional[pd.DataFrame]:
        """
        Reads a binary file and returns a DataFrame with the same structure as CSV.
        Returns None if file doesn't exist or can't be read.
        """
        if not bin_path.exists():
            return None

        try:
            with open(bin_path, 'rb') as f:
                # Read total bar count
                num_bars_bytes = f.read(8)
                if len(num_bars_bytes) != 8:
                    return None
                num_bars = struct.unpack('<Q', num_bars_bytes)[0]

                # Pack format: q (int64 UTC epoch), 4×d (double), Q (uint64)
                bar_struct = struct.Struct('<qddddQ')

                data = []
                for _ in range(num_bars):
                    # Variable-length timestamp string
                    ts_len_bytes = f.read(4)
                    if len(ts_len_bytes) != 4:
                        return None
                    ts_len = struct.unpack('<I', ts_len_bytes)[0]
                    ts_bytes = f.read(ts_len)
                    if len(ts_bytes) != ts_len:
                        return None
                    ts_str = ts_bytes.decode('utf-8')

                    # Fixed-size OHLCV data
                    bar_bytes = f.read(bar_struct.size)
                    if len(bar_bytes) != bar_struct.size:
                        return None
                    ts_epoch_utc, open_price, high, low, close, volume = bar_struct.unpack(bar_bytes)

                    data.append({
                        'ts_utc': ts_str,
                        'ts_epoch_utc': ts_epoch_utc,
                        'open': open_price,
                        'high': high,
                        'low': low,
                        'close': close,
                        'volume': volume
                    })

                df = pd.DataFrame(data)
                df['ts_utc'] = pd.to_datetime(df['ts_utc'])
                df.set_index('ts_utc', inplace=True)
                df.index = df.index.tz_convert(NY_TIMEZONE)

                return df

        except Exception as e:
            return None

    def sanity_check(self) -> bool:
        """
        Comprehensive data validation:
        1. Check all symbols have the same date range
        2. Verify each day has exactly 391 bars (09:30-16:00)
        3. Check both CSV and binary files exist and match
        4. Report detailed errors for any discrepancies

        Returns True if all checks pass, False otherwise.
        """
        print(f"\n{'='*70}")
        print(f"  SANITY CHECK - Comprehensive Data Validation")
        print(f"{'='*70}\n")

        symbols = self.list_all_symbols()
        if not symbols:
            print("❌ FAIL: No symbols found in database")
            return False

        print(f"📊 Found {len(symbols)} symbols: {', '.join(symbols)}\n")

        all_errors = []
        reference_date_range = None
        reference_days = None
        csv_issues = []
        bin_issues = []
        mismatch_issues = []

        for symbol in symbols:
            csv_path, bin_path = self._get_file_paths(symbol)

            # === CHECK 1: CSV File Validation ===
            if not csv_path.exists():
                all_errors.append(f"❌ {symbol}: CSV file missing")
                csv_issues.append(symbol)
                continue

            csv_df = self.read_existing_data(symbol)
            if csv_df is None or csv_df.empty:
                all_errors.append(f"❌ {symbol}: CSV file exists but is empty or unreadable")
                csv_issues.append(symbol)
                continue

            # Check CSV date range
            csv_date_range = self.get_date_range(csv_df)
            csv_start = csv_date_range[0]
            csv_end = csv_date_range[1]

            # Check CSV bar count alignment
            csv_bars = len(csv_df)
            csv_days = csv_bars // BARS_PER_DAY

            if csv_bars != csv_days * BARS_PER_DAY:
                all_errors.append(f"❌ {symbol} CSV: Total bars {csv_bars} ≠ {csv_days} days × {BARS_PER_DAY} bars")
                csv_issues.append(symbol)

            # Check each day has exactly 391 bars
            csv_per_day = csv_df.groupby(csv_df.index.date).size()
            csv_bad_days = [(str(d), int(c), csv_df[csv_df.index.date == d].index.min(), csv_df[csv_df.index.date == d].index.max())
                           for d, c in csv_per_day.items() if c != BARS_PER_DAY]

            if csv_bad_days:
                all_errors.append(f"❌ {symbol} CSV: {len(csv_bad_days)} days with incorrect bar count:")
                for date_str, count, first_time, last_time in csv_bad_days[:5]:  # Show first 5
                    all_errors.append(f"   • {date_str}: {count} bars (expected {BARS_PER_DAY}) "
                                    f"[{first_time.strftime('%H:%M:%S')} → {last_time.strftime('%H:%M:%S')}]")
                if len(csv_bad_days) > 5:
                    all_errors.append(f"   • ... and {len(csv_bad_days) - 5} more days")
                csv_issues.append(symbol)

            # === CHECK 2: Binary File Validation ===
            if not bin_path.exists():
                all_errors.append(f"❌ {symbol}: Binary file missing")
                bin_issues.append(symbol)
            else:
                bin_df = self._read_binary_file(bin_path)
                if bin_df is None:
                    all_errors.append(f"❌ {symbol}: Binary file exists but is unreadable or corrupted")
                    bin_issues.append(symbol)
                else:
                    # Check binary bar count alignment
                    bin_bars = len(bin_df)
                    bin_days = bin_bars // BARS_PER_DAY

                    if bin_bars != bin_days * BARS_PER_DAY:
                        all_errors.append(f"❌ {symbol} BIN: Total bars {bin_bars} ≠ {bin_days} days × {BARS_PER_DAY} bars")
                        bin_issues.append(symbol)

                    # Check each day has exactly 391 bars in binary
                    bin_per_day = bin_df.groupby(bin_df.index.date).size()
                    bin_bad_days = [(str(d), int(c), bin_df[bin_df.index.date == d].index.min(), bin_df[bin_df.index.date == d].index.max())
                                   for d, c in bin_per_day.items() if c != BARS_PER_DAY]

                    if bin_bad_days:
                        all_errors.append(f"❌ {symbol} BIN: {len(bin_bad_days)} days with incorrect bar count:")
                        for date_str, count, first_time, last_time in bin_bad_days[:5]:  # Show first 5
                            all_errors.append(f"   • {date_str}: {count} bars (expected {BARS_PER_DAY}) "
                                            f"[{first_time.strftime('%H:%M:%S')} → {last_time.strftime('%H:%M:%S')}]")
                        if len(bin_bad_days) > 5:
                            all_errors.append(f"   • ... and {len(bin_bad_days) - 5} more days")
                        bin_issues.append(symbol)

                    # === CHECK 3: CSV vs Binary Comparison ===
                    if csv_bars != bin_bars:
                        all_errors.append(f"❌ {symbol}: CSV/BIN bar count mismatch - CSV:{csv_bars} vs BIN:{bin_bars}")
                        mismatch_issues.append(symbol)

                    # Check date ranges match
                    bin_date_range = self.get_date_range(bin_df)
                    if csv_date_range != bin_date_range:
                        all_errors.append(f"❌ {symbol}: CSV/BIN date range mismatch")
                        all_errors.append(f"   • CSV: {csv_start} → {csv_end}")
                        all_errors.append(f"   • BIN: {bin_date_range[0]} → {bin_date_range[1]}")
                        mismatch_issues.append(symbol)

            # === CHECK 4: Cross-Symbol Date Range Consistency ===
            if reference_date_range is None:
                reference_date_range = csv_date_range
                reference_days = csv_days
            else:
                if csv_date_range != reference_date_range:
                    all_errors.append(f"❌ {symbol}: Date range differs from reference symbol")
                    all_errors.append(f"   • {symbol}: {csv_start} → {csv_end} ({csv_days} days)")
                    all_errors.append(f"   • Reference: {reference_date_range[0]} → {reference_date_range[1]} ({reference_days} days)")

        # === SUMMARY ===
        print(f"{'='*70}")
        print(f"  VALIDATION RESULTS")
        print(f"{'='*70}\n")

        if all_errors:
            print(f"❌ SANITY CHECK FAILED - Found {len(all_errors)} issues:\n")
            for error in all_errors:
                print(error)

            print(f"\n{'='*70}")
            print(f"  ERROR SUMMARY")
            print(f"{'='*70}")
            if csv_issues:
                print(f"CSV Issues: {len(set(csv_issues))} symbols - {', '.join(sorted(set(csv_issues)))}")
            if bin_issues:
                print(f"Binary Issues: {len(set(bin_issues))} symbols - {', '.join(sorted(set(bin_issues)))}")
            if mismatch_issues:
                print(f"CSV/BIN Mismatch: {len(set(mismatch_issues))} symbols - {', '.join(sorted(set(mismatch_issues)))}")
            print(f"{'='*70}\n")

            return False
        else:
            print(f"✅ ALL CHECKS PASSED!\n")
            print(f"Summary:")
            print(f"  • {len(symbols)} symbols validated")
            print(f"  • Date range: {reference_date_range[0]} → {reference_date_range[1]}")
            print(f"  • Trading days: {reference_days}")
            print(f"  • Bars per symbol: {reference_days * BARS_PER_DAY:,}")
            print(f"  • All days have exactly {BARS_PER_DAY} bars (09:30-16:00)")
            print(f"  • CSV and binary files match perfectly")
            print(f"\n{'='*70}\n")

            return True

    def get_global_date_range(self, symbols: List[str]) -> Optional[Tuple[datetime, datetime]]:
        """
        Returns the union of date ranges across all specified symbols.
        This ensures all symbols will have the same date range.
        """
        min_date = None
        max_date = None

        for symbol in symbols:
            df = self.read_existing_data(symbol)
            if df is not None and not df.empty:
                symbol_range = self.get_date_range(df)
                if symbol_range:
                    if min_date is None or symbol_range[0] < min_date:
                        min_date = symbol_range[0]
                    if max_date is None or symbol_range[1] > max_date:
                        max_date = symbol_range[1]

        if min_date and max_date:
            return (min_date, max_date)
        return None

    def update_symbol(self, symbol: str, start_date: str, end_date: str, api_key: str) -> bool:
        """
        Main method to update a symbol's data (fetch + merge + save).

        Returns:
            True if successful, False otherwise
        """
        print(f"\n{'='*70}")
        print(f"  Updating {symbol.upper()}")
        print(f"{'='*70}")

        # Step 1: Read existing data
        existing_df = self.read_existing_data(symbol)

        # Step 2: Determine optimal fetch range
        if existing_df is not None:
            existing_range = self.get_date_range(existing_df)
            print(f"📊 Existing range: {existing_range[0]} to {existing_range[1]}")
            print(f"📅 Requested range: {start_date} to {end_date}")

        # Step 3: Fetch new data
        raw_df = self.fetch_from_polygon(symbol, start_date, end_date, api_key)

        if raw_df is None or raw_df.empty:
            print(f"❌ No data fetched for {symbol}")
            return False

        # Step 4: Filter and align
        aligned_df = self.filter_and_align(raw_df)

        if aligned_df is None or aligned_df.empty:
            print(f"❌ No data remaining after filtering for {symbol}")
            return False

        # Step 5: Merge with existing
        merged_df, stats = self.merge_data(existing_df, aligned_df)

        # Step 6: Save
        self.save_data(merged_df, symbol)

        print(f"✅ {symbol} update complete!\n")
        return True

    def sync_all_symbols(self, symbols: List[str], api_key: str) -> Tuple[str, str]:
        """
        Ensures all symbols have the exact same date range.
        Returns the global (start_date, end_date) used for syncing.
        """
        print(f"\n{'='*70}")
        print(f"  Synchronizing {len(symbols)} symbols to same date range")
        print(f"{'='*70}\n")

        # Step 1: Find global date range across all symbols
        global_range = self.get_global_date_range(symbols)

        if global_range is None:
            print("ℹ️  No existing data found - will use requested range for all symbols")
            return None

        start_date = global_range[0].strftime('%Y-%m-%d')
        end_date = global_range[1].strftime('%Y-%m-%d')

        print(f"📊 Global date range detected: {start_date} to {end_date}")
        print(f"   Ensuring all {len(symbols)} symbols have this range...\n")

        # Step 2: Update each symbol to have the global range
        updated_count = 0
        for symbol in symbols:
            existing_df = self.read_existing_data(symbol)

            # Check if symbol already has the full range
            if existing_df is not None:
                existing_range = self.get_date_range(existing_df)
                if existing_range and existing_range[0] == global_range[0] and existing_range[1] == global_range[1]:
                    print(f"✓ {symbol}: Already synchronized ({start_date} to {end_date})")
                    continue

            # Symbol needs update
            if self.update_symbol(symbol, start_date, end_date, api_key):
                updated_count += 1

        print(f"\n{'='*70}")
        print(f"  Sync complete: {updated_count} symbols updated")
        print(f"  All symbols now have range: {start_date} to {end_date}")
        print(f"{'='*70}\n")

        return (start_date, end_date)


def main():
    parser = argparse.ArgumentParser(
        description="Market Data Manager - Append-only historical market data database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    # Data update commands
    parser.add_argument('--symbols', nargs='+', help="Symbols to update (e.g., TQQQ SQQQ)")
    parser.add_argument('--start', help="Start date (MM-DD, year is fixed to 2025)")
    parser.add_argument('--end', help="End date (MM-DD, year is fixed to 2025)")
    # Data directory is fixed to data/equities to avoid duplication and confusion

    # Sync commands
    parser.add_argument('--sync', action='store_true',
                       help="After updating, sync ALL symbols to same date range")
    parser.add_argument('--sync-only', action='store_true',
                       help="Sync all existing symbols to same date range (no new downloads)")
    parser.add_argument('--no-sync', action='store_true',
                       help="Skip automatic sync after updates (default is to sync all symbols)")

    # Show summary for all symbols
    parser.add_argument('--show', action='store_true',
                       help="Show all symbols with bars, days, start/end dates")

    # Query commands
    parser.add_argument('--status', action='store_true', help="Show status of specified symbols")
    parser.add_argument('--list', action='store_true', help="List all symbols in database")
    parser.add_argument('--list-dates', action='store_true', help="List all trading days available in database (one date per line)")

    # Symbols.conf sync
    parser.add_argument('--symbols-sync', action='store_true',
                       help="Sync data/equities with config/symbols.conf: add missing symbols, ensure equal date range and 391 bars/day, and remove extraneous symbols not in symbols.conf")

    # Sanity check
    parser.add_argument('--sanity-check', action='store_true',
                       help="Comprehensive data validation: check that all symbols have the same date range, exactly 391 bars per day, and verify both CSV and binary files match")

    args = parser.parse_args()

    # Convert MM-DD dates to YYYY-MM-DD (prepend "2025-")
    if args.start:
        if len(args.start) == 5 and args.start[2] == '-':
            args.start = "2025-" + args.start
    if args.end:
        if len(args.end) == 5 and args.end[2] == '-':
            args.end = "2025-" + args.end

    # Initialize database (fixed root)
    db = MarketDataDB("data/equities")

    # Helper: load symbols from config/symbols.conf (single source of truth)
    def load_symbols_from_conf() -> List[str]:
        conf_path = Path('config/symbols.conf')
        if not conf_path.exists():
            return []
        syms: List[str] = []
        for line in conf_path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            syms.append(s.upper())
        # Deduplicate, keep order stable-ish
        return sorted(set(syms))

    # Handle sanity check
    if args.sanity_check:
        success = db.sanity_check()
        return 0 if success else 1

    # Handle query commands
    if args.list:
        symbols = db.list_all_symbols()
        print(f"\n📚 Market Data Database (data/equities)")
        print(f"{'='*70}")
        if not symbols:
            print("  (empty)")
        else:
            print(f"  {len(symbols)} symbols: {', '.join(symbols)}")
        print(f"{'='*70}\n")
        return

    if args.list_dates:
        trading_days = db.list_trading_days()
        if not trading_days:
            print("No trading days found in database", flush=True)
            return
        # Output one date per line (for easy parsing by webapp)
        for date in trading_days:
            print(date, flush=True)
        return

    # Handle symbols-sync
    if args.symbols_sync:
        api_key = os.getenv('POLYGON_API_KEY')
        if not api_key:
            print("❌ Error: POLYGON_API_KEY environment variable not set")
            return

        # Read symbols.conf
        conf_path = Path('config/symbols.conf')
        if not conf_path.exists():
            print("❌ Error: config/symbols.conf not found")
            return
        desired = []
        for line in conf_path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith('#'): continue
            desired.append(s.upper())
        desired = sorted(set(desired))
        print(f"🔧 symbols-sync: target set from symbols.conf → {len(desired)} symbols")

        # Existing symbols in data dir
        existing = set(db.list_all_symbols())

        # Remove extraneous symbols not in symbols.conf
        extraneous = existing - set(desired)
        for sym in sorted(extraneous):
            csv_path, bin_path = db._get_file_paths(sym)
            try:
                if csv_path.exists(): csv_path.unlink()
                if bin_path.exists(): bin_path.unlink()
                print(f"🗑 Removed extraneous: {sym}")
            except Exception as e:
                print(f"⚠ Could not remove {sym}: {e}")

        # Add/update missing symbols
        # Find global range from any remaining symbol; if none exist, use a default range (last 30 NYSE sessions)
        remaining = [s for s in desired if s in db.list_all_symbols()]
        global_range = db.get_global_date_range(remaining) if remaining else None
        if global_range is None:
            # Build a default recent range
            end = datetime.now().date()
            start = end - timedelta(days=45)
            start_date = start.strftime('%Y-%m-%d')
            end_date = end.strftime('%Y-%m-%d')
            print(f"ℹ️ No existing symbols to infer range; using default {start_date} to {end_date}")
        else:
            start_date = global_range[0].strftime('%Y-%m-%d')
            end_date = global_range[1].strftime('%Y-%m-%d')
            print(f"📊 Using global range {start_date} to {end_date}")

        # Ensure each desired symbol exists and is aligned
        for sym in desired:
            if sym not in db.list_all_symbols():
                print(f"➕ Adding missing symbol {sym}")
                db.update_symbol(sym, start_date, end_date, api_key)
            else:
                # Ensure alignment and full bars
                df = db.read_existing_data(sym)
                if df is None or df.empty:
                    db.update_symbol(sym, start_date, end_date, api_key)
                else:
                    rng = db.get_date_range(df)
                    if not rng or rng[0].strftime('%Y-%m-%d') != start_date or rng[1].strftime('%Y-%m-%d') != end_date:
                        db.update_symbol(sym, start_date, end_date, api_key)

        # Final sync to ensure exact range and integrity across all desired symbols
        db.sync_all_symbols(desired, api_key)
        db.verify_integrity(desired)
        print("✅ symbols-sync complete")
        return

    if args.status:
        symbols = args.symbols
        if not symbols:
            # Default to symbols.conf when symbols are not provided
            symbols = load_symbols_from_conf()
            if not symbols:
                print("❌ Error: No symbols provided and config/symbols.conf is missing or empty")
                return

        print(f"\n📊 Database Status")
        print(f"{'='*70}")
        for symbol in symbols:
            status = db.get_status(symbol)
            if status['exists']:
                print(f"  {symbol}:")
                print(f"    Range: {status['start_date']} to {status['end_date']}")
                print(f"    Bars:  {status['bars']:,} ({status['days']} days)")
                print(f"    Size:  CSV={status['csv_size_kb']} KB, BIN={status['bin_size_kb']} KB")
            else:
                print(f"  {symbol}: (no data)")
        print(f"{'='*70}\n")
        return

    # Show summary for all symbols
    if args.show:
        symbols = db.list_all_symbols()
        print(f"\n📚 Market Data Summary (data/equities)")
        print(f"{'='*70}")
        if not symbols:
            print("  (empty)")
        else:
            for symbol in symbols:
                st = db.get_status(symbol)
                if st['exists']:
                    days = st['days']
                    bars = st['bars']
                    start = st['start_date']
                    end = st['end_date']
                    print(f"  {symbol}: {bars:,} bars ({days} days) | {start} → {end}")
                    # Per-day bar count validation (expect exactly 391)
                    try:
                        df = db.read_existing_data(symbol)
                        if df is not None and not df.empty:
                            per_day = df.groupby(df.index.date).size()
                            bad = [(str(d), int(c)) for d, c in per_day.items() if c != BARS_PER_DAY]
                            if bad:
                                print(f"    ⚠ Days with incorrect bar count (expected {BARS_PER_DAY}):")
                                # Print up to first 10 for brevity
                                for d, c in bad[:10]:
                                    print(f"      {d}: {c} bars")
                                if len(bad) > 10:
                                    print(f"      ... and {len(bad)-10} more")
                            else:
                                print(f"    ✓ All days have exactly {BARS_PER_DAY} bars")
                    except Exception as e:
                        print(f"    ⚠ Could not verify per-day bars: {e}")
                else:
                    print(f"  {symbol}: (no data)")
        print(f"{'='*70}\n")
        return

    # Handle sync-only command
    if args.sync_only:
        api_key = os.getenv('POLYGON_API_KEY')
        if not api_key:
            print("❌ Error: POLYGON_API_KEY environment variable not set")
            return

        # Prefer symbols.conf as source of truth; fallback to data directory
        all_symbols = load_symbols_from_conf() or db.list_all_symbols()
        if not all_symbols:
            print("❌ No symbols found in database")
            return

        db.sync_all_symbols(all_symbols, api_key)
        return

    # Handle data update commands
    if not args.start or not args.end:
        parser.print_help()
        print("\n❌ Error: Data update requires --start and --end")
        return

    # Get API key
    api_key = os.getenv('POLYGON_API_KEY')
    if not api_key:
        print("❌ Error: POLYGON_API_KEY environment variable not set")
        return

    # Resolve symbols: CLI list or default to symbols.conf
    symbols_for_update = args.symbols if args.symbols else load_symbols_from_conf()
    if not symbols_for_update:
        print("❌ Error: No symbols provided and config/symbols.conf is missing or empty")
        return

    # Update each symbol
    success_count = 0
    for symbol in symbols_for_update:
        if db.update_symbol(symbol, args.start, args.end, api_key):
            success_count += 1

    # Summary
    print(f"\n{'='*70}")
    print(f"  Update Summary: {success_count}/{len(symbols_for_update)} symbols successful")
    print(f"{'='*70}\n")

    # Automatic sync of all symbols unless skipped
    if success_count > 0 and not args.no_sync:
        all_symbols = db.list_all_symbols()
        if all_symbols:
            db.sync_all_symbols(all_symbols, api_key)
            # Integrity check after sync
            db.verify_integrity(all_symbols)


if __name__ == "__main__":
    main()
