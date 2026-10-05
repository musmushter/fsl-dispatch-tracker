#!/usr/bin/env bash
# FSL tracker — launch the dispatch-console Chrome with CDP on port 9222.
#
# Resolves a Chrome-family binary dynamically (newest Hermes-bundled Chrome for
# Testing, then any system Chrome/Chromium) so a Hermes update that renumbers
# ~/.hermes/tools/chromium-<N>/ cannot silently break the launcher.
set -u
# this script lives in linux/; cd to the repo root one level up so the
# $PWD-relative paths below (console_profile, console_chrome.log) stay correct
cd "$(dirname "$(dirname "$(readlink -f "$0")")")" || exit 1

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
# Preference: $FSL_CHROME override > system Chromium/Chrome > newest bundled
# Chrome for Testing. System chromium wins because its path is stable
# (/usr/bin/chromium), it updates with the OS, and it carries no "Chrome for
# Testing" branding; the bundled CfT sits at a version-numbered path that a
# Hermes update can renumber or delete and never receives security updates.
CHROME="${FSL_CHROME:-}"
if [ -z "$CHROME" ]; then
    for c in chromium chromium-browser google-chrome-stable google-chrome brave-browser; do
        if command -v "$c" >/dev/null 2>&1; then CHROME=$(command -v "$c"); break; fi
    done
fi
if [ -z "$CHROME" ]; then
    best=-1
    for c in "$HOME"/.hermes/tools/chromium-*/chrome-linux64/chrome; do
        [ -x "$c" ] || continue
        v=$(basename "$(dirname "$(dirname "$c")")"); v=${v#chromium-}
        case "$v" in ''|*[!0-9]*) continue ;; esac
        if [ "$v" -gt "$best" ]; then best=$v; CHROME=$c; fi
    done
fi
[ -n "$CHROME" ] || fail "no Chrome or Chromium found on this machine." \
    "Checked the usual system names and ~/.hermes/tools/chromium-*/chrome-linux64/chrome." \
    "Install one with:  sudo pacman -S chromium"
echo "Using: $CHROME"

# ---- refuse to double-launch: an existing instance ignores our flags ----
if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "A debug Chrome is ALREADY listening on port $PORT."
    echo "Use that window (or close every chrome window and re-run this)."
    read -r -p "Press Enter to close..." _ 2>/dev/null || true
    exit 0
fi

mkdir -p "$PROFILE"
LOG="$PWD/console_chrome.log"
: > "$LOG"

# --remote-allow-origins=* is REQUIRED on Chrome 111+ or the CDP websocket
# refuses to connect while /json/version still answers (silent reconnect loop).
# --disable-features=Vulkan: this box carries a broken Vulkan implicit layer
# (VkLayer_LSFGVK_frame_generation -> liblsfg-vk-layer.so) that the GPU process
# cannot initialise against, which leaves the window created but unrendered.
# Chrome never needs Vulkan for the console, so skip it entirely.
# Output goes to $LOG — a silent launcher makes a window failure undiagnosable.
nohup "$CHROME" \
    --remote-debugging-port=$PORT "--remote-allow-origins=*" \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    --ozone-platform-hint=auto \
    --disable-features=Vulkan \
    --disable-infobars \
    "$URL" >>"$LOG" 2>&1 &
CHROME_PID=$!
echo "chrome pid $CHROME_PID   (log: $LOG)"

# ---- wait for the port and SAY SO (a first-run dialog can stall launch) ----
for i in $(seq 1 30); do
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
        echo
        echo "Debug port $PORT is UP (after ${i}s)."
        echo "Log in to the dispatch console in that window and KEEP IT OPEN."
        echo "Then start the tracker:   ./linux/start_tracker.sh"
        exit 0
    fi
    sleep 1
done

echo
echo "--- last lines of $LOG ---"
tail -15 "$LOG" 2>/dev/null
fail "debug port $PORT never came up within 30s." \
    "Close every chrome/chromium window and run this again." \
    "A Chrome already running WITHOUT the debug flag swallows the new launch."