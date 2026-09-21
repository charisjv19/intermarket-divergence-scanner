"""
Automated P&L report from simulate_fills() output.

Sep 2026 audit Section 4 methodology: Wilson 95% CI on win rate,
bootstrap 95% CI on expectancy, profit factor, one-sided binomial
test vs 25% breakeven. Uses realized_R, never target_R.

no_fill_never_traded / no_fill_timeout / no_fill_50pct are excluded
from WR/expectancy (no position). Two views of no_fill_by_eod:
  (1) excluded  (2) included as 0R breakeven
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest.simulate_fills import GAP_DISCLOSURE, simulate_fills

RESOLVED = ("win", "loss")
EOD = "no_fill_by_eod"
NO_POS = ("no_fill_never_traded", "no_fill_timeout", "no_fill_50pct")
Z95 = 1.959963984540054
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 42
BREAKEVEN_P = 0.25


def wilson_ci(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n)) / denom
    return center - margin, center + margin


def binom_sf_ge(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). One-sided greater-than p-value."""
    if n <= 0:
        return float("nan")
    k = max(0, min(k, n))
    total = 0.0
    logp = math.log(p) if p > 0 else float("-inf")
    logq = math.log(1.0 - p) if p < 1 else float("-inf")
    for i in range(k, n + 1):
        logc = math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        total += math.exp(logc + i * logp + (n - i) * logq)
    return min(1.0, max(0.0, total))


def bootstrap_mean_ci(values: np.ndarray, n_boot: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(n_boot, len(values)), replace=True).mean(axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(values.mean()), float(lo), float(hi)


def profit_factor(values: np.ndarray) -> float:
    gains = values[values > 0].sum()
    losses = values[values < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / abs(losses))


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return float("nan")
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    return float(dd.min())


def _sample(filled: pd.DataFrame, treat_eod_as_zero: bool) -> pd.DataFrame:
    work = filled.copy()
    if treat_eod_as_zero:
        mask = work["outcome"].isin(RESOLVED) | (work["outcome"] == EOD)
        out = work.loc[mask].copy()
        eod = out["outcome"] == EOD
        out.loc[eod, "realized_R"] = 0.0
        return out
    return work.loc[work["outcome"].isin(RESOLVED)].copy()


def compute_stats(filled: pd.DataFrame, *, treat_eod_as_zero: bool = False) -> dict:
    sample = _sample(filled, treat_eod_as_zero)
    n = len(sample)
    wins = int((sample["outcome"] == "win").sum()) if n else 0
    losses = int((sample["outcome"] == "loss").sum()) if n else 0
    eod_n = int((sample["outcome"] == EOD).sum()) if n else 0
    wr = wins / n if n else float("nan")
    wr_lo, wr_hi = wilson_ci(wins, n)
    rs = sample["realized_R"].astype(float).to_numpy() if n else np.array([])
    exp, exp_lo, exp_hi = bootstrap_mean_ci(rs)
    p_gt = binom_sf_ge(wins, n, BREAKEVEN_P)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "eod_as_zero": eod_n,
        "win_rate": wr,
        "win_rate_ci_lo": wr_lo,
        "win_rate_ci_hi": wr_hi,
        "expectancy": exp,
        "expectancy_ci_lo": exp_lo,
        "expectancy_ci_hi": exp_hi,
        "profit_factor": profit_factor(rs),
        "binom_p_gt_25": p_gt,
    }


def funnel(filled: pd.DataFrame) -> pd.DataFrame:
    n = len(filled)
    order = [
        "win",
        "loss",
        "no_fill_timeout",
        "no_fill_50pct",
        "no_fill_never_traded",
        "no_fill_by_eod",
    ]
    rows = []
    counts = filled["outcome"].value_counts()
    for o in order:
        c = int(counts.get(o, 0))
        rows.append({"outcome": o, "count": c, "pct": (100.0 * c / n) if n else 0.0})
    extra = [o for o in counts.index if o not in order]
    for o in extra:
        c = int(counts[o])
        rows.append({"outcome": o, "count": c, "pct": (100.0 * c / n) if n else 0.0})
    return pd.DataFrame(rows)


def equity_curve(filled: pd.DataFrame, *, treat_eod_as_zero: bool = False) -> pd.DataFrame:
    sample = _sample(filled, treat_eod_as_zero)
    if sample.empty:
        return pd.DataFrame(columns=["i", "when", "realized_R", "equity"])
    when = pd.to_datetime(sample["exit_time"], utc=True, errors="coerce")
    if when.isna().all() and "smt_time" in sample.columns:
        when = pd.to_datetime(sample["smt_time"], utc=True, errors="coerce")
    sample = sample.assign(_when=when).sort_values("_when")
    r = sample["realized_R"].astype(float)
    eq = r.cumsum()
    return pd.DataFrame(
        {
            "i": np.arange(1, len(sample) + 1),
            "when": sample["_when"].to_numpy(),
            "realized_R": r.to_numpy(),
            "equity": eq.to_numpy(),
            "outcome": sample["outcome"].to_numpy(),
        }
    )


