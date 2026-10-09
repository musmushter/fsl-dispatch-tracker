FSL DISPATCH TRACKER — OPERATIONS
=================================

WHAT IT IS
Watches the AAA dispatch console (Salesforce FSL) by listening to the network
responses the console itself receives — it never logs in, never sends requests
to Salesforce, and never changes any data. It builds a live table of every
active driver's first (leftmost, non-grey, non-RAP) service and alerts you:

  DISPATCH_OVERDUE   Dispatched > 10 min without En Route            [toast+sound]
  STANDING_STILL     En Route only: GPS moved <150 m for >10 min     [toast+sound]
  FAR_AWAY           En Route standstill AND >5 km from service      [toast+sound]
  MEMBER_WAITING     Spotted/waiting 60+ min                         [dashboard only]
On Location / Tow Loaded / In Tow drivers never alert on movement (they are
supposed to be still); the Movement column still shows their state.
En Route standstill AT the service (within 300 m) also never alerts — the
driver probably just forgot to flip status; the Movement column shows AT LOC.

HOW TO START (after reboot, in this order)
1. Console Chrome (required — the tracker listens to it):
      double-click   windows\start_chrome.bat
   (launches Chrome with --remote-debugging-port=9222 and its own
   console_profile; log in if asked — the session usually persists there)
2. Tracker:  double-click  windows\start_tracker.bat
3. Dashboard:  http://127.0.0.1:8787/dashboard.html  (any browser; auto-refreshes)

On Linux (CachyOS / Arch) see the LINUX section at the end of this file.

Dedicated Chrome window may be minimized, but keep it OPEN. Closing it stops
the data flow. If the tracker loses the full day snapshot it auto-reloads the
console tab (watchdog, max once/10 min). If the session expires you get a
"LOGIN NEEDED" toast — log in through the FSL Chrome window only.

FILES
  tracker.py        the listener + alert engine + local dashboard server
  dashboard.html    the live table (served at localhost:8787)
  tests.py          regression suite (run it before trusting a change)
  README.txt        this file
  windows\          everything Windows-only — the only folder Windows users touch
      SETUP.bat               one-click install (start here on a new machine)
      setup_tracker.ps1       the installer it runs
      start_chrome.bat        console Chrome launcher  (regenerated per machine)
      start_tracker.bat       tracker launcher         (regenerated per machine)
      diagnose.bat            diagnostic run, prints why something fails
      share_link.bat          temporary read-only share link
      tools\cloudflared.exe   the tunnel share_link.bat uses
  linux\            everything Linux-only
      install_linux.sh        one-shot install: venv + deps + tests + service
      start.sh                browser + console + tracker + dashboard, in one go
      share_link.sh           share from a terminal (see SHARING below)
      fsl-tracker.service.in  systemd user unit template
  state*.json       current snapshot (dashboard reads this)
  events*.jsonl     audit log: every status change, alert fired, watchdog reload

Both launcher pairs cd to the REPO ROOT (one level up from their own folder),
so tracker.py, console_profile and the runtime state files all live together at
the root and the two platforms share them without duplicating anything.

TUNING (edit constants at top of tracker.py)
  DISPATCH_OVERDUE_MIN = 10    STANDSTILL_MIN = 10    STANDSTILL_RADIUS_M = 150
  FAR_MIN_ENROUTE = 15         FAR_DISTANCE_M = 5000  MEMBER_WAIT_MIN = 60
  GPS_STALE_MIN = 10           (dashboard flags GPS older than this in red)

TIMEZONE / ENCODING (critical, validated 2026-09-04)
Salesforce FSL Gantt payloads encode SchedStartTime/SchedEndTime/PTA__c/
LastKnownLocationDate as the datetime's CENTRAL WALL CLOCK printed as if it
were UTC (exactly -5h CDT / -6h CST from true epoch). LastModifiedDate and
delta updateTime ARE true epoch. tracker.py normalizes this at ingestion
(sf_ms_to_epoch, offset auto-follows DST via America/Chicago). If display
times ever look 5-6h off again, check the offset constant first.

