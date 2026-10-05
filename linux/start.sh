#!/usr/bin/env bash
# FSL tracker — one command to bring everything up.
#
#   1. console browser: uses the one already listening on 9222, else launches it
#   2. dispatch console: uses the tab already open, else opens it
#   3. tracker: uses the one already serving :8787, else starts it
#   4. dashboard: opens as a tab in that same browser
#
# Idempotent by design — run it as often as you like. It never starts a second
# browser, never opens a duplicate console tab, and never restarts a tracker
# that is already serving.
set -u
ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
cd "$ROOT" || exit 1

CDP_PORT=9222
DASH_PORT=8787
PROFILE="$ROOT/console_profile"
CLOG="$ROOT/console_chrome.log"
CONSOLE_URL="https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"
DASH_URL="http://127.0.0.1:$DASH_PORT/dashboard.html"

say()  { echo "   $*"; }
fail() { echo; echo "ERROR: $1"; shift
         for l in "$@"; do echo "   $l"; done; echo
         read -r -p "Press Enter to close..." _ 2>/dev/null || true; exit 1; }

cdp_up()  { curl -s --max-time 3 "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; }
dash_up() { curl -s --max-time 3 "http://127.0.0.1:$DASH_PORT/dashboard.html" >/dev/null 2>&1; }
pages()   { curl -s --max-time 5 "http://127.0.0.1:$CDP_PORT/json/list" 2>/dev/null; }
PY="$ROOT/.venv/bin/python"

# Keep you signed in across browser restarts.
#
# Salesforce's auth cookies (login.salesforce.com/session, __Secure-has-sid,
# inst, clientSrc) are SESSION-ONLY, and Chromium DELETES session-only cookies
# on a clean exit unless "Continue where you left off" is enabled — in which
# case it persists them. That single pref is the whole difference between
# needing a fresh login after every restart and staying signed in.
# No credentials are read, stored or typed here.
#
# MUST run while the browser is NOT running: Chromium rewrites Preferences from
# its in-memory copy on exit and would clobber the edit.
keep_signed_in() {
    [ -f "$PROFILE/Default/Preferences" ] || return 0
    PY="$ROOT/.venv/bin/python"
    [ -x "$PY" ] || return 0
    "$PY" - "$PROFILE/Default/Preferences" <<'PYEOF' 2>/dev/null || true
import json, sys
path = sys.argv[1]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)                      # fresh profile; Chromium writes its own
changed = False
sess = d.setdefault("session", {})
if sess.get("restore_on_startup") != 1:
    sess["restore_on_startup"] = 1   # 1 = continue where you left off
    changed = True
prof = d.setdefault("profile", {})
if prof.get("exit_type") != "Normal":
    prof["exit_type"] = "Normal"     # don't show the crash-restore bubble
    changed = True
if changed:
    json.dump(d, open(path, "w", encoding="utf-8"))
    print("   session persistence enabled (survives browser restarts)")
PYEOF
}

# -f/--fg: run the tracker in THIS terminal (the Windows-cmd-window model) so
# closing the window, or Ctrl-C, stops it — instead of handing it to the
# systemd user service, which is the better default (survives a crash, logs to
# journald, keeps running after logout).
FG=0
case "${1:-}" in -f|--fg|--foreground) FG=1 ;; esac

open_dashboard() {
    if pages | grep -q "$DASH_URL"; then
        say "dashboard tab already open"
    else
        curl -s -X PUT --max-time 10 "http://127.0.0.1:$CDP_PORT/json/new?$DASH_URL" \
            >/dev/null 2>&1 && say "opened in the console browser" \
                                || say "could not open the tab — go to $DASH_URL"
    fi
}

