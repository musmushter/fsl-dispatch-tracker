#!/usr/bin/env bash
# Share the FSL tracker dashboard as a temporary public link.
# Linux counterpart of windows\share_link.bat — same idea, same tool.
#
# Viewers need no install and no login: the link shows the live dashboard.
# The link is TEMPORARY — it changes every time you run this, and it dies when
# you close this window (or press Ctrl-C). The tracker itself must be running.
set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT" || exit 1

DASH_PORT="${FSL_DASH_PORT:-8787}"
DASH_URL="http://127.0.0.1:$DASH_PORT"
TOOLS="$ROOT/linux/tools"
BIN="$TOOLS/cloudflared"

# The tunnel must not outlive whatever opened it. There are two ways it can:
# the terminal window is closed without signalling us, or the process that
# launched us exits and leaves us orphaned while still holding an open tunnel.
# Either way the dashboard keeps being published with nothing on screen to say
# so — seen live: a real tunnel to :8787 still serving with no window anywhere.
# So remember the terminal and the parent, and leave when either goes.
# FSL_SHARE_KEEP=1 deliberately opts out (a link that survives its window).
WATCH_TTY="$(tty 2>/dev/null || true)"
case "$WATCH_TTY" in /dev/pts/*|/dev/tty*) ;; *) WATCH_TTY="" ;; esac
WATCH_PPID="$PPID"

say() { printf '   %s\n' "$*"; }
die() { echo; printf '   %s\n' "$1"; shift
        for l in "$@"; do printf '   %s\n' "$l"; done; echo; exit 1; }

echo "============================================"
echo " FSL Tracker — share link"
echo "============================================"
echo "A temporary public link will appear below."
echo "Anyone with it can VIEW the dashboard."
echo "The link stops working when you close this window (Ctrl-C)."
echo

# ---- 1. the tracker must be running --------------------------------------
if ! curl -sf -o /dev/null --max-time 5 "$DASH_URL/dashboard.html"; then
    die "the tracker is not answering on $DASH_URL." \
        "Start it first:   ./linux/start.sh" \
        "or:               systemctl --user start fsl-tracker"
fi
say "tracker is up on $DASH_URL"

# ---- 2. cloudflared: PATH first, then the bundled copy, then download -----
CF=""
if command -v cloudflared >/dev/null 2>&1; then
    CF="$(command -v cloudflared)"
    say "using cloudflared from PATH — $CF"
elif [ -x "$BIN" ]; then
    CF="$BIN"
    say "using the bundled cloudflared"
else
    say "cloudflared is not here yet — fetching it once (about 40 MB)"
    case "$(uname -m)" in
        x86_64|amd64)  CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" ;;
        aarch64|arm64) CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" ;;
        *) die "no cloudflared build for $(uname -m)." \
               "Install it from your distro instead:  sudo pacman -S cloudflared" ;;
    esac
    mkdir -p "$TOOLS"
    if ! curl -fL --progress-bar -o "$BIN.part" "$CF_URL"; then
        rm -f "$BIN.part"
        die "could not download cloudflared." \
            "Install it from your distro instead:  sudo pacman -S cloudflared" \
            "(it is in extra/ and cachyos-extra-v3/), then run this again."
    fi
    chmod +x "$BIN.part" && mv "$BIN.part" "$BIN"
    CF="$BIN"
    say "saved to $BIN"
fi

# ---- 3. quick tunnel; show the link, die with the window ------------------
# cloudflared writes the public URL to its log rather than stdout, so watch the
# log for it, then stream the log so its errors stay visible.
LOG="$(mktemp -t fsl-share.XXXXXX)"
cleanup() {
    [ -n "${CFPID:-}" ]        && kill "$CFPID"        2>/dev/null
    [ -n "${TAILPID:-}" ]      && kill "$TAILPID"      2>/dev/null
    [ -n "${WATCHDOG_PID:-}" ] && kill "$WATCHDOG_PID" 2>/dev/null
    rm -f "$LOG"
    return 0
}
on_signal() { cleanup; trap - EXIT; exit 0; }
trap cleanup EXIT
trap on_signal INT TERM HUP

"$CF" tunnel --url "$DASH_URL" --no-autoupdate >"$LOG" 2>&1 &
CFPID=$!

# Guard the tunnel for the WHOLE run, not just the final tail: the reachability
# check below can take minutes, and an orphan created during it was still
# serving long after the window was gone. This runs from the moment cloudflared
# starts until cleanup kills it.
WATCHDOG_PID=""
if [ "${FSL_SHARE_KEEP:-0}" != "1" ]; then
    WATCH_SELF=$$
    (
        while :; do
            sleep 2
            gone=0
            if [ -n "$WATCH_TTY" ] && [ ! -e "$WATCH_TTY" ]; then gone=1; fi
            if [ "$(ps -o ppid= -p "$WATCH_SELF" 2>/dev/null | tr -d ' ')" != "$WATCH_PPID" ]; then gone=1; fi
            if [ "$gone" = 1 ]; then
                printf '   the window that opened this is gone — closing the tunnel\n'
                kill -TERM "$WATCH_SELF" 2>/dev/null
                exit 0
            fi
        done
    ) &
    WATCHDOG_PID=$!
fi

LINK=""
for _ in $(seq 1 80); do                          # up to 40s
    LINK="$(grep -oE 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1)"
    [ -n "$LINK" ] && break
    kill -0 "$CFPID" 2>/dev/null || break         # cloudflared gave up
    sleep 0.5
done

if [ -z "$LINK" ]; then
    die "the tunnel did not come up. cloudflared said:" "$(tail -n 12 "$LOG")"
fi

echo
echo "   +-------------------------------------------------------------+"
echo "   |  SHARE THIS LINK:                                           |"
echo "   |                                                             |"
echo "   |  $LINK"
echo "   +-------------------------------------------------------------+"
echo
say "viewers need no install and no login — just the link"
say "the link changes every run and dies when this window closes"
if command -v wl-copy >/dev/null 2>&1; then
    printf '%s' "$LINK" | wl-copy 2>/dev/null && say "copied to the clipboard"
elif command -v xclip >/dev/null 2>&1; then
    printf '%s' "$LINK" | xclip -selection clipboard 2>/dev/null && say "copied to the clipboard"
elif command -v xsel >/dev/null 2>&1; then
    printf '%s' "$LINK" | xsel -b 2>/dev/null && say "copied to the clipboard"
fi
echo
say "keep this window open for as long as you want the link live"

# The quick-tunnel hostname is only registered in DNS shortly AFTER the tunnel
# connects, so the link can fail to resolve or return 502 for a minute or two.
# Wait it out here instead of letting the first person you send it to hit that.
echo
say "checking the link is really reachable — this can take a minute or two"
LIVE=0
for _ in $(seq 1 60); do                          # up to ~3 min
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$LINK/dashboard.html" 2>/dev/null)"
    [ "$CODE" = "200" ] && { LIVE=1; break; }
    sleep 3
done
if [ "$LIVE" = 1 ]; then
    say "the link is LIVE and answering — safe to send"
else
    say "it is not answering yet. Usually up within a few minutes; if it stays"
    say "dead, close this window and run this script again for a fresh link."
fi
echo

# Stream cloudflared's log so its status (and any error) stays visible. Kept in
# the background so cleanup can stop it too — a foreground tail would be
# orphaned when the script is signalled rather than interrupted.
tail -f "$LOG" &
TAILPID=$!
wait "$TAILPID"