ETA COLUMN (replaces PTA)
Two sources, merged:
  1. Passive: feeds the console loads itself (you open a feed or the
     watchdog reload opens one).
  2. AUTO-FETCH (added 2026-09-04 late): every ~22 s the tracker fetches the
     service feed page itself through the console tab's own logged-in session
     (CDP Runtime.evaluate + fetch, same-origin) for followed services that
     have no ETA or a >45-min-old one, one service at a time, each retried at
     most every 10 min. Rounds through all Dispatched/En Route/On-Location
     services. Parses the newest "ETA n[-m] MIN" comment -> window = comment
     time + n..m minutes. Shown as e.g. "04:54 PM-4:59 PM"; red * = comment
     older than 45 min. If no ETA comment exists in the feed, the column
     stays empty — many services simply have none. MIF/NAP prefixes ignored.
     Every fetch is logged in events.jsonl ("eta_fetch" with has_eta flag)
     so you can verify it's reading feeds correctly.

NOTES / LIMITS
- "status age" for services already in a status when the tracker starts is
  estimated from Salesforce's LastModifiedDate (the last time ANY field of the
  appointment changed), so it can slightly understate true status age.
  Status changes observed live are exact.
- GPS comes from the same live positions feed the console map uses; if the
  console shows a stale dot, the tracker sees the same staleness (GPS age
  column shows it). Forced map updates from driver status changes arrive here
  exactly as they appear in the console.
- Distances are straight-line (haversine), matching your "far" rule.
- Read-only by construction: no credentials stored, no requests sent to
  Salesforce. Volume impact on the org: zero extra requests.

