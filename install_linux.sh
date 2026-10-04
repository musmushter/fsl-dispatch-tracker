#!/usr/bin/env bash
# FSL tracker — one-shot Linux installer (CachyOS / Arch / any systemd distro).
#
#   ./install_linux.sh                build venv, run tests, install the service
#   ./install_linux.sh --no-service   build the venv and run tests only
#
# Everything is derived from THIS script's location, so the tree can live
# anywhere (the systemd unit is generated with the real path at install time).
set -eu
DIR="$(dirname "$(readlink -f "$0")")"
cd "$DIR"

echo "== FSL tracker — Linux install =="
echo "   dir: $DIR"

# ---- 1. venv + dependencies ----
if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "-- creating venv"
    python3 -m venv "$DIR/.venv"
fi
echo "-- installing dependencies (websockets, tzdata)"
"$DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/.venv/bin/python" -m pip install --quiet websockets tzdata
"$DIR/.venv/bin/python" -c "import websockets, tzdata" && echo "   deps OK"

# ---- 2. launchers ----
chmod +x "$DIR/start_chrome.sh" "$DIR/start_tracker.sh"

# ---- 3. regression suite ----
echo "-- running regression suite"
if "$DIR/.venv/bin/python" "$DIR/tests.py" > "$DIR/tests_out.txt" 2>&1; then
    echo "   $(grep -c '^PASS' "$DIR/tests_out.txt") passed, $(grep -c '^FAIL' "$DIR/tests_out.txt") failed   -> tests_out.txt"
else
    echo "   WARNING: suite exited non-zero — see tests_out.txt"
fi

if [ "${1:-}" = "--no-service" ]; then
    echo "done (service not installed)."
    exit 0
fi

# ---- 4. systemd user service, generated for THIS machine's path ----
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|__TRACKER_DIR__|$DIR|g" "$DIR/fsl-tracker.service.in" > "$UNIT_DIR/fsl-tracker.service"
systemctl --user daemon-reload
echo "-- service installed: $UNIT_DIR/fsl-tracker.service"

echo
echo "Next steps:"
echo "  1. ./start_chrome.sh                  # log in to the dispatch console"
echo "  2. systemctl --user enable --now fsl-tracker"
echo "  3. open http://127.0.0.1:8787/dashboard.html"
echo
echo "Logs:    journalctl --user -u fsl-tracker -f"
echo "Survive logout (recommended):  sudo loginctl enable-linger $USER"