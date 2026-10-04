#!/usr/bin/env bash
# FSL tracker — launch the dispatch-console Chrome with CDP on port 9222.
#
# Resolves a Chrome-family binary dynamically (newest Hermes-bundled Chrome for
# Testing, then any system Chrome/Chromium) so a Hermes update that renumbers
# ~/.hermes/tools/chromium-<N>/ cannot silently break the launcher.
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1

PORT=9222
PROFILE="$PWD/console_profile"
URL="https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"

fail() {
    echo
    echo "ERROR: $1"
    shift
    for line in "$@"; do echo "  $line"; done
    echo
    read -r -p "Press Enter to close..." _ 2>/dev/null || true
    exit 1
}

# ---- resolve a Chrome-family binary: newest bundled first, then system ----
CHROME=""; best=-1
for c in "$HOME"/.hermes/tools/chromium-*/chrome-linux64/chrome; do
    [ -x "$c" ] || continue
    v=$(basename "$(dirname "$(dirname "$c")")"); v=${v#chromium-}
    case "$v" in ''|*[!0-9]*) continue ;; esac
    if [ "$v" -gt "$best" ]; then best=$v; CHROME=$c; fi
done
if [ -z "$CHROME" ]; then
    for c in chromium chromium-browser google-chrome-stable google-chrome brave-browser; do
        if command -v "$c" >/dev/null 2>&1; then CHROME=$(command -v "$c"); break; fi
    done
fi
[ -n "$CHROME" ] || fail "no Chrome or Chromium found on this machine." \
    "Checked ~/.hermes/tools/chromium-*/chrome-linux64/chrome and the usual" \
    "system names. Install one with:  sudo pacman -S chromium"
echo "Using: $CHROME"

# ---- refuse to double-launch: an existing instance ignores our flags ----
if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "A debug Chrome is ALREADY listening on port $PORT."
    echo "Use that window (or close every chrome window and re-run this)."
    read -r -p "Press Enter to close..." _ 2>/dev/null || true
    exit 0
fi

mkdir -p "$PROFILE"
# --remote-allow-origins=* is REQUIRED on Chrome 111+ or the CDP websocket
# refuses to connect while /json/version still answers (silent reconnect loop).
nohup "$CHROME" --remote-debugging-port=$PORT "--remote-allow-origins=*" \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    "$URL" >/dev/null 2>&1 &

# ---- wait for the port and SAY SO (a first-run dialog can stall launch) ----
for i in $(seq 1 30); do
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
        echo
        echo "Debug port $PORT is UP (after ${i}s)."
        echo "Log in to the dispatch console in that window and KEEP IT OPEN."
        echo "Then start the tracker:   ./start_tracker.sh"
        exit 0
    fi
    sleep 1
done

fail "debug port $PORT never came up within 30s." \
    "Close every chrome/chromium window and run this again." \
    "A Chrome already running WITHOUT the debug flag swallows the new launch."