"""
Reconstruct v8.7 entry / stop / target from the documented formula.

v8.7 never stored entry_price, stop_loss, or take_profit. This module
applies the v8.2-era documented rules (and the Sep 2026 audit Section 4
R-multiple convention) to a v8.7 signal CSV that already has
entry_50, sw2 prices, direction, instrument, and entry_type.

This is Cursor's best-effort replica, not verified original v8.7 output.

Documented formula (NOT v8.8 current logic):
  - Entry: limit at 50% of the post-SMT FVG (`entry_50`) for BOTH
    SMT_IN_FVG and FVG_AFTER_SMT. v8.7's SMT_IN_FVG tag is context;
    the fill is still the post-SMT FVG 50% limit (v8.2 docstring).
  - Stop: swept_extreme +/- 20 ticks. Tick size 0.25 on ES and NQ, so
    5.0 points for BOTH instruments. v8.7-era scanners (v8.2–v8.7) do
    not contain stop code; this 5.0/5.0 rule is the documented one.
    v8.8 later floors NQ at 40 ticks / 10.0 pt — do not use that here.
  - Target: 3R, or 1.5R when entry_type is SMT_IN_FVG (audit Section 4).

swept_extreme is the confirming-instrument sw2 price (v8.7 sets
swept_extreme = sw2c[0] then strips the column from the CSV).
"""

from __future__ import annotations

from typing import Optional, Union

import pandas as pd

from backtest.simulate_fills import LIMIT_TYPES, simulate_fills

RECONSTRUCTION_LABEL = (
    "v8.7 RECONSTRUCTED from documented formula and fill-simulated on "
    "real data -- not v8.7's original (never-stored) values."
)

TICK_SIZE = 0.25
STOP_TICKS = 20  # documented v8.7: 20 ticks for ES and NQ
STOP_POINTS = STOP_TICKS * TICK_SIZE  # 5.0
SMT_IN_FVG_TP_R = 1.5
FVG_AFTER_SMT_TP_R = 3.0
SIM_LIMIT_TYPE = "FVG_AFTER_SMT"

# v8.7-era scanners never computed a stop. v8.8 NQ min-stop is 10.0.
STOP_RULE_FLAG = (
    "Documented v8.7 stop is 20 ticks x 0.25 = 5.0 pt for ES and NQ. "
    "No v8.7-era scanner (v8.2 through v8.7) stores or computes stop_loss; "
    "this 5.0/5.0 rule is reconstructed from the documented formula, not "
    "from code. v8.8 uses ES 20 ticks / 5.0 pt and NQ 40 ticks / 10.0 pt "
    "— that NQ floor is NOT applied here."
)


def swept_extreme_from_row(row: pd.Series) -> float:
    """Confirming-instrument sw2 price (v8.7 swept_extreme = sw2c[0])."""
    if "swept_extreme" in row.index and pd.notna(row["swept_extreme"]):
        return float(row["swept_extreme"])
    inst = str(row["instrument"]).upper()
    if inst in {"ES", "MES"}:
        col = "es_sw2_price"
    elif inst in {"NQ", "MNQ"}:
        col = "nq_sw2_price"
    else:
        raise ValueError(f"cannot reconstruct swept_extreme for instrument {inst}")
    if col not in row.index or pd.isna(row[col]):
        raise ValueError(f"signal missing {col}; cannot reconstruct swept_extreme")
    return float(row[col])


def target_r_for_entry_type(entry_type) -> float:
    et = "" if entry_type is None or (isinstance(entry_type, float) and pd.isna(entry_type)) else str(entry_type)
    if et == "SMT_IN_FVG":
        return SMT_IN_FVG_TP_R
    return FVG_AFTER_SMT_TP_R


def reconstruct_one(row: pd.Series) -> dict:
    """Return entry_price / stop_loss / take_profit plus reconstruction metadata."""
    if "entry_50" not in row.index or pd.isna(row["entry_50"]):
        raise ValueError("v8.7 signal missing entry_50; cannot reconstruct limit entry")
    direction = str(row["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction {direction}")

    entry = round(float(row["entry_50"]), 2)
    swept = round(swept_extreme_from_row(row), 2)
    if direction == "LONG":
        stop = round(swept - STOP_POINTS, 2)
    else:
        stop = round(swept + STOP_POINTS, 2)

    risk = abs(entry - stop)
    target_r = target_r_for_entry_type(row["entry_type"] if "entry_type" in row.index else None)
    if risk == 0:
        tp = entry
    elif direction == "LONG":
        tp = round(entry + target_r * risk, 2)
    else:
        tp = round(entry - target_r * risk, 2)

    inverted = (direction == "LONG" and entry <= stop) or (direction == "SHORT" and entry >= stop)
    return {
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": tp,
        "target_R": target_r,
        "swept_extreme": swept,
        "stop_offset_pts": STOP_POINTS,
        "risk_pts": round(risk, 2),
        "risk_inverted": inverted,
        "reconstruction_label": RECONSTRUCTION_LABEL,
    }


def reconstruct_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """Copy the v8.7 CSV and attach reconstructed levels. Does not fill-simulate."""
    out = signals.copy()
    rows = [reconstruct_one(row) for _, row in out.iterrows()]
    extra = pd.DataFrame(rows, index=out.index)
    for col in extra.columns:
        out[col] = extra[col]
    return out


def _limit_copy_for_sim(reconstructed: pd.DataFrame) -> pd.DataFrame:
    """
    v8.7 SMT_IN_FVG is still a 50% limit. simulate_fills() treats SMT_IN_FVG
    as a market fill at confirmation close, so force the limit path.
    """
    sim = reconstructed.copy()
    sim["entry_type"] = SIM_LIMIT_TYPE
    if "fvg_bar" not in sim.columns:
        raise ValueError("reconstructed v8.7 signals need fvg_bar to simulate the 50% limit")
    missing = sim["fvg_bar"].isna()
    if missing.any():
        raise ValueError(
            f"{int(missing.sum())} reconstructed rows missing fvg_bar; "
            "v8.7 limit entries require the post-SMT FVG bar"
        )
    return sim


def simulate_reconstructed_v87(
    signals: Union[pd.DataFrame, str],
    *,
    es_bars: Optional[pd.DataFrame] = None,
    nq_bars: Optional[pd.DataFrame] = None,
    bars: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Reconstruct documented v8.7 levels, then simulate_fills() as 50% limits."""
    src = pd.read_csv(signals) if not isinstance(signals, pd.DataFrame) else signals
    reconstructed = reconstruct_signals(src)
    original_type = reconstructed["entry_type"].copy()
    filled = simulate_fills(_limit_copy_for_sim(reconstructed), bars, es_bars=es_bars, nq_bars=nq_bars)
    filled["entry_type"] = original_type.to_numpy()
    filled["reconstruction_label"] = RECONSTRUCTION_LABEL
    return filled


assert STOP_POINTS == 5.0
assert SIM_LIMIT_TYPE in LIMIT_TYPES
