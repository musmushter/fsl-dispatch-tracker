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
if pages | grep -q 's/dispatch-console"'; then
    say "console tab already open"
else
    say "console tab not loaded — opening it"
    curl -s -X PUT --max-time 10 "http://127.0.0.1:$CDP_PORT/json/new?$CONSOLE_URL" \
        >/dev/null 2>&1 || say "could not open it via CDP (open it by hand)"
    sleep 3
fi
# Only the CONSOLE being on a login redirect is worth warning about. A
# persistent login.salesforce.com helper tab is always present and means
# nothing, so match the redirect's own startURL, not the bare word 'login'.
if pages | grep -q 'login?[^"]*dispatch-console'; then
    say "console needs a sign-in — bringing that tab to the front"
    TID=$(pages | "$ROOT/.venv/bin/python" -c "
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
    say "sign in there. Tick 'Remember me' and the session then survives"
    say "browser restarts, so this should be rare."
fi

# ---------------- 3. tracker ----------------
echo "== 3/4 tracker =="
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
if pages | grep -q "$DASH_URL"; then
    say "dashboard tab already open"
else
    curl -s -X PUT --max-time 10 "http://127.0.0.1:$CDP_PORT/json/new?$DASH_URL" \
        >/dev/null 2>&1 && say "opened in the console browser" \
                            || say "could not open the tab — go to $DASH_URL"
fi

echo
echo "Ready. Console + dashboard are tabs in the same browser window."
echo "Keep that window OPEN during your shift (minimizing is fine)."