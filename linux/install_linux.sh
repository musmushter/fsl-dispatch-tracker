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
chmod +x "$DIR/linux/start.sh" "$DIR/linux/share_link.sh" \
         "$DIR/linux/share_link_terminal.sh"

# Desktop entry, generated with THIS machine's real path — the same rule as the
# systemd unit below, and as the Windows SETUP.ps1 rewriting its .bats. It lands
# in the application menu (Terminal=true opens konsole and shows the link), and
# in ~/Desktop too when that folder exists, so it can simply be double-clicked
# the way windows\share_link.bat is.
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
sed "s|@ROOT@|$DIR|g" "$DIR/linux/fsl-share.desktop.in" > "$APPS/fsl-share.desktop"
chmod +x "$APPS/fsl-share.desktop"
if [ -d "$HOME/Desktop" ]; then
    cp "$APPS/fsl-share.desktop" "$HOME/Desktop/fsl-share.desktop"
    chmod +x "$HOME/Desktop/fsl-share.desktop"
fi
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS" >/dev/null 2>&1
echo "   launcher: $APPS/fsl-share.desktop  (menu: 'FSL Tracker - Share Link')"

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