# Close leftover login-redirect tabs. They are dead weight, and with 'continue
# where you left off' Chromium restores them on every start, where they pile up
# and — if one comes back to the front — make a perfectly good session look
# like it got logged out.
# Keep exactly ONE console tab. Two things used to leave extras behind:
# leftover login-redirect tabs (which 'continue where you left off' restores on
# every start, and which — brought to the front — make a working session look
# logged out), and duplicate console tabs from deciding before the session
# restore had finished.
tidy_console_tabs() {
    pages | "$PY" -c "
import json,sys
try:
    ts = [t for t in json.load(sys.stdin) if t.get('type') == 'page']
except Exception:
    ts = []
doomed  = [t['id'] for t in ts if 'login?ec=302' in t.get('url','')]
console = [t['id'] for t in ts if 's/dispatch-console' in t.get('url','')]
doomed += console[1:]          # keep the first console tab, ditch the rest
for i in doomed:
    print(i)
" 2>/dev/null | while read -r id; do
        [ -n "$id" ] || continue
        curl -s --max-time 5 "http://127.0.0.1:$CDP_PORT/json/close/$id" >/dev/null 2>&1 \
            && say "closed a spare console/login tab"
    done
    return 0
}

echo "FSL tracker — starting up"

# ---------------- 1. console browser ----------------
echo "== 1/4 console browser =="
if cdp_up; then
    say "already listening on :$CDP_PORT — not launching another"
else
    # $FSL_CHROME override > system Chromium/Chrome > newest bundled Chrome for
    # Testing. System chromium wins: stable path, updates with the OS, no CfT
    # branding. The bundled CfT lives at a version-numbered path a Hermes
    # update can renumber, hence the glob.
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
    [ -n "$CHROME" ] || fail "no Chrome or Chromium found." \
        "Install one with:  sudo pacman -S chromium"

    mkdir -p "$PROFILE"; : > "$CLOG"
    keep_signed_in
    say "launching $(basename "$CHROME") with the dispatch console"
    # --remote-allow-origins=* is REQUIRED on Chrome 111+ or the CDP websocket
    # is refused while /json/version still answers (a silent reconnect loop).
    # A dedicated NON-default profile is what keeps Chrome 136+ from demanding
    # an "Allow remote debugging?" click on every attach.
    nohup "$CHROME" --remote-debugging-port=$CDP_PORT "--remote-allow-origins=*" \
        --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
        --ozone-platform-hint=auto --disable-features=Vulkan --disable-infobars \
        "$CONSOLE_URL" >>"$CLOG" 2>&1 &

    for i in $(seq 1 30); do cdp_up && break; sleep 1; done
    cdp_up || { echo; tail -15 "$CLOG" 2>/dev/null
                fail "debug port $CDP_PORT never came up within 30s." \
                     "Close every chrome/chromium window and run this again." \
                     "A Chrome already running WITHOUT the debug flag swallows the launch."; }
    say "debug port UP after ${i}s"
fi

# ---------------- 2. dispatch console ----------------
echo "== 2/4 dispatch console =="
# Wait for Chromium's session restore to settle before deciding anything. With
# 'continue where you left off' the previous tabs reappear ASYNCHRONOUSLY, and
# deciding too early made this open a console tab that was about to be restored
# anyway — that is where the duplicates came from.
if ! pages | grep -q 'dispatch-console'; then
    for _ in $(seq 1 8); do
        sleep 1
        pages | grep -q 'dispatch-console' && break
    done
fi

SIGNED_IN=0; pages | grep -q 's/dispatch-console"'         && SIGNED_IN=1
HAS_LOGIN=0; pages | grep -q 'login?[^"]*dispatch-console' && HAS_LOGIN=1

if [ "$SIGNED_IN" = 1 ]; then
    say "console tab already open and signed in"
    tidy_console_tabs
elif [ "$HAS_LOGIN" = 1 ]; then
    say "console tab open (showing the login page)"
else
    say "console tab not loaded — opening it"
    curl -s -X PUT --max-time 10 "http://127.0.0.1:$CDP_PORT/json/new?$CONSOLE_URL" \
        >/dev/null 2>&1 || say "could not open it via CDP (open it by hand)"
    sleep 3
