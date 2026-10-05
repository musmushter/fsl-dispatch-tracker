#!/usr/bin/env bash
# Entry point for the desktop launcher (linux/fsl-share.desktop).
#
# Exists only to run share_link.sh inside a terminal window and then decide
# whether to keep that window open. Double-clicking a .desktop gives you no
# shell, so without this an error (tracker not running, download failed) would
# flash past as the window closed on exit.
#
# On SUCCESS the tunnel runs until you close the window or press Ctrl-C, and the
# window then closes by itself — same as the Windows share_link.bat cmd window.
# On FAILURE the window stays so you can read why.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

"$DIR/share_link.sh"
rc=$?

if [ "$rc" -ne 0 ]; then
    echo
    printf '   stopped with exit code %s.\n' "$rc"
    printf '   press Enter to close this window… '
    read -r _ || true
fi
exit 0
