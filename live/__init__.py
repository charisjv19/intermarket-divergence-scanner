"""Separate live scanners for frozen v8.7 and v8.8.

Each version polls ProjectX closed 1-minute bars, runs that version's
`run()` unchanged, and relays only newly appeared identities.

    python -m live.v87
    python -m live.v88

Relay: stdout + JSONL. Set SIGNAL_WEBHOOK_URL or SLACK_WEBHOOK_URL to
also POST each alert (Slack incoming-webhook compatible).
"""