fi
# On the login page: surface the tab and TICK 'Remember me'.
#
# #rememberUn is a plain boolean with no credentials in it, and it is what
# makes Salesforce issue a PERSISTENT session cookie. Left unticked the session
# dies with the browser, no matter what Chromium does with its cookie store.
#
# Deliberately NOT auto-submitting the form. Chrome withholds an autofilled
# password from scripts (input.value reads empty while the field is visibly
# filled), so we cannot confirm a synthetic click would carry it — and a click
# that didn't would count as a FAILED login, which repeated can lock the
# account. Signing in stays a human action; this just removes the friction.
if [ "$SIGNED_IN" != 1 ] && [ "$HAS_LOGIN" = 1 ]; then
    say "console needs a sign-in — bringing that tab to the front"
    TID=$(pages | "$PY" -c "
import json,sys
try:
    for t in json.load(sys.stdin):
        if t.get('type') == 'page' and 'dispatch-console' in t.get('url',''):
            print(t['id']); break
except Exception:
    pass
" 2>/dev/null)
    [ -n "${TID:-}" ] && curl -s --max-time 5 \
        "http://127.0.0.1:$CDP_PORT/json/activate/$TID" >/dev/null 2>&1
    if [ -x "$PY" ]; then
        "$PY" - "$CDP_PORT" <<'PYEOF' 2>/dev/null
import asyncio, json, sys, urllib.request, websockets
port = sys.argv[1] if len(sys.argv) > 1 else "9222"
try:
    pages = json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json/list", timeout=5))
except Exception:
    sys.exit(0)
tab = next((t for t in pages if t.get("type") == "page"
            and "dispatch-console" in t.get("url", "")), None)
if not tab:
    sys.exit(0)
JS = ("(() => { const c = document.querySelector('#rememberUn');"
      " if (!c) return 'no-checkbox';"
      " if (c.checked) return 'already-on';"
      " c.click(); return c.checked ? 'ticked' : 'failed'; })()")
async def go():
    async with websockets.connect(tab["webSocketDebuggerUrl"],
                                  open_timeout=8) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS,
                                             "returnByValue": True}}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if m.get("id") == 1:
                v = (m.get("result", {}).get("result", {}) or {}).get("value")
                if v == "ticked":
                    print("   Remember me: ticked (makes the session persist)")
                elif v == "already-on":
                    print("   Remember me: already on")
                return
try:
    asyncio.run(go())
except Exception:
    pass
PYEOF
    fi
    say "sign in there — 'Remember me' is set, so the session should survive"
    say "browser restarts."
fi

# ---------------- 3. tracker ----------------
echo "== 3/4 tracker =="
if [ "$FG" = 1 ]; then
    # Foreground run: this terminal owns the tracker, so closing it stops the
    # tracker. The background service must release :8787 first, or the
    # foreground instance cannot bind it.
    if systemctl --user is-active fsl-tracker >/dev/null 2>&1; then
        say "stopping fsl-tracker.service so this terminal can own the tracker"
        systemctl --user stop fsl-tracker
        for i in $(seq 1 10); do dash_up || break; sleep 1; done
    fi
    PY="$ROOT/.venv/bin/python"
    [ -x "$PY" ] || fail "no venv python at $PY" \
        "Run ./linux/install_linux.sh first (it builds the venv and deps)."
    say "tracker running in THIS terminal — close it (or Ctrl-C) to stop"
    open_dashboard
    echo
    exec "$PY" "$ROOT/tracker.py"
fi
if dash_up; then
    say "already serving :$DASH_PORT — leaving it running"
else
    if systemctl --user cat fsl-tracker.service >/dev/null 2>&1; then
        systemctl --user start fsl-tracker
        say "started fsl-tracker.service"
    else
        PY="$ROOT/.venv/bin/python"
        [ -x "$PY" ] || fail "no venv python at $PY" \
            "Run ./linux/install_linux.sh first (it builds the venv and deps)."
        nohup "$PY" "$ROOT/tracker.py" >>"$ROOT/tracker_stdout.log" 2>&1 &
        say "started tracker.py in the background"
    fi
    for i in $(seq 1 30); do dash_up && break; sleep 1; done
    dash_up || fail "the tracker never started serving :$DASH_PORT" \
        "Check $ROOT/tracker_stdout.log" \
        "or:  journalctl --user -u fsl-tracker -n 40"
    say "dashboard port UP after ${i}s"
fi

# ---------------- 4. dashboard tab ----------------
echo "== 4/4 dashboard =="
open_dashboard

echo
echo "Ready. Console + dashboard are tabs in the same browser window."
echo "Keep that window OPEN during your shift (minimizing is fine)."