def _fmt_pct(x) -> str:
    if x != x:
        return "nan"
    return f"{100.0 * x:.1f}%"


def _fmt_r(x) -> str:
    if x != x:
        return "nan"
    if math.isinf(x):
        return "inf"
    return f"{x:.3f}R"


def _fmt_ci(lo, hi, pct=False) -> str:
    if lo != lo or hi != hi:
        return "nan"
    if pct:
        return f"[{100.0 * lo:.1f}%, {100.0 * hi:.1f}%]"
    return f"[{lo:.3f}, {hi:.3f}]"


def _stats_block(title: str, s: dict) -> str:
    lines = [
        title,
        f"  n={s['n']}  wins={s['wins']}  losses={s['losses']}"
        + (f"  eod_as_0R={s['eod_as_zero']}" if s["eod_as_zero"] else ""),
        f"  win rate: {_fmt_pct(s['win_rate'])}  Wilson 95% CI {_fmt_ci(s['win_rate_ci_lo'], s['win_rate_ci_hi'], pct=True)}",
        f"  expectancy: {_fmt_r(s['expectancy'])}  bootstrap 95% CI {_fmt_ci(s['expectancy_ci_lo'], s['expectancy_ci_hi'])}",
        f"  profit factor: {_fmt_r(s['profit_factor']).replace('R', '')}",
        f"  one-sided binomial P(WR > 25%): {s['binom_p_gt_25']:.4g}",
    ]
    return "\n".join(lines)


def breakdown(filled: pd.DataFrame, col: str, treat_eod_as_zero: bool) -> str:
    lines = [f"By {col}:"]
    for key, grp in filled.groupby(col, dropna=False):
        s = compute_stats(grp, treat_eod_as_zero=treat_eod_as_zero)
        lines.append(
            f"  {key}: n={s['n']}  WR={_fmt_pct(s['win_rate'])}  "
            f"E={_fmt_r(s['expectancy'])}  PF={_fmt_r(s['profit_factor']).replace('R','')}  "
            f"funnel win/loss/to/50/never/eod="
            f"{int((grp.outcome=='win').sum())}/"
            f"{int((grp.outcome=='loss').sum())}/"
            f"{int((grp.outcome=='no_fill_timeout').sum())}/"
            f"{int((grp.outcome=='no_fill_50pct').sum())}/"
            f"{int((grp.outcome=='no_fill_never_traded').sum())}/"
            f"{int((grp.outcome=='no_fill_by_eod').sum())}"
        )
    return "\n".join(lines)


def write_equity_svg(curve: pd.DataFrame, path: Path, title: str) -> None:
    w, h, pad = 900, 320, 40
    if curve.empty:
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"></svg>\n')
        return
    y = curve["equity"].to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    ymin, ymax = float(y.min()), float(y.max())
    if ymin == ymax:
        ymin, ymax = ymin - 1, ymax + 1
    def sx(i):
        return pad + (w - 2 * pad) * (i / max(len(y) - 1, 1))
    def sy(v):
        return pad + (h - 2 * pad) * (1.0 - (v - ymin) / (ymax - ymin))
    pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(y))
    zero = sy(0.0)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
  <rect width="100%" height="100%" fill="#111"/>
  <text x="{pad}" y="22" fill="#eee" font-size="14">{title}</text>
  <line x1="{pad}" y1="{zero:.1f}" x2="{w-pad}" y2="{zero:.1f}" stroke="#555" stroke-dasharray="4"/>
  <polyline fill="none" stroke="#6cf" stroke-width="2" points="{pts}"/>
  <text x="{pad}" y="{h-10}" fill="#aaa" font-size="11">n={len(y)}  end={y[-1]:.2f}R  maxDD={max_drawdown(y):.2f}R</text>
