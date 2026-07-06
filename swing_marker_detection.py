"""
Swing Marker Detection

Identifies swing highs and swing lows on OHLC bar data using a 4-bar pattern.
This is the same logic used across the scanner (5m/15m internal detection),
the Pine Script indicator, and the TradingView swing marker tool.

Pattern (swing high confirmed at bar[0]):
  - bar[1].high > bar[2].high   (bar[1] is higher than the bar before it)
  - bar[1].high > bar[3].high   (bar[1] is higher than two bars before it)
  - bar[1].high > bar[0].high   (bar[1] is higher than the current bar)

  The swing is AT bar[1], but only CONFIRMED when bar[0] closes.
  This means detection has a 1-bar lag — the swing exists at bar[1]
  but you don't know it until bar[0] prints.

Inverse logic for swing lows:
  - bar[1].low < bar[2].low
  - bar[1].low < bar[3].low
  - bar[1].low < bar[0].low

Usage:
  python swing_marker_detection.py <csv_path> [output_path]

  Input CSV must have columns: time, open, high, low, close
  Output adds Swing High and Swing Low columns (1 = swing, 0 = not)
"""

import pandas as pd
import sys


def detect_swings(df):
    """
    Detect swing highs and swing lows using the 4-bar pattern.

    Parameters:
        df: DataFrame with 'high' and 'low' columns

    Returns:
        DataFrame with 'Swing High' and 'Swing Low' columns added (1/0 flags).
        The flag is placed on the bar WHERE the swing occurs (bar[1] in the pattern),
        not the bar where it's confirmed (bar[0]).
    """
    h = df['high'].values
    l = df['low'].values
    n = len(df)

    swing_high = [0] * n
    swing_low = [0] * n

    for i in range(3, n):
        if h[i-1] > h[i-2] and h[i-1] > h[i-3] and h[i-1] > h[i]:
            swing_high[i-1] = 1

        if l[i-1] < l[i-2] and l[i-1] < l[i-3] and l[i-1] < l[i]:
            swing_low[i-1] = 1

    df = df.copy()
    df['Swing High'] = swing_high
    df['Swing Low'] = swing_low
    return df


def detect_swings_with_prices(df):
    """
    Returns lists of (timestamp, price, bar_index) tuples for programmatic use.

    Returns:
        (swing_highs, swing_lows) — each a list of tuples in chronological order.
    """
    h = df['high'].values
    l = df['low'].values
    n = len(df)
    times = df['time'].values if 'time' in df.columns else df.index

    swing_highs = []
    swing_lows = []

    for i in range(3, n):
        if h[i-1] > h[i-2] and h[i-1] > h[i-3] and h[i-1] > h[i]:
            swing_highs.append((times[i-1], float(h[i-1]), i-1))

        if l[i-1] < l[i-2] and l[i-1] < l[i-3] and l[i-1] < l[i]:
            swing_lows.append((times[i-1], float(l[i-1]), i-1))

    return swing_highs, swing_lows


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python swing_marker_detection.py <csv_path> [output_path]")
        print("  Input CSV must have columns: time, open, high, low, close")
        sys.exit(1)

    csv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else csv_path.replace('.csv', '_with_swings.csv')

    df = pd.read_csv(csv_path)
    df = detect_swings(df)

    print(f"Bars:        {len(df)}")
    print(f"Swing Highs: {int(df['Swing High'].sum())}")
    print(f"Swing Lows:  {int(df['Swing Low'].sum())}")

    df.to_csv(output_path, index=False)
    print(f"Saved → {output_path}")
