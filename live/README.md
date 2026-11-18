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

v8.8 here is the tagged OOS file, not `origin/main`. v8.10 is a new
protocol on that OOS baseline: NQ-confirm SMT can fire even when
ES-confirm already exists. Do not edit `smt_scanner_v8_8.py` for that.

## 1. Files you need on your laptop

Checkout this branch (`cursor/live-scanners-v87-v88-e152`) in Cursor or VS Code. You need:

- `live/` (runners)
- `smt_scanner_v8_7.py`
- `smt_scanner_v8_8.py` (OOS)
- `projectx_historical_pull.py`
- `swing_marker_detection.py`
- `backtest/` (v8.7 reconstructed levels + identity key)
- `requirements.txt`
- `.env.example` → copy to `.env`
- `.vscode/launch.json` (Run and Debug)

```bash
cd /path/to/intermarket-divergence-scanner
git checkout cursor/live-scanners-v87-v88-e152
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

Without `SLACK_WEBHOOK_URL`, alerts still print in the terminal and append to `live/state/v87_alerts.jsonl` / `v88_alerts.jsonl`.

## 3. Run in Cursor / VS Code

Install the Python extension. Select the `.venv` interpreter.

Run and Debug (`Ctrl+Shift+D` / `Cmd+Shift+D`):

- **Live v8.7** — one terminal, v8.7 only
- **Live v8.8 OOS** — one terminal, tagged OOS v8.8 only
- **Live v8.10 dual-confirm** — NQ-confirm even when ES-confirm exists
- **Both live scanners** — starts v8.7 and v8.8 (leave both terminals running)
- **v8.7 + v8.8 OOS + v8.10** — all three

First poll seeds identities already in the 36h window (no dump of old names). After that, only new identities go to Slack. Terminal poll lines (`new=0 scanned`) are local logs, not Slack messages.

From a terminal instead:

```bash
python -m live.v87 --test-webhook
python -m live.v88 --test-webhook
python -m live.v810 --test-webhook
python -m live.v87
python -m live.v88
python -m live.v810
```

Keep the laptop awake; sleep stops polling. These are not the Cloud Agent processes.