</svg>
"""
    path.write_text(svg)


def render_report(filled: pd.DataFrame, *, label: str) -> str:
    n = len(filled)
    fun = funnel(filled)
    s1 = compute_stats(filled, treat_eod_as_zero=False)
    s2 = compute_stats(filled, treat_eod_as_zero=True)
    eq1 = equity_curve(filled, treat_eod_as_zero=False)
    eq2 = equity_curve(filled, treat_eod_as_zero=True)
    month = None
    if "smt_time" in filled.columns:
        month = pd.to_datetime(filled["smt_time"], utc=True, errors="coerce").dt.tz_convert("America/New_York").dt.strftime("%Y-%m")
    lines = [
        label,
        "=" * len(label),
        f"signals={n}  (every scanner row, zero discretionary filtering)",
        "",
        "1. Outcome funnel (overall)",
    ]
    for _, row in fun.iterrows():
        lines.append(f"  {row['outcome']:<24} {int(row['count']):4d}  {row['pct']:5.1f}%")
    timeout_50 = int((filled["outcome"] == "no_fill_timeout").sum()) + int(
        (filled["outcome"] == "no_fill_50pct").sum()
    )
    lines += [
        f"  (timeout+50pct combined     {timeout_50:4d}  {100.0 * timeout_50 / n if n else 0:5.1f}%)",
        "",
        "1b. Funnel by entry_type",
    ]
    if "entry_type" in filled.columns:
        for et, grp in filled.groupby("entry_type"):
            lines.append(f"  {et} n={len(grp)}")
            for _, row in funnel(grp).iterrows():
                if row["count"]:
                    lines.append(f"    {row['outcome']:<22} {int(row['count']):4d}  {row['pct']:5.1f}%")
    lines += [
        "",
        "2. Stats — no_fill_by_eod EXCLUDED (resolved win/loss only; realized_R not target_R)",
        _stats_block("", s1).lstrip("\n"),
        f"  max drawdown (equity): {_fmt_r(max_drawdown(eq1['equity'].to_numpy() if len(eq1) else np.array([])))}",
        "",
        "3. Breakdown by entry_type / instrument / month (view 1: eod excluded)",
    ]
    if "entry_type" in filled.columns:
        lines.append(breakdown(filled, "entry_type", False))
    if "instrument" in filled.columns:
        lines.append(breakdown(filled, "instrument", False))
    if month is not None:
        tmp = filled.assign(month=month)
        lines.append(breakdown(tmp, "month", False))
    lines += [
        "",
        "4. Equity curve (cumulative realized_R, chronological) and max drawdown",
        f"  view 1 (eod excluded): end={_fmt_r(float(eq1['equity'].iloc[-1]) if len(eq1) else float('nan'))}  "
        f"maxDD={_fmt_r(max_drawdown(eq1['equity'].to_numpy() if len(eq1) else np.array([])))}",
        f"  view 2 (eod as 0R):     end={_fmt_r(float(eq2['equity'].iloc[-1]) if len(eq2) else float('nan'))}  "
        f"maxDD={_fmt_r(max_drawdown(eq2['equity'].to_numpy() if len(eq2) else np.array([])))}",
        "  files: pnl_equity_excluded.svg / pnl_equity_eod0.svg",
        "",
        "5. Same stats treating no_fill_by_eod as breakeven (0R) instead of excluded",
        _stats_block("", s2).lstrip("\n"),
        "",
        GAP_DISCLOSURE,
    ]
    return "\n".join(lines) + "\n"


def run_report(
    signals: Union[str, Path, pd.DataFrame],
    *,
    es_bars,
    nq_bars,
    out_dir: Union[str, Path],
    label: str,
) -> pd.DataFrame:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filled = simulate_fills(signals, es_bars=es_bars, nq_bars=nq_bars)
    filled.to_csv(out_dir / "pnl_fills.csv", index=False)
    eq1 = equity_curve(filled, treat_eod_as_zero=False)
    eq2 = equity_curve(filled, treat_eod_as_zero=True)
    eq1.to_csv(out_dir / "pnl_equity_excluded.csv", index=False)
    eq2.to_csv(out_dir / "pnl_equity_eod0.csv", index=False)
    write_equity_svg(eq1, out_dir / "pnl_equity_excluded.svg", "Cumulative realized_R (eod excluded)")
    write_equity_svg(eq2, out_dir / "pnl_equity_eod0.svg", "Cumulative realized_R (eod as 0R)")
    text = render_report(filled, label=label)
    (out_dir / "pnl_report.txt").write_text(text)
    print(text)
    return filled


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Automated fill P&L report (audit Section 4 stats).")
    p.add_argument("--signals", required=True)
    p.add_argument("--es-bars", required=True)
    p.add_argument("--nq-bars", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument(
        "--label",
        default="fully automated, zero discretionary filtering, v8.8 current head",
    )
    args = p.parse_args(argv)
    es = pd.read_csv(args.es_bars)
    nq = pd.read_csv(args.nq_bars)
    run_report(args.signals, es_bars=es, nq_bars=nq, out_dir=args.out_dir, label=args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
