"""Shuffle the live-achievable v8.9 closed-bar set and assign random IDs.

May-Aug and Sep-Jan only. Order is a permutation with a fixed seed so any
completed prefix is a random sample. No fill outcomes are attached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from backtest.compare_versions import identity_key
from backtest.discretion_review.common import (
    BLIND_CSV_COLS,
    ID_SEED,
    SAMPLE_SEED,
    decision_bar_time,
    identity_tuple,
    random_ids,
    to_et,
)

RENDER_COLS = list(BLIND_CSV_COLS) + [
    "stop_loss",
    "smt_time",
    "fvg_bar",
    "fvg_low",
    "fvg_high",
    "entry_50",
    "pre_fvg_bar",
    "pre_fvg_low",
    "pre_fvg_high",
    "es_sw1_time",
    "es_sw1_price",
    "es_sw2_time",
    "es_sw2_price",
    "nq_sw1_time",
    "nq_sw1_price",
    "nq_sw2_time",
    "nq_sw2_price",
]

DEFAULT_SIGNALS = {
    "mayaug": Path("/tmp/waterfall/signals_closed/mayaug_v89_closed.csv"),
    "sepjan": Path("/tmp/waterfall/signals_closed/sepjan_v89_closed.csv"),
}


def load_closed_signals(
    paths: Optional[dict[str, Union[str, Path]]] = None,
) -> pd.DataFrame:
    paths = paths or DEFAULT_SIGNALS
    frames = []
    for window, path in paths.items():
        df = pd.read_csv(path)
        df["window"] = window
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    if out.duplicated(subset=["date", "sw2_conf_time", "direction", "instrument"]).any():
        raise ValueError("duplicate identities in the closed-bar signal set")
    return out


def filter_step5_to_windows(fills: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    wanted = {identity_key(r) for _, r in signals.iterrows()}
    mask = [identity_key(r) in wanted for _, r in fills.iterrows()]
    return fills.loc[mask].copy()


def assert_signals_in_fills(signals: pd.DataFrame, fills: pd.DataFrame) -> None:
    """Every closed-bar identity must exist in Step 5. Extra Step 5 rows (other windows) are expected."""
    sig_ids = {identity_tuple(r) for _, r in signals.iterrows()}
    fill_ids = {identity_tuple(r) for _, r in fills.iterrows()}
    missing = sig_ids - fill_ids
    if missing:
        raise AssertionError(
            f"{len(missing)} closed-bar identities missing from Step 5 fills"
        )


def shuffled_order(
    signals: pd.DataFrame,
    *,
    seed: int = SAMPLE_SEED,
    id_seed: int = ID_SEED,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(signals))
    ordered = signals.iloc[perm].copy().reset_index(drop=True)
    ordered.insert(0, "stored_order", np.arange(1, len(ordered) + 1))
    ordered.insert(1, "id", random_ids(len(ordered), seed=id_seed))
    ordered["decision_time"] = [decision_bar_time(r).isoformat() for _, r in ordered.iterrows()]
    ordered["sw2_conf_time"] = [
        to_et(t).isoformat() for t in ordered["sw2_conf_time"]
    ]
    return ordered


def _select_cols(ordered: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    missing = [c for c in cols if c not in ordered.columns]
    if missing:
        raise KeyError(f"ordered sample missing {missing}")
    return ordered.loc[:, cols].copy()


def blind_map(ordered: pd.DataFrame) -> pd.DataFrame:
    return _select_cols(ordered, list(BLIND_CSV_COLS))


def render_map(ordered: pd.DataFrame) -> pd.DataFrame:
    return _select_cols(ordered, RENDER_COLS)
