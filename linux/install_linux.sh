#!/usr/bin/env bash
# FSL tracker — one-shot Linux installer (CachyOS / Arch / any systemd distro).
#
#   ./linux/install_linux.sh                build venv, run tests, install service
#   ./linux/install_linux.sh --no-service   build the venv and run tests only
#
# DIR is the REPO ROOT (this script lives in linux/, one level down), so the
# venv, tracker.py and the runtime files all sit together at the root and the
# systemd unit is generated with this machine's real path at install time.
set -eu
DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
cd "$DIR"

echo "== FSL tracker — Linux install =="
echo "   root: $DIR"

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
chmod +x "$DIR/linux/start.sh" "$DIR/linux/share_link.sh"

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
sed "s|__TRACKER_DIR__|$DIR|g" "$DIR/linux/fsl-tracker.service.in" > "$UNIT_DIR/fsl-tracker.service"
systemctl --user daemon-reload
echo "-- service installed: $UNIT_DIR/fsl-tracker.service"

echo
echo "Next steps:"
echo "  1. ./linux/start.sh                    # browser + console + tracker + dashboard"
echo "  2. log in to the dispatch console tab if it asks"
echo "  3. (optional) systemctl --user enable fsl-tracker   # start at login"
echo
echo "Logs:    journalctl --user -u fsl-tracker -f"
echo "Survive logout (recommended):  sudo loginctl enable-linger $USER"