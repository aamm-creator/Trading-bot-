"""Download historical OHLCV data from Binance via ccxt (paginated)."""
from __future__ import annotations

import os
import time

import ccxt
import pandas as pd

SYMBOL = "BTC/USDT"
TIMEFRAME = "15m"
TIMEFRAME_MS = 15 * 60 * 1000
LIMIT_PER_REQUEST = 1000  # Binance max per call

YEARS_BACK = 5


def main() -> None:
    exchange = ccxt.binance({"enableRateLimit": True})
    exchange.load_markets()

    since = exchange.milliseconds() - (YEARS_BACK * 365 * 24 * 60 * 60 * 1000)
    target = YEARS_BACK * 365 * 24 * 4  # ~175,200 candles

    all_rows: list[list] = []
    fetched = 0

    print(f"Downloading {SYMBOL} {TIMEFRAME} data from Binance...")
    print(f"Target: ~{target} candles ({YEARS_BACK} years)")

    while True:
        try:
            rows = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=LIMIT_PER_REQUEST)
        except Exception as e:
            print(f"Error: {e}, retrying in 5s...")
            time.sleep(5)
            continue

        if not rows:
            break

        all_rows.extend(rows)
        fetched += len(rows)
        since = rows[-1][0] + TIMEFRAME_MS

        if fetched % 10000 < LIMIT_PER_REQUEST:
            pct = min(100, fetched / target * 100)
            latest = pd.Timestamp(rows[-1][0], unit="ms", tz="UTC")
            print(f"  {fetched:,} candles ({pct:.0f}%) — latest: {latest}")

        if len(rows) < LIMIT_PER_REQUEST:
            break

        time.sleep(0.2)

    if not all_rows:
        print("No data!")
        return

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    os.makedirs("data", exist_ok=True)
    outfile = "data/btc_usdt_15m_5y.csv"
    df.to_csv(outfile, index=False)

    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    print(f"\nDone! Saved {len(df):,} candles to {outfile}")
    print(f"Date range: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]} ({days} days / {days/365:.1f} years)")


if __name__ == "__main__":
    main()
