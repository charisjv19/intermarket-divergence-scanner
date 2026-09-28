"""Outcome-blind MES/MNQ charts clipped at the decision bar."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from backtest.discretion_review.common import (
    DISPLAY,
    bar_ohlc,
    clip_15m,
    clip_1m,
    confirmed_swing_mask,
    decision_bar_time,
    near_edge_price,
    parse_swing_et,
    strip_png_text,
    to_et,
)

BG = "#131722"
PANEL = "#1a1e2e"
UP = "#26a69a"
DOWN = "#ef5350"
GRID = "#2a2e39"
TEXT = "#d1d4dc"
MUTED = "#787b86"
FVG_LONG = "#26a69a"
FVG_SHORT = "#ef5350"
STOP = "#ff9800"
MID = "#ffeb3b"
EDGE = "#80deea"
SWEPT = "#ce93d8"
SWING_HI = "#81d4fa"
SWING_LO = "#f48fb1"


def _naive_et(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).map(lambda t: to_et(t).tz_localize(None) if to_et(t).tzinfo else t)


def _naive_et(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).map(lambda t: to_et(t).tz_localize(None) if to_et(t).tzinfo else t)


def _body_colors(df: pd.DataFrame) -> np.ndarray:
    return np.where(df["close"].to_numpy() >= df["open"].to_numpy(), UP, DOWN)


def _draw_candles(ax, df: pd.DataFrame, x) -> None:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    colors = _body_colors(df)
    ax.vlines(x, l, h, color=colors, linewidth=1.0, zorder=3)
    body_lo = np.minimum(o, c)
    body_hi = np.maximum(o, c)
    height = np.maximum(body_hi - body_lo, (h - l) * 0.02 + 1e-6)
    ax.bar(
        x,
        height,
        bottom=body_lo,
        width=0.7,
        color=colors,
        linewidth=0,
        zorder=4,
        align="center",
    )


def _style_ax(ax, ylabel: str) -> None:
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=TEXT, labelsize=8)
    ax.yaxis.label.set_color(TEXT)
    ax.set_ylabel(ylabel, fontsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.4, alpha=0.8)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")


def _time_ticks(ax, df: pd.DataFrame, x, fmt: str) -> None:
    n = len(df)
    if n == 0:
        return
    step = max(1, n // 6)
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)
    labels = [to_et(t).strftime(fmt) for t in df["et"].iloc[idxs]]
    ax.set_xticks([x[i] for i in idxs])
    ax.set_xticklabels(labels, color=MUTED, fontsize=7)


def _x_of(clipped: pd.DataFrame, when) -> Optional[float]:
    et = to_et(when)
    hits = clipped.index[clipped["et"] == et]
    if len(hits) == 0:
        return None
    return float(hits[0])


def _swing_times(row: pd.Series) -> list:
    times = []
    for col in ("es_sw1_time", "es_sw2_time", "nq_sw1_time", "nq_sw2_time"):
        val = row.get(col)
        if val is None or pd.isna(val):
            continue
        times.append(parse_swing_et(val))
    return times


def _mark_tape_swings(ax, clipped: pd.DataFrame, x, decision) -> None:
    ok = confirmed_swing_mask(clipped, decision)
    hi = clipped["Swing High"].to_numpy() == 1
    lo = clipped["Swing Low"].to_numpy() == 1
    if hi.any():
        mask = hi & ok.to_numpy()
        ax.scatter(
            x[mask],
            clipped.loc[mask, "high"],
            s=14,
            marker="v",
            c=SWING_HI,
            zorder=6,
            linewidths=0,
        )
    if lo.any():
        mask = lo & ok.to_numpy()
        ax.scatter(
            x[mask],
            clipped.loc[mask, "low"],
            s=14,
            marker="^",
            c=SWING_LO,
            zorder=6,
            linewidths=0,
        )


def _mark_named_swings(ax, clipped: pd.DataFrame, x, row: pd.Series, side: str) -> None:
    prefix = "es" if side == "ES" else "nq"
    for label, tcol, pcol in (
        ("sw1", f"{prefix}_sw1_time", f"{prefix}_sw1_price"),
        ("sw2", f"{prefix}_sw2_time", f"{prefix}_sw2_price"),
    ):
        ts = row.get(tcol)
        px = row.get(pcol)
        if ts is None or pd.isna(ts) or px is None or pd.isna(px):
            continue
        xi = _x_of(clipped, parse_swing_et(ts))
        if xi is None:
            continue
        ax.scatter(
            [xi],
            [float(px)],
            s=46,
            marker="o",
            facecolors="none",
            edgecolors="#ffffff",
            linewidths=1.2,
            zorder=7,
        )
        ax.annotate(
            label,
            (xi, float(px)),
            textcoords="offset points",
            xytext=(-16, 8) if label == "sw1" else (8, 8),
            color="#ffffff",
            fontsize=8,
            fontweight="bold",
            zorder=8,
        )


def _hline(ax, y, color, ls, lw, label, handles) -> None:
    line = ax.axhline(y, color=color, linestyle=ls, linewidth=lw, zorder=5, label=label)
    handles.append(line)


def _fvg_box(ax, clipped: pd.DataFrame, row: pd.Series):
    etype = str(row["entry_type"])
    if etype == "SMT_IN_FVG":
        lo, hi = row.get("pre_fvg_low"), row.get("pre_fvg_high")
        mid_ts = row.get("pre_fvg_bar")
        box_label = "pre-FVG"
    elif etype == "FVG_AFTER_SMT":
        lo, hi = row.get("fvg_low"), row.get("fvg_high")
        mid_ts = row.get("fvg_bar")
        box_label = "FVG"
    else:
        return None
    if lo is None or hi is None or pd.isna(lo) or pd.isna(hi):
        return None
    lo, hi = float(lo), float(hi)
    color = FVG_LONG if str(row["direction"]).upper() == "LONG" else FVG_SHORT
    x0 = 0.0
    if mid_ts is not None and not pd.isna(mid_ts):
        mid_x = _x_of(clipped, to_et(mid_ts))
        if mid_x is not None:
            x0 = max(0.0, mid_x - 1.0)
    x1 = float(len(clipped) - 1) + 0.45
    patch = Rectangle(
        (x0 - 0.45, lo),
        x1 - (x0 - 0.45),
        hi - lo,
        facecolor=color,
        edgecolor=color,
        alpha=0.28,
        linewidth=1.0,
        zorder=2,
        label=box_label,
    )
    ax.add_patch(patch)
    return patch


def _levels(ax, clipped: pd.DataFrame, row: pd.Series, traded: bool) -> None:
    if not traded:
        return
    handles = []
    box = _fvg_box(ax, clipped, row)
    if box is not None:
        handles.append(box)
    stop = row.get("stop_loss")
    if stop is not None and not pd.isna(stop):
        _hline(ax, float(stop), STOP, "-", 1.6, "stop", handles)
    instrument = str(row["instrument"]).upper()
    swept_col = "es_sw2_price" if instrument == "ES" else "nq_sw2_price"
    swept = row.get(swept_col)
    if swept is not None and not pd.isna(swept):
        _hline(ax, float(swept), SWEPT, "--", 1.1, "swept", handles)
    if str(row["entry_type"]) == "FVG_AFTER_SMT":
        mid = row.get("entry_50")
        if mid is not None and not pd.isna(mid):
            _hline(ax, float(mid), MID, ":", 1.1, "50%", handles)
        edge = near_edge_price(row)
        if edge is not None:
            _hline(ax, edge, EDGE, ":", 1.1, "near-edge", handles)
    if handles:
        ax.legend(
            handles=handles,
            loc="upper left",
            fontsize=7,
            framealpha=0.82,
            facecolor=PANEL,
            edgecolor=GRID,
            labelcolor=TEXT,
            borderpad=0.4,
            handlelength=2.2,
        )


def _pad_ylim(ax, df: pd.DataFrame, extra: list[Optional[float]] = ()) -> None:
    lo = float(df["low"].min())
    hi = float(df["high"].max())
    for v in extra:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if np.isnan(fv):
            continue
        lo = min(lo, fv)
        hi = max(hi, fv)
    pad = (hi - lo) * 0.08 if hi > lo else 1.0
    ax.set_ylim(lo - pad, hi + pad)


def render_signal(
    row: pd.Series,
    es: pd.DataFrame,
    nq: pd.DataFrame,
    out_path: Union[str, Path],
) -> dict:
    decision = decision_bar_time(row)
    extra = _swing_times(row)
    es_1m = clip_1m(es, decision, extra_times=extra)
    nq_1m = clip_1m(nq, decision, extra_times=extra)
    # Keep MES/MNQ on the same x index: inner-join timestamps after the
    # independent lookback extend, then re-clip (still inclusive of decision).
    common = sorted(set(es_1m["et"]).intersection(set(nq_1m["et"])))
    es_1m = es_1m[es_1m["et"].isin(common)].reset_index(drop=True)
    nq_1m = nq_1m[nq_1m["et"].isin(common)].reset_index(drop=True)
    if es_1m["et"].iloc[-1] != to_et(decision) or nq_1m["et"].iloc[-1] != to_et(decision):
        raise AssertionError("aligned clip lost the decision bar")

    es_15 = clip_15m(es, decision)
    nq_15 = clip_15m(nq, decision)

    traded = str(row["instrument"]).upper()
    display = DISPLAY.get(traded, traded)
    sid = str(row["id"])
    when = to_et(decision)
    header = (
        f"{sid}   {when.strftime('%Y-%m-%d %H:%M ET')}   "
        f"{row['entry_type']}   {display} {str(row['direction']).upper()}   "
        f"{row['combined_15m_bias']}   {row['session']}"
    )
    es_px = bar_ohlc(es, decision)
    nq_px = bar_ohlc(nq, decision)
    footer = (
        f"decision {when.strftime('%Y-%m-%d %H:%M:%S %Z')}   "
        f"MES O {es_px['open']:.2f}  H {es_px['high']:.2f}  "
        f"L {es_px['low']:.2f}  C {es_px['close']:.2f}   "
        f"MNQ O {nq_px['open']:.2f}  H {nq_px['high']:.2f}  "
        f"L {nq_px['low']:.2f}  C {nq_px['close']:.2f}"
    )

    fig = plt.figure(figsize=(15.2, 10.4), facecolor=BG)
    gs = fig.add_gridspec(
        4,
        1,
        height_ratios=[1.05, 1.05, 2.55, 2.55],
        hspace=0.28,
        left=0.04,
        right=0.90,
        top=0.93,
        bottom=0.07,
    )
    ax_es15 = fig.add_subplot(gs[0])
    ax_nq15 = fig.add_subplot(gs[1])
    ax_es1 = fig.add_subplot(gs[2])
    ax_nq1 = fig.add_subplot(gs[3])

    for ax, df, label, is_15, side in (
        (ax_es15, es_15, "MES 15m closed", True, "ES"),
        (ax_nq15, nq_15, "MNQ 15m closed", True, "NQ"),
        (ax_es1, es_1m, "MES 1m", False, "ES"),
        (ax_nq1, nq_1m, "MNQ 1m", False, "NQ"),
    ):
        _style_ax(ax, label)
        if df.empty:
            ax.text(0.5, 0.5, "no closed bars", ha="center", va="center", color=MUTED, transform=ax.transAxes)
            continue
        x = np.arange(len(df), dtype=float)
        _draw_candles(ax, df, x)
        ax.set_xlim(-0.8, len(df) - 0.2)
        extra_lv = []
        if not is_15 and side == traded:
            extra_lv = [
                row.get("stop_loss"),
                row.get("fvg_low") if str(row["entry_type"]) == "FVG_AFTER_SMT" else row.get("pre_fvg_low"),
                row.get("fvg_high") if str(row["entry_type"]) == "FVG_AFTER_SMT" else row.get("pre_fvg_high"),
                row.get("entry_50") if str(row["entry_type"]) == "FVG_AFTER_SMT" else None,
                near_edge_price(row),
                row.get("es_sw2_price" if traded == "ES" else "nq_sw2_price"),
            ]
        _pad_ylim(ax, df, extra_lv)
        _time_ticks(ax, df, x, "%m-%d %H:%M" if is_15 else "%H:%M")
        if not is_15:
            _mark_tape_swings(ax, df, x, decision)
            _mark_named_swings(ax, df, x, row, side)
            _levels(ax, df, row, traded=side == traded)
            ax.axvline(len(df) - 1, color="#ffffff", linewidth=0.7, alpha=0.35, zorder=1)

    fig.text(0.04, 0.965, header, color=TEXT, fontsize=10, fontweight="bold", ha="left", va="top")
    fig.text(0.04, 0.018, footer, color=MUTED, fontsize=8, ha="left", va="bottom")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    strip_png_text(out_path)
    return {"es": es_px, "nq": nq_px, "decision": when}
