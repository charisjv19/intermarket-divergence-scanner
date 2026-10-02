"""Separate live scanners for frozen v8.7 and tagged OOS v8.8.

Each version polls ProjectX closed 1-minute bars, runs that version's
`run()` unchanged, and relays only newly appeared identities.

v8.8 is the tagged OOS scanner (`git show v8.8:smt_scanner_v8_8.py`),
not origin/main. That file has the confirmation-clock, session-gap,
and inner-join fixes. Do not replace it with main's copy.

    python -m live.v87
    python -m live.v88

Relay: stdout + JSONL. Copy `.env.example` to `.env` and set
`SLACK_WEBHOOK_URL` for Slack. See `live/README.md` for Cursor/VS Code.
"""
