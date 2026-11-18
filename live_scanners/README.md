# Live scanners (local + Slack)

Separate processes — all three always score each closed 1-minute bar.
They poll TopstepX MES/MNQ, run frozen `smt_scanner_v8_7.py` / tagged
OOS `smt_scanner_v8_8.py` / v8.10 `smt_scanner_v8_10.py`, and **Slack
only when that version has a new identity**. A process with nothing new
does not post “no alert.”

Loop: bar close → wait 1s → incremental pull of that close → score →
Slack if new. If a poll overruns the next minute, that process catch-up
re-polls immediately (stdout only; Slack stays new-identity-only). Live
retrieveBars uses a 20s timeout. After the first poll the 36h window is
cached on disk and only new bars (plus a 5-minute overlap) are fetched.

v8.8 here is the tagged OOS file (the `v8.8` tag; `main` now carries the same file). v8.10 is a new
protocol on that OOS baseline: NQ-confirm SMT can fire even when
ES-confirm already exists. Do not edit `smt_scanner_v8_8.py` for that.

State files (gitignored) are written to `live_scanners/state/` and
`live_scanners/bars/`.

> **Opening in VS Code or Cursor:** open the repo's **top folder**
> (`intermarket-divergence-scanner`, the one that contains `live_scanners/`,
> `.vscode/`, `requirements.txt` and `.env.example`) — **not** `live_scanners/`
> on its own. VS Code only finds the Run and Debug buttons in the top
> folder's `.vscode/`, the runner loads `.env` from the top folder, and
> `python -m live_scanners.v88` only works from there. Opening
> `live_scanners/` by itself gives "No module named live_scanners" and no
> launch buttons.

## 1. Files you need on your laptop

Everything is on `main`. Clone or pull the repo and open the **repo root**
folder (the one with `.vscode/`, `requirements.txt` and `.env.example`) in
Cursor or VS Code — not the `live_scanners/` folder by itself.

What the live scanners use:

- `live_scanners/` — runner code (poll, score, relay) and the **live copies**
  of the scanners currently running: `smt_scanner_v8_7.py`,
  `smt_scanner_v8_8.py` (tagged OOS) and `smt_scanner_v8_10.py`. The root
  files with the same names are the originals; `live_scanners/tests/test_live_copies.py`
  fails if a copy drifts from its original.
- `backtesting_toolkit/` — `projectx_historical_pull.py` (TopstepX client),
  v8.7 reconstructed levels and the identity key.
- Repo root setup files: `requirements.txt`, `.env.example` → `.env`,
  `.vscode/launch.json` (Run and Debug).

```bash
cd /path/to/intermarket-divergence-scanner
git checkout main && git pull
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your TopstepX user/key and Slack webhook. `.env` is gitignored.

## 2. Slack alerts

1. Open [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → From scratch.
2. **Incoming Webhooks** → On → **Add New Webhook to Workspace**.
3. Pick the channel (for example `#smt-live`) → copy the URL (`https://hooks.slack.com/services/...`).
4. Paste it into `.env` as `SLACK_WEBHOOK_URL=...`.

Run **Test Slack webhook (v8.8 OOS)** from Run and Debug. You should see a test post in that channel. Then start the live configs.

Without `SLACK_WEBHOOK_URL`, alerts still print in the terminal and append to `live_scanners/state/v87_alerts.jsonl` / `v88_alerts.jsonl`.

Chart-vs-Slack misses, late relays, and protocol ideas from live prints
go in [`LIVE_SCANNER_NOTES.md`](LIVE_SCANNER_NOTES.md) — newest first,
one block per observation. That log is observation only. It does not
change v8.7, tagged OOS v8.8, v8.10, or any other open version. Version
edits, pushes, or new scanners require an explicit ask and verification.

## 3. Run in Cursor / VS Code

Open the repo's top folder (see the note at the top of this file), not
`live_scanners/`. Install the Python extension. Select the `.venv` interpreter.

Run and Debug (`Ctrl+Shift+D` / `Cmd+Shift+D`):

- **Live v8.7** — one terminal, v8.7 only
- **Live v8.8 OOS** — one terminal, tagged OOS v8.8 only
- **Live v8.10 dual-confirm** — NQ-confirm even when ES-confirm exists
- **Both live scanners** — starts v8.7 and v8.8 (leave both terminals running)
- **v8.7 + v8.8 OOS + v8.10** — all three

First poll seeds identities already in the 36h window (no dump of old names). After that, only new identities go to Slack. Terminal poll lines (`new=0 scanned`) are local logs, not Slack messages.

From a terminal instead:

```bash
python -m live_scanners.v87 --test-webhook
python -m live_scanners.v88 --test-webhook
python -m live_scanners.v810 --test-webhook
python -m live_scanners.v87
python -m live_scanners.v88
python -m live_scanners.v810
```

Keep the laptop awake; sleep stops polling. These are not the Cloud Agent processes.
