"""Score a finished discretion review against Step 5 fills.

Run only after review. The HTML tool never imports this module.
Skipped and never-filled signals count as 0R.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.compare_versions import identity_key
from backtest.discretion_review.common import HAS_R, NEVER_FILLED, SAMPLE_SEED
from backtest.simulate_fills import GAP_DISCLOSURE

BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 42


def assigned_r(outcome, realized) -> float:
    if pd.isna(outcome):
        return 0.0
    o = str(outcome)
    if o in NEVER_FILLED:
        return 0.0
    if o in HAS_R:
        if realized is None or pd.isna(realized):
            return 0.0
        return float(realized)
    return 0.0


def attach_r(decisions: pd.DataFrame, id_map: pd.DataFrame, fills: pd.DataFrame) -> pd.DataFrame:
    id_map = id_map.copy()
    fills = fills.copy()
    decisions = decisions.copy()
    if "stored_order" not in decisions.columns:
        decisions = decisions.merge(id_map[["id", "stored_order"]], on="id", how="left")
    if "reason_label" not in decisions.columns:
        decisions["reason_label"] = ""
    if "reason_code" not in decisions.columns:
        decisions["reason_code"] = ""
    id_map["_key"] = [identity_key(r) for _, r in id_map.iterrows()]
    fills["_key"] = [identity_key(r) for _, r in fills.iterrows()]
    fill_r = fills.drop_duplicates("_key").set_index("_key")[["outcome", "realized_R"]]
    mapped = id_map.merge(fill_r, left_on="_key", right_index=True, how="left")
    work = decisions.merge(mapped, on="id", how="left", suffixes=("", "_map"))
    if "stored_order" not in work.columns and "stored_order_map" in work.columns:
        work["stored_order"] = work["stored_order_map"]
    if work["outcome"].isna().any():
        missing = work.loc[work["outcome"].isna(), "id"].tolist()
        raise SystemExit(f"decisions not joinable to Step 5 fills: {missing[:8]}")
    work["all_r"] = [
        assigned_r(o, r) for o, r in zip(work["outcome"], work["realized_R"])
    ]
    take = work["decision"].astype(str).str.lower() == "take"
    work["take_r"] = np.where(take, work["all_r"], 0.0)
    work["skip_r"] = np.where(~take, work["all_r"], np.nan)
    work["delta_r"] = work["take_r"] - work["all_r"]
    work["is_take"] = take
    return work.sort_values("stored_order").reset_index(drop=True)


def day_block_mean_ci(
    df: pd.DataFrame,
    col: str,
    *,
    n_boot: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    if df.empty:
        return float("nan"), float("nan"), float("nan")
    groups = [g[col].to_numpy(dtype=float) for _, g in df.groupby("date", sort=False)]
    rng = np.random.default_rng(seed)
    n_days = len(groups)
    means = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n_days, size=n_days)
        vals = np.concatenate([groups[j] for j in idx])
        means[i] = vals.mean() if len(vals) else 0.0
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(df[col].mean()), float(lo), float(hi)


def _fmt(x: float) -> str:
    if x != x:
        return "NA"
    return f"{x:+.3f}"


def summarize(work: pd.DataFrame, title: str) -> list[str]:
    n = len(work)
    n_take = int(work["is_take"].sum())
    n_skip = n - n_take
    all_sum = float(work["all_r"].sum())
    take_sum = float(work["take_r"].sum())
    skip_vals = work.loc[~work["is_take"], "all_r"]
    skip_sum = float(skip_vals.sum()) if len(skip_vals) else 0.0
    delta_mean, d_lo, d_hi = day_block_mean_ci(work, "delta_r")
    lines = [
        f"=== {title}  n={n}  takes={n_take}  skips={n_skip}  take_rate={n_take / n if n else float('nan'):.3f} ===",
        f"R if all taken:     sum {all_sum:+.3f}   mean {work['all_r'].mean() if n else float('nan'):+.3f}",
        f"R of takes only:    sum {take_sum:+.3f}   mean {work.loc[work['is_take'], 'all_r'].mean() if n_take else float('nan'):+.3f}  (skip=0 in take book: mean {work['take_r'].mean() if n else float('nan'):+.3f})",
        f"R of skips:         sum {skip_sum:+.3f}   mean {skip_vals.mean() if n_skip else float('nan'):+.3f}",
        f"takes-minus-all:    sum {take_sum - all_sum:+.3f}   mean {delta_mean:+.3f}   day-block 95% CI [{_fmt(d_lo)}, {_fmt(d_hi)}]  (seed {BOOTSTRAP_SEED}, {BOOTSTRAP_N} resamples)",
    ]
    return lines


def by_window_setup(work: pd.DataFrame) -> list[str]:
    lines = ["--- by window and setup ---"]
    for keys, g in work.groupby(["window", "entry_type"], sort=True):
        lines.extend(summarize(g, f"{keys[0]} {keys[1]}"))
    return lines


def by_skip_reason(work: pd.DataFrame) -> list[str]:
    skips = work.loc[~work["is_take"]].copy()
    lines = ["--- skip reasons (mean R of skipped signals; 0R if never filled) ---"]
    if skips.empty:
        lines.append("no skips")
        return lines
    skips["reason_label"] = skips["reason_label"].fillna("").replace("", "(no reason)")
    grp = skips.groupby("reason_label", dropna=False)["all_r"].agg(["count", "mean", "sum"])
    grp = grp.sort_values("count", ascending=False)
    for label, r in grp.iterrows():
        lines.append(f"  {label}: n={int(r['count'])}  meanR={r['mean']:+.3f}  sumR={r['sum']:+.3f}")
    all_mean = work["all_r"].mean()
    lines.append(f"  (reviewed-set all-taken mean R = {all_mean:+.3f}; a skip reason looks useful if its mean R is below that)")
    return lines


def half_split(work: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = work.sort_values("stored_order")
    cut = len(ordered) // 2
    return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()


def report(work: pd.DataFrame) -> str:
    first, second = half_split(work)
    parts = [
        GAP_DISCLOSURE.rstrip(),
        "Skipped and never-filled count as 0R. HAS_R outcomes (win/loss/no_fill_by_eod) use realized_R.",
        "Half-split is by stored_order among the reviewed rows only.",
        "",
        *summarize(work, "reviewed sample"),
        "",
        *by_window_setup(work),
        "",
        *by_skip_reason(work),
        "",
        *summarize(first, "FIRST HALF (discovery — which skip reasons look useful)"),
        *by_skip_reason(first),
        "",
        *summarize(second, "SECOND HALF (held-out check)"),
        *by_skip_reason(second),
        "",
        f"sample seed reference {SAMPLE_SEED}; bootstrap seed {BOOTSTRAP_SEED}",
    ]
    return "\n".join(parts) + "\n"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score a finished discretion review")
    p.add_argument("--decisions", type=Path, required=True)
    p.add_argument("--id-map", type=Path, required=True)
    p.add_argument("--fills", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    decisions = pd.read_csv(args.decisions)
    id_map = pd.read_csv(args.id_map)
    fills = pd.read_csv(args.fills)
    work = attach_r(decisions, id_map, fills)
    text = report(work)
    print(text, end="")
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
