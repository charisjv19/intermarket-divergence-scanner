"""Separate live scanners for frozen v8.7, tagged OOS v8.8, and v8.10.

Each version polls ProjectX closed 1-minute bars, runs that version's
`run()` unchanged, and relays only newly appeared identities.

v8.8 is the tagged OOS scanner (`git show v8.8:smt_scanner_v8_8.py`),
not origin/main. Do not replace it with main's copy.

v8.10 is a new protocol on that OOS baseline: ES-confirm and NQ-confirm
are independent, so an NQ-confirm / ES-fail pair can fire even when an
ES-confirm pair already exists on the same sw2.

    python -m live.v87
    python -m live.v88
    python -m live.v810

Relay: stdout + JSONL. Copy `.env.example` to `.env` and set
`SLACK_WEBHOOK_URL` for Slack. See `live/README.md` for Cursor/VS Code.
"""