SETUP ON A NEW MACHINE (colleague install)
ONE CLICK:  double-click  windows\SETUP.bat
  (installs Python 3.11 + Chrome if missing, pip packages, Start Menu
  shortcuts, then opens the console for first login — just log in with
  your own AAA credentials and you're done)

Manual steps, if you prefer:
1. Install Python 3.11+ (python.org, tick "Add to PATH") and Google Chrome.
2. Get this folder (git clone), then:
      pip install websockets
3. Start the console Chrome (windows\start_chrome.bat) and log in with YOUR OWN
   AAA credentials. Each person runs their own tracker against their own
   console session — the tracker only READS what your console receives.
4. Start the tracker (windows\start_tracker.bat) and open
      http://127.0.0.1:8787/dashboard.html
5. Windows toasts fire from tracker.py (fire_toast); no extra setup.

SHARING / UPDATES
- Code lives in git (github.com/musmushter/fsl-dispatch-tracker).
- Layout: tracker.py / dashboard.html / tests.py at the root; platform files
  under windows\ and linux\. Windows users only ever touch windows\.
- Pull before your shift to get fixes:  git pull
- state.json / events.jsonl / logs are local-only (gitignored) — your runtime
  data never mixes with anyone else's.
- Tunables are the constants at the top of tracker.py; keep local tweaks
  uncommitted or commit them under your own branch.

ALERTS (current set)
  DISPATCH_OVERDUE   Dispatched >10 min without En Route          [toast]
  STANDING_STILL     En Route standstill >10 min (<150 m span)   [toast]
  FAR_AWAY           standstill >5 km from service               [toast]
  ETA_EXPIRED        posted ETA window passed + >6.7 km out      [toast]
  MEMBER_WAITING     Spotted 60+ min                             [board only]
  The four toast alerts are the tracker's own (urgent) alerts; MEMBER_WAITING is
  amber. RED ALERT — the board marker and the report's "RED ALERT SERVICE" — is
  NOT one of them: it follows the CONSOLE's own Gantt colour. Every service's
  GanttColor__c is read from the console payload (normal bars are #228B22
  green) and a red/orange (high-risk) bar is marked. So a green bar carrying a
  DISPATCH_OVERDUE alert is NOT a red alert, and a red bar IS marked even when
  the tracker raises nothing. The Flags-column badge shows the console colour
  in its tooltip.
  AT LOC / AT DROP OFF / MOVING are board states, not alerts: a standstill
  within 300 m of the service (or of the tow pair's drop-off leg) suppresses
  standstill alerts — the driver probably forgot to flip status.

REPORT BUTTON (drivers section)
Each row has a Report button that copies a dispatch-ready message to the
clipboard: driver + call + status duration + ETA ("ETA n mins" / "no ETA")
+ "not answering" (always included; delete manually when wrong) + movement
("not moving for N mins" when En Route standstill, "not moving on map" when
Dispatched standstill). If the driver is moving while Dispatched, or sitting
AT LOC / AT DROP OFF, that clause goes on its OWN line, capitalised:
  "He is moving but didn't change status"
If the ETA window has passed, the report switches to the 3-line late format
+ "Mbr needs an update on ETA", adding "and not close to srv" only when the
driver is >3.3 km out. An ETA that passed LESS THAN 3 MIN AGO reads
"ETA just exceeded" instead of "He exceeded ETA n mins ago" — and a service
the CONSOLE paints red/orange says "RED ALERT SERVICE" right after the call
ID, in both the normal and the late format.

CONSOLE DAY GUARD
The console's Gantt (DHTMLX) day selector follows the BROWSER's local clock, and
a tracker reload resets it there — so on a machine running Egypt time the board
lands a day ahead of Houston after every hourly reload, and the app then streams
the wrong day's services (the console's own 'Today' button jumps to that local
day too, so it cannot be used to fix it). After every reload, and every 10 min,
the tracker reads the displayed day and steps the prev/next arrows until it
reads today in AMERICA/CHICAGO, logging a `day_fix` event when it had to move.
It never touches a day that is already correct.

CALLBACK SECTION
Below the drivers: services cleared in the last 12 h (newest first, 20 at a
time + Load more). Member name/benefit auto-fetched from the work order;
member waited = Spotted/Scheduled/Dispatched -> On Location, on-scene =
On Location -> Cleared, both read from the SA chatter feeds (exact, not
estimated). '–' = the feed genuinely lacks the timestamps. Canceled
services appear flagged. RAP services and confirmed-external resources are
excluded entirely.

SHARE A READ-ONLY LINK (remote viewing)
Double-click  share_link.bat  (tracker must be running). It prints a public
trycloudflare.com link — anyone with it can VIEW the live dashboard from
anywhere, no install or login. The link is temporary: it changes every time
you run share_link.bat and dies when you close its window. To let viewers
hear about alerts, the dashboard header has a 'Enable alerts' bell — they
click it once, allow browser notifications, and get a popup per urgent
alert (same text as the Windows toasts). The link only exposes the dashboard,
not your machine — but note that any viewer can also reach tracker.py's
POST /settings route, which is deliberately NOT authenticated (it is how every
viewer's gear menu stays in sync), so share the link only with people you would
let change the alert settings.

LINUX (CachyOS / Arch, systemd)
===============================
Same tracker, same dashboard — only the launchers differ. Everything Linux is
under linux\; tracker.py, dashboard.html and the runtime files stay at the root.

Install once:
    git clone <repo> && cd fsl_tracker
    ./linux/install_linux.sh
That creates .venv, installs websockets + tzdata, runs the regression suite,
and installs the systemd user unit generated for this machine's path.

Daily use — one command does all of it:
    ./linux/start.sh
It reuses a browser already listening on :9222, a console tab already open, and
a tracker already serving :8787 — running it twice changes nothing. Then it
opens the dashboard as a tab in that same browser window. Keep the window OPEN
during your shift (minimizing is fine).

The unit restarts the tracker if it dies and logs to journald:
    journalctl --user -u fsl-tracker -f
To keep it running after logout:   sudo loginctl enable-linger $USER

Stopping / controlling the tracker (Linux)
The tracker is a systemd USER SERVICE, so there is no window to close — that is
the job the Windows cmd window did. Controlling it:
    systemctl --user stop fsl-tracker        <- this IS "closing the tracker"
    systemctl --user start fsl-tracker
    systemctl --user restart fsl-tracker
    systemctl --user status fsl-tracker
    journalctl --user -u fsl-tracker -f      <- watch its output live
    tail -f tracker_stdout.log               <- same thing, from the file
Stopping the tracker does NOT touch the browser; the console tab stays open and
logged in, so restarting is instant. Stopping the BROWSER is what needs a fresh
login (though 'Remember me' plus the session-restore pref in start.sh usually
avoid even that).

Prefer the Windows-style window you can close? Run it in the foreground:
    ./linux/start.sh --fg
The tracker then stays in that terminal; closing the terminal (or Ctrl-C)
stops it. Any background service is stopped first so the terminal can own
port 8787.

Browser: start.sh prefers a system chromium / google-chrome and falls
back to the Hermes-bundled Chrome for Testing, resolved by glob so a Hermes
update that renumbers it cannot break the launcher. It always launches its own
dedicated, NON-default profile (console_profile) — that is what keeps Chrome
136+ from demanding an "Allow remote debugging?" click on every attach. Do NOT
instead point the tracker at a browser you enabled debugging on via
chrome://inspect/#remote-debugging: that mode is popup-gated (Chrome asks on
every attach and cannot be told to remember), so it cannot run unattended.

Alerts on Linux use notify-send instead of Windows toasts (same text) and, like
the Windows toast — whose PowerShell ends with SystemSounds::Exclamation — they
now PLAY A SOUND too. notify-send on its own is silent, which is why the very
same alert was quiet on Linux while Windows beeped. The sound follows your
desktop's sound theme via libcanberra (canberra-gtk-play -i dialog-warning),
falling back to paplay / pw-play on the freedesktop dialog-warning.oga file.

It needs no extra config and it obeys the mute settings for free, because muted
alert types never reach fire_toast in the first place. Overrides, if you want
them: FSL_TOAST_SILENT=1 silences it, FSL_TOAST_SOUND=<file> plays your own
file, FSL_TOAST_SOUND_EVENT=<name> picks a different libcanberra event (try
alarm-clock-elapsed or bell).

This is the DESKTOP toast. The dashboard's own 'Enable alerts' browser popups
are separate — they are whatever the browser and your notification daemon do
with them.

SHARING: use the Share button in the dashboard header (next to Settings). Same
on both platforms. It opens a small panel with Create link / Copy link / Close
share:

  - the TRACKER starts a Cloudflare quick tunnel and shows the public
    trycloudflare.com link; Copy link puts it on your clipboard;
  - cloudflared is a CHILD of the tracker process, so STOPPING THE TRACKER
    CLOSES THE LINK — a share link cannot outlive the tracker;
  - the link can take a minute or two to become reachable while its DNS record
    is created; the panel says whether it is live or still starting;
  - a viewer reading the board THROUGH the link gets no share controls:
    /share reports local=false for anything arriving via the tunnel, and
    /share/start + /share/stop are refused (403) for those requests.

cloudflared must be present: a system cloudflared, else the repo's own copy
(linux\tools\cloudflared). If the panel says 'cloudflared not found', run
./linux/share_link.sh once — it downloads the static binary into linux\tools\.
$FSL_CLOUDFLARED overrides the lookup.

The CLI equivalent, ./linux/share_link.sh (windows\share_link.bat on Windows),
starts the same quick tunnel from a terminal instead, independent of the
tracker. It ties the tunnel to that window: closing it (or Ctrl-C) ends the
link, and it also exits if the launcher it was started from goes away, so a link
cannot quietly keep publishing your board. FSL_SHARE_KEEP=1 opts out of that.

Same warning as Windows: the link reaches tracker.py's HTTP routes, and
POST /settings (the alert preferences) is deliberately unauthenticated, so any
viewer can change them. Share only with people you would let do that.

Not yet ported to Linux: diagnose.bat.
