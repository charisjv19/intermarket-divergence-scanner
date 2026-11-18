"""Build an outcome-blind discretion-review pack (pilot or full).

Does not read fill outcomes into HTML, images, or the review CSV.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import pandas as pd

from backtest.discretion_review.common import (
    SAMPLE_SEED,
    leak_hits,
    nearby_shapes,
    prepare_tape,
    scan_path_for_leaks,
    scan_tree_for_leaks,
    to_et,
)
from backtest.discretion_review.render import render_signal
from backtest.discretion_review.sample import (
    DEFAULT_SIGNALS,
    assert_signals_in_fills,
    blind_map,
    filter_step5_to_windows,
    load_closed_signals,
    render_map,
    shuffled_order,
)

DEFAULT_TAPES = {
    "mayaug": {
        "ES": Path("/tmp/windows/ES_may_aug.csv"),
        "NQ": Path("/tmp/windows/NQ_may_aug.csv"),
    },
    "sepjan": {
        "ES": Path("/tmp/windows/ES_sep_jan.csv"),
        "NQ": Path("/tmp/windows/NQ_sep_jan.csv"),
    },
}
DEFAULT_FILLS = Path("/opt/cursor/artifacts/live_achievable_waterfall/step5_v89_combined.csv")
TEMPLATE = Path(__file__).with_name("review_template.html")
SPOTCHECK_N = 5
SPOTCHECK_SEED = 42


def load_tapes(tape_paths: dict) -> dict:
    loaded = {}
    for window, sides in tape_paths.items():
        loaded[window] = {
            "ES": prepare_tape(pd.read_csv(sides["ES"])),
            "NQ": prepare_tape(pd.read_csv(sides["NQ"])),
        }
    return loaded


def png_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.standard_b64encode(path.read_bytes()).decode("ascii")


def write_html(catalog: list[dict], out_html: Path, pack_id: str) -> None:
    raw = TEMPLATE.read_text(encoding="utf-8")
    html = raw.replace("__PACK_ID__", pack_id).replace(
        "__CATALOG_JSON__", json.dumps(catalog, separators=(",", ":"))
    )
    out_html.write_text(html, encoding="utf-8")


def html_for_leak_scan(html: str) -> str:
    """Drop embedded chart bytes so leak scan does not search base64 noise."""
    return re.sub(r"const CATALOG = \[.*?\];", "const CATALOG = [];", html, count=1, flags=re.S)


def write_spotcheck(rows: pd.DataFrame, tapes: dict, out_path: Path, n: int = SPOTCHECK_N) -> pd.DataFrame:
    rng = pd.Series(rows.index).sample(n=min(n, len(rows)), random_state=SPOTCHECK_SEED)
    picked = rows.loc[rng.values].copy()
    lines = [
        "SPOT-CHECK: 5 random decision bars from the pilot.",
        "Compare candle SHAPES and relative moves to TradingView, not just the",
        "absolute print. Older exports can sit at a different price level than",
        "today's front-month MES/MNQ chart (roll / contract). A near-constant",
        "offset with matching bodies is expected in that case.",
        "Tapes here are ES/NQ 1-minute historical; charts are labeled MES/MNQ.",
        "This environment cannot open TradingView; the prints below are tape truth.",
        "",
    ]
    records = []
    for _, row in picked.iterrows():
        window = row["window"]
        inst = str(row["instrument"]).upper()
        tape = tapes[window][inst]
        decision = to_et(row["decision_time"])
        shapes = nearby_shapes(tape, decision, n_before=3)
        lines.append(f"id={row['id']}  {inst}  {row['entry_type']}  {row['direction']}")
        lines.append(f"  decision {decision.isoformat()}")
        for _, bar in shapes.iterrows():
            mark = "  <-- decision" if to_et(bar["et"]) == decision else ""
            lines.append(
                f"  {to_et(bar['et']).strftime('%Y-%m-%d %H:%M')}  "
                f"O {bar['open']:.2f}  H {bar['high']:.2f}  "
                f"L {bar['low']:.2f}  C {bar['close']:.2f}  "
                f"{bar['shape']} body={bar['body']:.2f} range={bar['range']:.2f}{mark}"
            )
        lines.append("")
        last = shapes.iloc[-1]
        records.append(
            {
                "id": row["id"],
                "instrument": inst,
                "decision_time": decision.isoformat(),
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "close": float(last["close"]),
            }
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return pd.DataFrame(records)


def build(
    *,
    out_dir: Path,
    n_pilot: int,
    seed: int,
    signal_paths: dict,
    tape_paths: dict,
    fills_path: Path | None,
) -> None:
    out_dir = Path(out_dir)
    images = out_dir / "images"
    private = out_dir / "private"
    images.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    signals = load_closed_signals(signal_paths)
    if fills_path and Path(fills_path).is_file():
        fills = pd.read_csv(fills_path)
        assert_signals_in_fills(signals, fills)
        windowed = filter_step5_to_windows(fills, signals)
        if len(windowed) != len(signals):
            raise AssertionError(
                f"windowed Step 5 n={len(windowed)} != closed-bar n={len(signals)}"
            )
        print(f"Step 5 identity check OK: {len(signals)} closed-bar = {len(windowed)} fills")
    else:
        print("Step 5 fills not found; skipping identity overlap check")

    ordered = shuffled_order(signals, seed=seed)
    blind_map(ordered).to_csv(private / "order_full.csv", index=False)
    render_map(ordered).to_csv(private / "render_rows.csv", index=False)
    print(f"stored order n={len(ordered)} seed={seed} ids unique={ordered['id'].nunique()}")

    pilot = ordered.iloc[:n_pilot].copy()
    blind_map(pilot).to_csv(private / "id_map.csv", index=False)
    render_map(pilot).to_csv(private / "render_rows_pilot.csv", index=False)

    tapes = load_tapes(tape_paths)
    catalog = []
    for i, row in pilot.iterrows():
        png = images / f"{row['id']}.png"
        print(f"render {row['stored_order']}/{n_pilot} {row['id']} {row['entry_type']} {row['window']}")
        render_signal(row, tapes[row["window"]]["ES"], tapes[row["window"]]["NQ"], png)
        catalog.append(
            {
                "id": row["id"],
                "stored_order": int(row["stored_order"]),
                "image": png_data_uri(png),
            }
        )

    pack_id = f"pilot-{n_pilot}-seed{seed}"
    write_html(catalog, out_dir / "review.html", pack_id)
    write_spotcheck(pilot, tapes, out_dir / "SPOTCHECK.txt")

    readme = out_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "Discretion review PILOT (hindsight-blind).",
                f"Signals: v8.9 closed-bar live-achievable set, May-Aug + Sep-Jan, n={len(ordered)}.",
                f"This pack renders the first {n_pilot} of the stored random order (seed {seed}).",
                "Open review.html in a browser (double-click after extracting the zip).",
                "Charts are embedded in that HTML file, so they work even if the",
                "images folder is missing. Keys: T take, S skip,",
                "1-7 skip+reason, Backspace undo. Download decisions.csv when done.",
                "Do not open private/ during review. Scoring is a separate repo script:",
                "  python -m backtest.discretion_review.score_review \\",
                "    --decisions decisions.csv \\",
                "    --id-map private/id_map.csv \\",
                "    --fills /opt/cursor/artifacts/live_achievable_waterfall/step5_v89_combined.csv",
                "The review page never loads results or R.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    review_hits: list[str] = []
    review_hits.extend(scan_tree_for_leaks(images))
    html_path = out_dir / "review.html"
    if html_path.is_file():
        hits = leak_hits(html_for_leak_scan(html_path.read_text(encoding="utf-8")))
        if hits:
            review_hits.append(f"{html_path}: text {hits}")
    for p in (
        out_dir / "README.txt",
        out_dir / "SPOTCHECK.txt",
        private / "id_map.csv",
        private / "order_full.csv",
        private / "render_rows.csv",
        private / "render_rows_pilot.csv",
    ):
        if p.is_file():
            review_hits.extend(scan_path_for_leaks(p))
    if review_hits:
        raise SystemExit("LEAK SCAN FAILED:\n" + "\n".join(review_hits))
    print(f"leak scan OK on review surfaces ({len(catalog)} images)")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build an outcome-blind discretion review pack")
    p.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/discretion_review_pilot"))
    p.add_argument("--pilot", type=int, default=40)
    p.add_argument("--seed", type=int, default=SAMPLE_SEED)
    p.add_argument("--fills", type=Path, default=DEFAULT_FILLS)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    build(
        out_dir=args.out,
        n_pilot=args.pilot,
        seed=args.seed,
        signal_paths=DEFAULT_SIGNALS,
        tape_paths=DEFAULT_TAPES,
        fills_path=args.fills,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
