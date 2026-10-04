#!/usr/bin/env bash
# FSL tracker — start the tracker (CDP listener + dashboard on :8787).
#
# Runs in the foreground and PAUSES on exit so a crash is readable instead of
# a window that vanishes. For always-on use, prefer the systemd user service:
#   systemctl --user start fsl-tracker
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1

PY="$PWD/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "ERROR: no venv python at $PY"
    echo "Fix:  python3 -m venv .venv && .venv/bin/pip install websockets tzdata"
    read -r -p "Press Enter to close..." _ 2>/dev/null || true
    exit 1
fi

if ! "$PY" -c "import websockets" 2>/dev/null; then
    echo "ERROR: the venv is missing its dependencies."
    echo "Fix:  .venv/bin/pip install websockets tzdata"
    read -r -p "Press Enter to close..." _ 2>/dev/null || true
    exit 1
fi

"$PY" tracker.py
rc=$?
echo
echo "tracker exited with code $rc"
read -r -p "Press Enter to close..." _ 2>/dev/null || true
exit $rc