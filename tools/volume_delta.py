"""
Volume delta per 1-minute bar, built from 1-second bars.

Each 1-second bar's volume is classed as buying or selling by its direction
(the same bar-polarity rule TradingView's Volume Delta uses on intrabars):

  close > open                       -> buy
  close < open                       -> sell
  close == open, close > prev close  -> buy
  close == open, close < prev close  -> sell
  close == open == prev close        -> same side as the previous second

Per minute (bars labelled by their start time, like the 1-minute exports):

  open, high, low, close, Volume   rebuilt from the seconds
  Buy Vol, Sell Vol                volume classed as buying / selling
  Delta                            Buy Vol - Sell Vol
  Delta High, Delta Low            highest / lowest running delta within the
                                   minute (running delta starts at 0, so
                                   Delta High >= 0 >= Delta Low)
  Active Secs                      seconds in the minute that traded

This is an estimate of aggressive buying/selling from 1-second direction, not
true aggressor-side data from the order book.

  python3 -m tools.volume_delta IN_1s.csv OUT_1m.csv
"""

from __future__ import annotations

import argparse
from typing import Optional

import numpy as np
import pandas as pd

DELTA_COLUMNS = ["Buy Vol", "Sell Vol", "Delta", "Delta High", "Delta Low", "Active Secs"]


def second_polarity(df: pd.DataFrame) -> np.ndarray:
    """+1 buy / -1 sell for each 1-second bar (df sorted by time)."""
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    sign = np.sign(c - o)
    flat = sign == 0
    sign[flat] = np.sign(c[flat] - prev_c[flat])          # NaN for the very first bar
    sign = pd.Series(sign).replace(0, np.nan).ffill().fillna(1).to_numpy()
    return sign.astype(int)


def minute_delta(df_1s: pd.DataFrame) -> pd.DataFrame:
    """1-minute OHLCV + delta columns from 1-second bars (time, open, high, low, close, Volume)."""
    df = df_1s.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    sign = second_polarity(df)
    vol = df["Volume"].to_numpy(np.int64)
    df["_buy"] = np.where(sign > 0, vol, 0)
    df["_sell"] = np.where(sign < 0, vol, 0)
    df["_signed"] = sign * vol
    df["minute"] = df["time"].dt.floor("min")
    df["_run"] = df.groupby("minute")["_signed"].cumsum()
    g = df.groupby("minute", sort=True)
    out = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "Volume": g["Volume"].sum(),
        "Buy Vol": g["_buy"].sum(),
        "Sell Vol": g["_sell"].sum(),
        "Delta High": g["_run"].max().clip(lower=0),
        "Delta Low": g["_run"].min().clip(upper=0),
        "Active Secs": g.size(),
    })
    out["Delta"] = out["Buy Vol"] - out["Sell Vol"]
    out = out.reset_index().rename(columns={"minute": "time"})
    return out[["time", "open", "high", "low", "close", "Volume"] + DELTA_COLUMNS]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="1-minute OHLCV + volume delta from a 1-second CSV.")
    p.add_argument("input", help="1-second CSV or .csv.gz (time, open, high, low, close, Volume)")
    p.add_argument("output", help="Output 1-minute CSV")
    a = p.parse_args(argv)
    m = minute_delta(pd.read_csv(a.input))
    m["time"] = m["time"].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    m.to_csv(a.output, index=False)
    print(f"{len(m)} minutes -> {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
