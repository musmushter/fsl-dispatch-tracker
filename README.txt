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
      "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\musta\AppData\Local\hermes\fsl_recon_profile" "https://aaa-ace.my.site.com/ACEContractorCommunity/s/dispatch-console"
   Log in if asked (session usually persists in that profile).
   Or just double-click:  start_chrome.bat
2. Tracker:  double-click  start_tracker.bat
   (runs: python C:\Users\musta\fsl_tracker\tracker.py)
3. Dashboard:  http://127.0.0.1:8787/dashboard.html  (any browser; auto-refreshes)

Dedicated Chrome window may be minimized, but keep it OPEN. Closing it stops
the data flow. If the tracker loses the full day snapshot it auto-reloads the
console tab (watchdog, max once/10 min). If the session expires you get a
"LOGIN NEEDED" toast — log in through the FSL Chrome window only.

FILES
  tracker.py       the listener + alert engine + local dashboard server
  dashboard.html   the live table (served at localhost:8787)
  state.json       current snapshot (dashboard reads this)
  events.jsonl     audit log: every status change, alert fired, watchdog reload
  recon/           capture scripts + captured traffic from the build session

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
ONE CLICK:  double-click  SETUP.bat
  (installs Python 3.11 + Chrome if missing, pip packages, Start Menu
  shortcuts, then opens the console for first login — just log in with
  your own AAA credentials and you're done)

Manual steps, if you prefer:
1. Install Python 3.11+ (python.org, tick "Add to PATH") and Google Chrome.
2. Get this folder (git clone), then:
      pip install websockets
3. Start the console Chrome (start_chrome.bat) and log in with YOUR OWN AAA
   credentials. Each person runs their own tracker against their own console
   session — the tracker only READS what your console receives.
4. Start the tracker (start_tracker.bat) and open
      http://127.0.0.1:8787/dashboard.html
5. Windows toasts fire from tracker.py (fire_toast); no extra setup.

SHARING / UPDATES
- Code lives in git (github.com/musmushter/fsl-dispatch-tracker, private).
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
  AT LOC / AT DROP OFF / MOVING are board states, not alerts: a standstill
  within 300 m of the service (or of the tow pair's drop-off leg) suppresses
  standstill alerts — the driver probably forgot to flip status.

REPORT BUTTON (drivers section)
Each row has a Report button that copies a dispatch-ready message to the
clipboard: driver + call + status duration + ETA ("ETA n mins" / "no ETA")
+ "not answering" (always included; delete manually when wrong) + movement
("not moving for N mins" when En Route standstill, "not moving on map" when
Dispatched standstill, "he is moving but didn't change status" when moving
while Dispatched or sitting AT LOC/AT DROP OFF). If the ETA window has
passed, the report switches to the 3-line late format ("He exceeded ETA
n mins ago" + "Mbr needs an update on ETA"), adding "and not close to srv"
only when the driver is >3.3 km out.

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
alert (same text as the Windows toasts). Read-only: viewers cannot change
anything; the link only exposes the dashboard, not your machine.
