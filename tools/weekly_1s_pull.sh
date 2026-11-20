#!/usr/bin/env bash
# Weekly 1-second MES/MNQ pull into the private historical-data repo.
# Meant for cron on Saturdays (market closed). Extra arguments are passed through,
# e.g.  tools/weekly_1s_pull.sh --no-push   or   --import-from data_1s
#
# DATA_REPO  local clone of the private data repo (default: ~/intermarket-divergence-data)
# PYTHON     python interpreter (default: python3)
set -euo pipefail
SCANNER_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_REPO="${DATA_REPO:-$HOME/intermarket-divergence-data}"
PY="${PYTHON:-python3}"
cd "$SCANNER_REPO"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) weekly 1s pull -> $DATA_REPO ==="
"$PY" -W ignore::RuntimeWarning -m backtesting_toolkit.weekly_1s_to_repo --data-repo "$DATA_REPO" "$@"
