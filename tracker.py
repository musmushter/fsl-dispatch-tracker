"""
FSL Dispatch Tracker — listens to the dispatch console's own network traffic
via CDP (dedicated Chrome on port 9222) and maintains live state:
  - per-driver live GPS + position history (updatedLivePositions)
  - per-service status/times/address/driver (updatedGanttServices, calendar loads)
  - alert rules (dispatch overdue, standing still, far away, member waiting)
Writes state.json for the dashboard, events.jsonl as an audit log, and fires
Windows toasts for urgent alerts.

Read-only: it never sends requests to Salesforce. It only reads responses the
console itself already received.
"""
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo
import json
import math
import re
import subprocess
import time
import traceback
import urllib.request

import websockets

# Windows Pythons ship no timezone database; the `tzdata` pip package fills
# the gap. Import order: pip tzdata -> system db -> fail with a fix hint.
try:
    import tzdata  # noqa: F401  (registers its zoneinfo key)
except ImportError:
    pass
try:
    ZONE = ZoneInfo("America/Chicago")
except Exception:
    print("FATAL: no timezone database. Fix:  pip install tzdata", flush=True)
    raise SystemExit(1)

VERSION = "1.1"

BASE = r"C:/Users/musta/fsl_tracker"
STATE_FILE = BASE + r"/state.json"
SETTINGS_FILE = BASE + r"/settings.json"
# ---- per-account scoping ----
# The community hosts multiple AAA teams (separate portal logins). All runtime
# data is scoped by ACCOUNT_KEY = the service-territory id that dominates the
# gantt payloads, so switching logins can't bleed calls between teams.
ACCOUNT_KEY = None
ACCOUNT_FILES = {}  # label -> path template

def current_paths():
    return account_paths(ACCOUNT_KEY)

def account_paths(key):
    """File paths for an account key: state_<key8>.json etc. Legacy (no key)
    files keep their original names."""
    k = (key or "")[:8] or ""
    sfx = ("_" + k) if k else ""
    return {
        "state":   BASE + f"/state{sfx}.json",
        "events":  BASE + f"/events{sfx}.jsonl",
        "etas":    BASE + f"/etas{sfx}.json",
        "settings":BASE + f"/settings{sfx}.json",
    }
EVENTS_FILE = BASE + r"/events.jsonl"

# ---------------- alert customization ----------------
# settings.json: {"muted_types": [...], "muted_drivers": [...]} — user-editable
# from the dashboard gear menu (POST /settings) or by hand. Muted alert types
# are dropped from state.json AND never fire Windows toasts.
DEFAULT_SETTINGS = {"muted_types": [], "muted_drivers": [],
                    "rules": [], "disabled_builtins": [],
                    "builtin_overrides": {},
                    "numeric_names_external": True}
SETTINGS = dict(DEFAULT_SETTINGS)

def load_settings():
    global SETTINGS
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        SETTINGS = {"muted_types": list(data.get("muted_types") or []),
                    "muted_drivers": list(data.get("muted_drivers") or []),
                    "rules": list(data.get("rules") or []),
                    "disabled_builtins": list(data.get("disabled_builtins") or []),
                    "builtin_overrides": dict(data.get("builtin_overrides") or {}),
                    "numeric_names_external": bool(data.get("numeric_names_external", True))}
    except Exception:
        SETTINGS = dict(DEFAULT_SETTINGS)

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f, indent=1)
    except Exception as e:
        print("settings save failed:", e, flush=True)

# ---------------- custom alert rules ----------------
_OPS = {
    "eq":       lambda a, b: a == b,
    "ne":       lambda a, b: a != b,
    "gt":       lambda a, b: a is not None and a > b,
    "gte":      lambda a, b: a is not None and a >= b,
    "lt":       lambda a, b: a is not None and a < b,
    "lte":      lambda a, b: a is not None and a <= b,
    "contains": lambda a, b: b.lower() in str(a or "").lower(),
}

def builtin_threshold(atype, key, default):
    """User-tuned threshold for a built-in alert (settings gear -> Modify)."""
    try:
        v = (SETTINGS.get("builtin_overrides") or {}).get(atype, {}).get(key)
        return default if v is None else float(v)
    except Exception:
        return default

def rule_matches(rule, ctx):
    """True when every (or any, per rule.match) condition holds for ctx."""
    conds = rule.get("conds") or []
    if not conds:
        return False
    fn = any if rule.get("match") == "any" else all
    results = []
    for c in conds:
        val = ctx.get(c.get("field"))
        try:
            thr = c.get("value")
            if isinstance(val, bool):
                thr = str(thr).strip().lower() in ("true", "1", "yes")
            elif isinstance(val, (int, float)) and isinstance(thr, str):
                thr = float(thr)
        except Exception:
            thr = c.get("value")
        op = _OPS.get(c.get("op") or "eq")
        try:
            results.append(bool(op(val, thr)) if op else False)
        except Exception:
            results.append(False)
    return fn(results)

# ---------------- alert configuration ----------------
DISPATCH_OVERDUE_MIN = 10      # Dispatched > 10 min without En Route
STANDSTILL_MIN = 10            # no movement > STANDSTILL_RADIUS_M for > 10 min
STANDSTILL_RADIUS_M = 150      # GPS jitter allowance
AT_LOCATION_RADIUS_M = 300     # standstill this close to the service = forgot
                               # to flip status -> no alert (AT LOC on board)
FAR_DISTANCE_M = 5000          # standstill beyond this = FAR_AWAY (urgent variant)
MEMBER_WAIT_MIN = 60           # Spotted for 60+ min (dashboard only)
STALE_SCHED_HOURS = 3          # a service still 'Scheduled' this long after
                               # its sched time is dead (rescheduled/driver
                               # off) -> drop from the board

GPS_STALE_MIN = 10             # live position older than this = stale
ETA_EXPIRED_DIST_M = 6700      # ETA passed and still farther than this
                               # (~10 min at 40 km/h avg) = ETA_EXPIRED alert

# statuses marking the driver ARRIVED at the member / vehicle
ARRIVED_STATUSES = ("On Location", "Tow Loaded", "In Tow")
# statuses that complete a service (for the cleared-services log; 'Canceled'
# is deliberately excluded — a canceled call is not work performed)
DONE_STATUSES = ("Cleared", "Complete", "Tow Complete")


def now_ms():
    return int(time.time() * 1000)


def ms_to_central(ms):
    if not ms:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, ZONE).strftime("%I:%M %p")


# Salesforce FSL Gantt payloads encode SchedStartTime/SchedEndTime/PTA__c/
# LastKnownLocationDate as the datetime's US-Central WALL CLOCK printed as if
# it were UTC (validated 2026-09-04 against console ground truth).
# LastModifiedDate and delta updateTime are true epoch — do NOT shift those.
def sf_ms_to_epoch(ms):
    """Convert a wall-as-UTC Salesforce ms value to true epoch ms. The Central
    offset is computed per call so a tracker left running across a DST change
    keeps converting wall-clock values correctly."""
    if not ms:
        return None
    off = -int(dt.datetime.now(ZONE).utcoffset().total_seconds()) * 1000
    return ms + off


ETA_RANGE_RE = re.compile(r"ETA[^0-9\n]{0,12}?(\d{1,3})\s*(?:[-–/]|to\b)\s*(\d{1,3})\s*(?:m(?:in)?(?:s|utes?)?)?\b", re.I)
ETA_SINGLE_RE = re.compile(r"ETA[^0-9\n]{0,12}?(\d{1,3})\s*(?:m(?:in)?(?:s|utes?)?)?\b", re.I)
# 2ND KMI: driver's renewed commitment after a second contact attempt —
# '2ND KMI ETA UPDT .. driver will be there in 60-75 minutes or less'.
# Numbers need a time unit so call-IDs/random digits can't match.
KMI2_RANGE_RE = re.compile(r"2\s*nd\s*kmi(?:(?!\d).){0,200}?(\d{1,3})\s*(?:[-–/]|to\b)\s*(\d{1,3})\s*(m(?:in)?(?:s|utes?)?)\b", re.I | re.S)
KMI2_SINGLE_RE = re.compile(r"2\s*nd\s*kmi(?:(?!\d).){0,200}?(\d{1,3})\s*(m(?:in)?(?:s|utes?)?)\b", re.I | re.S)
# external/contractor resources arrive as "198114 - Jamal Awawda" (bare
# numeric prefix, no phone block) — user wants them off the tracker entirely
EXTERNAL_NAME_RE = re.compile(r"^\d{3,}\s*[-–]\s*\S")
# confirmed-external resource ids (kept off the board regardless of name shape)
EXTERNAL_RESOURCE_IDS = {"174584"}
FEED_TEXT_RE = re.compile(r'class="feeditemtext[^"]*"[^>]*>(.*?)</span>', re.S)
FEED_TIME_RE = re.compile(r"(Today|Yesterday) at (\d{1,2}):(\d{2})\s*([AP]M)", re.I)
# SA feed status changes: 'changed Status from Scheduled to Dispatched.'
# (feed text has irregular spacing: 'changed  Status ... En Route .')
SA_CHANGE_RE = re.compile(r"changed\s+Status\s+from\s+([^.<|]+?)\s+to\s+([^.<|]+?)\s*\.", re.I)


def feed_post_epoch(tm):
    """FEED_TIME_RE match ('Today|Yesterday at H:MM AM/PM') -> true epoch ms
    (the feed prints Central wall clock)."""
    hh = int(tm.group(2)) % 12 + (12 if tm.group(4).upper() == "PM" else 0)
    wall = dt.datetime.now(ZONE).replace(hour=hh, minute=int(tm.group(3)),
                                         second=0, microsecond=0)
    if tm.group(1).lower() == "yesterday":
        wall -= dt.timedelta(days=1)
    return int(wall.timestamp() * 1000)


def parse_status_times_from_feed(body):
    """SA chatter feed -> ({status: epoch_ms}, reassigned:int). Times are the
    newest 'changed Status ... to X' post per status. `reassigned` counts
    Dispatched/En Route posts (>1 = the call changed hands); audit-log
    information only."""
    clean = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = clean.replace("&nbsp;", " ")
    out = {}
    reassigned = 0
    for m in SA_CHANGE_RE.finditer(clean):
        status = m.group(2).strip()
        tm = FEED_TIME_RE.search(clean, m.end(), m.end() + 400)
        if not tm:
            continue
        posted = feed_post_epoch(tm)
        # keep the newest post per status
        if status not in out or posted > out[status]:
            out[status] = posted
        if status in ("Dispatched", "En Route"):
            reassigned += 1
    return out, reassigned


def eta_ok(body):
    """Quick check: does this feed HTML contain any ETA comment?
    Works on raw HTML (tag-stripped first so entities/attributes don't matter)."""
    clean = re.sub(r"<[^>]+>", " ", body)
    clean = clean.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return bool(ETA_RANGE_RE.search(clean) or ETA_SINGLE_RE.search(clean))


def parse_eta_from_feed(body, ref_epoch_ms):
    """Find the newest 'ETA n[-m] MIN' comment in a chatter feed HTML body.
    Returns dict(low, high, posted, text) in true epoch ms, or None."""
    best = None
    for m in FEED_TEXT_RE.finditer(body):
        clean = re.sub(r"<[^>]+>", " ", m.group(1))
        clean = clean.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        em = ETA_RANGE_RE.search(clean) or ETA_SINGLE_RE.search(clean)
        if not em:
            # 2ND KMI commitments carry the driver's renewed ETA without the
            # 'ETA n MIN' keyword ('...driver will be there in 60-75 minutes')
            em = KMI2_RANGE_RE.search(clean) or KMI2_SINGLE_RE.search(clean)
            if not em:
                continue
            # KMI2 match spans from '2ND KMI' to the numbers; narrow to the
            # numeric tail so lo/hi parse from the numbers, and the stored
            # text stays clean
            # narrow to the numbers, skipping the leading '2' of '2ND':
            pairs = re.search(r"(\d{1,3})\s*(?:[-–/]|to\b)\s*(\d{1,3})", em.group(0))
            if pairs:
                em = pairs
            else:
                singles = list(re.finditer(r"(\d{1,3})", em.group(0)))
                em = singles[-1] if singles else None
                if em is None:
                    continue
        lo = int(em.group(1))
        hi = int(em.group(2)) if em.lastindex and em.lastindex >= 2 else lo
        # post time: first 'Today/Yesterday at H:MM' after the text block
        tm = FEED_TIME_RE.search(body, m.end(), m.end() + 4000)
        if not tm:
            continue
        posted = feed_post_epoch(tm)
        cand = {"low": posted + lo * 60000, "high": posted + hi * 60000,
                "posted": posted, "text": em.group(0).strip()}
        if best is None or cand["posted"] > best["posted"]:
            best = cand
    return best


WO_CONTACT_RE = re.compile(r"Contact(?:\s*\|)+\s*([A-Za-z .,'-]{3,60}?)\s*(?:\||$)")
WO_BENEFIT_RE = re.compile(r"Membership Benefit Level(?:\s*\|)+\s*([A-Za-z ]{2,30}?)\s*(?:\||$)")
WO_WTYPE_RE = re.compile(r"Work Type(?:\s*\|)+\s*([A-Za-z0-9 ./-]{2,60}?)\s*(?:\||$)")


def parse_wo_lightbox(body):
    """Pull Contact (member name), benefit level, work type from the WO
    lightbox HTML (pipe-normalized text)."""
    txt = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    txt = re.sub(r"<[^>]+>", " | ", txt)
    txt = " ".join(txt.split())
    out = {}
    m = WO_CONTACT_RE.search(txt)
    if m:
        out["member_name"] = m.group(1).strip()
    m = WO_BENEFIT_RE.search(txt)
    if m:
        out["benefit"] = m.group(1).strip()
    m = WO_WTYPE_RE.search(txt)
    if m:
        out["work_type"] = m.group(1).strip()
    return out


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def coerce_text(v):
    """Scalar-ish fields (WorkType etc.) sometimes arrive as differential
    fragments ({'r': n}) instead of strings — normalize to text or None."""
    v = unwrap(v)
    if isinstance(v, str):
        return v.strip() or None
    return None


def coerce_name(v):
    """ResourceName normally arrives as a string, but some bulk payloads
    deliver it as {'Name': ...} / {'s':n,'v':{...}} — normalize to a string.
    Salesforce prints decorative prefixes ('*Bryce Williams', '-Konitz
    Hughes') on some resources — strip them (external contractors like
    '198114 - Jamal Awawda' are filtered separately by EXTERNAL_NAME_RE,
    which runs on the RAW name before this strips anything)."""
    v = unwrap(v)
    if isinstance(v, dict):
        v = v.get("Name") or v.get("name") or ""
    v = str(v).strip() if v else None
    if v:
        v = re.sub(r"^[\*\-]+\s*", "", v).strip() or None
    return v


def unwrap(v):
    """Salesforce gantt JSON wraps values as {"s": n, "v": real}."""
    if isinstance(v, dict) and set(v.keys()) == {"s", "v"}:
        return v["v"]
    return v


# ---------------- state ----------------
class State:
    def __init__(self):
        self.services = {}      # sa_id -> dict
        self.drivers = {}       # resource_id -> {name, pos:{lat,lng,t}, history:[...]}
        self.alerts = {}        # alert_key -> alert dict (active)
        self.events_fh = open(EVENTS_FILE, "a", encoding="utf-8")
        self.last_data_ts = 0
        self.last_full_ts = 0   # last full-day load (bulk ingest)
        self.last_reload_ts = 0 # last time watchdog reloaded the console tab
        self.login_required = False
        self.etas = {}          # parent_or_sa_id -> {"low","high","posted","text"}
        try:
            with open(current_paths()["etas"], encoding="utf-8") as f:
                self.etas = json.load(f)
        except Exception:
            pass
        self.eta_fetch = {}     # sa_id -> last autofetch attempt ts
        self._acct_cand_key = None
        self._acct_cand_n = 0
        # enrichment via the WO lightbox (Contact/member name); the gantt and
        # SA feeds never carry a person name
        self.wo_cache = {}      # parent_id -> {"name":..., "benefit":..., fetched ts}
        self.wo_fetch = {}      # parent_id -> last attempt ts
        self.feed_times = {}    # sa_id -> {status: epoch_ms} from SA feed
        self.feed_time_fetch = {}  # sa_id -> last attempt ts
        self.dropoff_cache = {}     # sa_id -> "street, city" from lightbox
        self.dropoff_fetch = {}     # sa_id -> last attempt ts
        self.last_movement = {} # resource_id -> "MOVING" | "STANDSTILL" | None
        self.driver_order = {}  # resource_id -> console/gantt appearance order

    def log_event(self, kind, **kw):
        rec = {"ts": now_ms(), "kind": kind, **kw}
        self.events_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.events_fh.flush()

    # ---------- data ingestion ----------
    def ingest_delta(self, res):
        self.detect_account(res)
        self.last_data_ts = now_ms()
        lp = unwrap(res.get("updatedLivePositions")) or {}
        for rid, wrap in lp.items():
            pos = unwrap(wrap)
            if not isinstance(pos, dict):
                continue
            d = self.drivers.setdefault(rid, {"name": None, "pos": None, "history": []})
            lat, lng = pos.get("LastKnownLatitude"), pos.get("LastKnownLongitude")
            # LastKnownLocationDate is wall-as-UTC -> normalize to true epoch
            t = sf_ms_to_epoch(pos.get("LastKnownLocationDate")) or now_ms()
            if lat is None or lng is None:
                continue
            prev = d["pos"]
            d["pos"] = {"lat": lat, "lng": lng, "t": t}
            if not d["history"] or d["history"][-1]["t"] != t:
                d["history"].append({"lat": lat, "lng": lng, "t": t})
                # keep 3 hours
                cutoff = now_ms() - 3 * 3600 * 1000
                d["history"] = [h for h in d["history"] if h["t"] >= cutoff]

        gsvc = unwrap(res.get("updatedGanttServices")) or []
        for item in gsvc:
            svc = unwrap(item)
            if isinstance(svc, dict):
                self.ingest_service(svc)

        dgs = unwrap(res.get("deletedGanttServices")) or []
        for item in dgs:
            sid = unwrap(item)
            if isinstance(sid, str) and sid in self.services:
                self.services[sid]["IsDeleted"] = True

    def ingest_service(self, svc):
        f = unwrap(svc.get("Fields")) or svc
        sa_id = f.get("Id") or svc.get("Id")
        if not sa_id:
            return
        prev_status = self.services.get(sa_id, {}).get("status")
        status = f.get("Status")
        rec = self.services.get(sa_id, {
            "first_seen": now_ms(),
            "status_history": [],
            # seed with the record's own last-modified time so alerts don't
            # wait a full status-age after (re)attach; live transitions
            # overwrite this with the observed time
            "status_since": f.get("LastModifiedDate") or now_ms(),
        })
        rec.update({
            "sa_id": sa_id,
            "appt": f.get("AppointmentNumber"),
            "call_id": f.get("D3_Call_ID__c") or f.get("GanttLabel__c"),
            "work_type": coerce_text(f.get("WorkType")),
            "status": status,
            "status_category": f.get("StatusCategory"),
            # wall-as-UTC -> true epoch
            "sched_start": sf_ms_to_epoch(f.get("SchedStartTime")),
            "sched_end": sf_ms_to_epoch(f.get("SchedEndTime")),
            # ActualStartTime = when the driver really went On Location
            # (wall-as-UTC like other Sched* fields) — exact member wait even
            # for services cleared before the tracker started
            "actual_start": sf_ms_to_epoch(f.get("ActualStartTime")),
            "pta": sf_ms_to_epoch(f.get("PTA__c")),
            "street": f.get("Street"),
            "city": f.get("City"),
            "addr": f.get("FSL_Street_City__c"),
            "lat": f.get("Latitude"),
            "lng": f.get("Longitude"),
            "call_type": f.get("WO_Call_Type__c"),
            "work_type_id": coerce_text(f.get("WorkTypeId")),
            "subject": f.get("Subject"),
            "phone": f.get("Phone_Number__c"),
            "benefit": f.get("Member_Benefit_Level__c"),
            "vehicle": f.get("FSL_Member_Vehicle_Name__c"),
            "gantt_color": f.get("GanttColor__c") or f.get("FSL__GanttColor__c"),
            "resource_id": svc.get("Resource") or f.get("Service_Resource__c"),
            "resource_name": coerce_name(svc.get("ResourceName")) or coerce_name(f.get("Service_Resource__r")),
            "last_modified": f.get("LastModifiedDate"),
            "parent_id": f.get("ParentRecordId") or (svc.get("ParentFields") or {}).get("Id"),
            "related": svc.get("relatedService1") or f.get("Related_Service__c"),
            "reltype": svc.get("relationshipType"),
            "IsDeleted": f.get("IsDeleted", False),
        })
        # bulk-seeded records (full-day load) can't observe live flips: derive
        # arrival/completion estimates from LastModifiedDate so the cleared
        # log still has usable timestamps
        if prev_status is None:
            if status in ARRIVED_STATUSES and not rec.get("arrived_at"):
                rec["arrived_at"] = f.get("LastModifiedDate")
            if status in DONE_STATUSES and not rec.get("cleared_at"):
                rec["cleared_at"] = f.get("LastModifiedDate")
        if status and status != prev_status:
            rec["status_history"].append({"status": status, "t": now_ms(),
                                          "source": "watch"})
            # For a service we just learned about, keep the seeded
            # LastModifiedDate-based status_since (best estimate of when the
            # status actually started). Only a live observed flip gets "now".
            if prev_status is not None:
                rec["status_since"] = now_ms()
            # arrival / completion timestamps for the cleared-services log;
            # live-observed flips are reliable, bulk-seeded estimates are not
            # (LastModifiedDate is the record's last write, not the arrival)
            if status in ARRIVED_STATUSES and not rec.get("arrived_at"):
                rec["arrived_at"] = (now_ms() if prev_status is not None
                                     else (f.get("LastModifiedDate") or now_ms()))
                rec["arrived_live"] = prev_status is not None
            if status in DONE_STATUSES and not rec.get("cleared_at"):
                rec["cleared_at"] = (now_ms() if prev_status is not None
                                     else (f.get("LastModifiedDate") or now_ms()))
            self.log_event("status", appt=rec["appt"], call_id=rec["call_id"],
                           sa_id=rec["sa_id"], related=rec.get("related"),
                           reltype=rec.get("reltype"),
                           driver=rec["resource_name"] if "resource_name" in rec else rec["resource_id"],
                           status=status)
        if rec["resource_id"] and rec.get("resource_name"):
            d = self.drivers.setdefault(rec["resource_id"], {"name": None, "pos": None, "history": []})
            d["name"] = rec["resource_name"]
        # trim status history
        rec["status_history"] = rec["status_history"][-50:]
        self.services[sa_id] = rec

    def ingest_bulk(self, res):
        """Full-day loads (ResourceCalendar / initial Gantt): dict-shaped or list results."""
        self.last_data_ts = now_ms()
        self.last_full_ts = now_ms()
        self.login_required = False
        found = []
        self.detect_account(res)

        def walk(x):
            if isinstance(x, dict):
                if "AppointmentNumber" in x:
                    found.append(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(res)
        # remember console order: the sequence resources appear in the gantt
        # payload (the dispatch console renders them in this order)
        for svc in found:
            rid = unwrap(svc.get("Resource")) or (unwrap(svc.get("Fields")) or {}).get("Service_Resource__c")
            if rid and rid not in self.driver_order:
                self.driver_order[rid] = len(self.driver_order)
        for svc in found:
            self.ingest_service(svc)

    # ---------- exact status timing ----------
    def status_epoch(self, svc):
        """Best-known start time of the service's CURRENT status.
        Priority: live-observed flip > exact SA-feed post time > seeded
        LastModifiedDate (Salesforce bumps it on ANY field change, so it can
        read far too recent — Marcos' 24-min dispatch showed as 13)."""
        status = svc.get("status")
        hist = svc.get("status_history") or []
        observed = bool(hist) and hist[-1].get("status") == status \
            and hist[-1].get("source") == "watch"
        if observed and svc.get("status_since"):
            return svc["status_since"]
        ft = (getattr(self, "feed_times", None) or {}).get(svc.get("sa_id")) or {}
        if status and ft.get(status):
            return ft[status]
        if status == "Dispatched":
            # a live-observed En Route flip implies Dispatched started before it;
            # fall through to seed
            pass
        return svc.get("status_since") or svc.get("last_modified")

    # ---------- account scoping ----------
    TERR_RE = re.compile(r'"ServiceTerritoryId"\s*:\s*"(0Hh[0-9A-Za-z]{12,15})"')

    def detect_account(self, raw):
        """Derive the account key from the gantt payload's dominating territory
        id. On change: swap data files, wipe in-memory state, reload settings.
        Only FULL loads may switch accounts: single-resource getServices
        responses (one lane, possibly another team's territory peeking into the
        gantt) must never wipe the board mid-shift."""
        global ACCOUNT_KEY, SETTINGS
        text = raw if isinstance(raw, str) else json.dumps(raw)
        terrs = self.TERR_RE.findall(text)
        if not terrs:
            return
        counts = {}
        for t in terrs:
            counts[t] = counts.get(t, 0) + 1
        key = max(counts, key=counts.get)
        if key == ACCOUNT_KEY:
            return
        # account switches only on MULTI-resource loads (initial ResourceCalendar
        # / full-day gantt). A single-lane load is a peek, not a login switch.
        if text.count('"AppointmentNumber"') < 2 and ACCOUNT_KEY is not None:
            return
        # and the new key must win 3 consecutive multi-service bulks before we
        # wipe anything — a viewport-limited partial load must not nuke the board
        if ACCOUNT_KEY is not None:
            if key != self._acct_cand_key:
                self._acct_cand_key = key
                self._acct_cand_n = 1
            else:
                self._acct_cand_n += 1
            if self._acct_cand_n < 3:
                return
        self._acct_cand_key = None
        self._acct_cand_n = 0
        first = ACCOUNT_KEY is None
        ACCOUNT_KEY = key
        # close old events handle, open the scoped one
        try:
            self.events_fh.close()
        except Exception:
            pass
        self.events_fh = open(current_paths()["events"], "a", encoding="utf-8")
        # reset memory so nothing crosses accounts
        self.services.clear()
        self.drivers.clear()
        self.alerts.clear()
        self.etas.clear()
        self.eta_fetch.clear()
        self.driver_order.clear()
        self.wo_cache.clear()
        self.dropoff_cache.clear()
        self.cleared_log_list = getattr(self, "cleared_log_list", [])
        self.cleared_log_list.clear()
        load_settings()
        print(f"account: {key}{'  (first load)' if first else '  SWITCHED — state reset'}", flush=True)

    # ---------- derived view ----------
    def is_external(self, rec):
        """Contractor resources ('198114 - Jamal Awawda') — not our drivers ON THE
        DEFAULT ACCOUNT. Teams that work WITH All American see those units named
        '104416 - Curtis Kees'; per-account settings (numeric_names_external=false)
        keep them on the board. Resource-id blocklist always applies."""
        if rec.get("resource_id") in EXTERNAL_RESOURCE_IDS:
            return True
        if not SETTINGS.get("numeric_names_external", True):
            return False
        return bool(EXTERNAL_NAME_RE.match(rec.get("resource_name") or ""))

    def cleared(self, rec):
        if (rec.get("status") in DONE_STATUSES or rec.get("status") == "Canceled"
                or rec.get("status_category") in ("Completed",)
                or rec.get("IsDeleted")):
            return True
        # never-started service: still 'Scheduled' far past its slot = dead
        # (rescheduled, driver off, silently canceled — the console's date
        # filter simply moves on and no 'Cleared' delta ever arrives)
        if (rec.get("status") == "Scheduled" and rec.get("sched_start")):
            if now_ms() - rec["sched_start"] > STALE_SCHED_HOURS * 3600 * 1000:
                return True
        return False

    def future_day(self, rec):
        """True if the service is Scheduled for a LATER day than today —
        it belongs on tomorrow's board, not today's (reschedules land here)."""
        sched = rec.get("sched_start")
        if not sched or rec.get("status") != "Scheduled":
            return False
        return not dt.datetime.fromtimestamp(sched / 1000, ZONE).date() \
            <= dt.datetime.now(ZONE).date()

    def first_service(self, rid):
        """Service to follow: earliest sched among active services, skipping
        RAP. Tow pairs ('Immediately Follow', often sharing one call ID and
        sometimes the same sched time):
          - head = earlier sched, tie-break by appointment number
          - head active → follow the head, exclude the second (its statuses
            are chain bookkeeping)
          - EXCEPTION: if the head still says 'Dispatched' while the second
            is further along (En Route/On Location/...), the second is the
            live leg — follow it
          - head cleared → the second is followed again (post-check below)
        A followed pair-second gets chain_second=True: its lingering
        'Dispatched' is bookkeeping (no dispatch alert) but its En Route/In Tow
        transitions are real."""
        cands = [s for s in self.services.values()
                 if s.get("resource_id") == rid and not self.cleared(s)
                 and not self.future_day(s) and not self.is_external(s)
                 and (s.get("call_type") or "").upper() != "RAP"]
        if not cands:
            return None
        by_id = {s["sa_id"]: s for s in cands}

        def appt_n(x):
            try:
                return int(str(x.get("appt", "")).replace("SA-", ""))
            except (TypeError, ValueError):
                return 0

        progressed = ("En Route", "On Location", "Tow Loaded", "In Tow")
        excluded, chain_seconds, processed = set(), set(), set()
        for s in cands:
            sid = s["sa_id"]
            if sid in processed:
                continue
            rel_id = s.get("related")
            if (not rel_id or s.get("reltype") != "Immediately Follow"
                    or rel_id not in by_id or rel_id in processed):
                continue
            mate = by_id[rel_id]
            processed.update({sid, mate["sa_id"]})
            s_key = (s.get("sched_start") or 0, appt_n(s))
            m_key = (mate.get("sched_start") or 0, appt_n(mate))
            head, second = (mate, s) if m_key <= s_key else (s, mate)
            if head.get("status") == "Dispatched" \
                    and second.get("status") in progressed:
                # second leg is the live one
                excluded.add(head["sa_id"])
                chain_seconds.add(second["sa_id"])
            else:
                excluded.add(second["sa_id"])
        cands = [s for s in cands if s["sa_id"] not in excluded]
        if not cands:
            return None
        cands.sort(key=lambda s: ((s.get("sched_start") or 0), s.get("appt") or ""))
        svc = cands[0]
        rel_id = svc.get("related")
        if svc["sa_id"] in chain_seconds or (
                rel_id and svc.get("reltype") == "Immediately Follow"):
            head = self.services.get(rel_id) if rel_id else None
            if (svc["sa_id"] in chain_seconds or
                    (head and head.get("resource_id") == rid and self.cleared(head)
                     and (head.get("sched_start") or 0) <= (svc.get("sched_start") or 0))):
                svc["chain_second"] = True
            else:
                svc.pop("chain_second", None)
        else:
            svc.pop("chain_second", None)
        return svc

    def busy_on_rap(self, rid):
        """True if the driver currently has a RAP service in an active-work
        state (Dispatched/En Route/On Location...). A driver on a RAP call
        can't respond to their dispatched regular call — suppress the
        dispatch-overdue alert for it (the dispatch is for their NEXT job)."""
        for s in self.services.values():
            if (s.get("resource_id") == rid
                    and (s.get("call_type") or "").upper() == "RAP"
                    and not self.cleared(s) and not self.future_day(s)):
                return True
        return False

    def driver_name(self, rid):
        d = self.drivers.get(rid)
        if d and d.get("name"):
            return d["name"]
        for s in self.services.values():
            if s.get("resource_id") == rid and s.get("resource_name"):
                return s["resource_name"]
        return rid

    def compute_alerts(self):
        now = now_ms()
        active = {}
        disabled = set(SETTINGS.get("disabled_builtins") or [])
        rules = [r for r in (SETTINGS.get("rules") or []) if r.get("enabled", True)]
        # collect all drivers that have any live service
        rids = {s["resource_id"] for s in self.services.values()
                if not self.cleared(s) and not self.future_day(s)
                and not self.is_external(s)
                and s.get("resource_id")}
        for rid in rids:
            name = self.driver_name(rid)
            svc = self.first_service(rid)
            if not svc:
                continue
            status = svc.get("status")
            d = self.drivers.get(rid, {})
            pos = d.get("pos") or {}
            hist = d.get("history") or []
            # distance driver -> service
            dist_m = None
            if pos.get("lat") is not None and svc.get("lat") is not None:
                dist_m = haversine_m(pos["lat"], pos["lng"], svc["lat"], svc["lng"])
            gps_age_min = (now - pos.get("t", 0)) / 60000 if pos.get("t") else None

            base = {"driver": name, "driver_id": rid, "call_id": svc.get("call_id"),
                    "appt": svc.get("appt"), "status": status,
                    "work_type": svc.get("work_type")}
            # chain-second: the tow pair's second leg shows a lingering
            # 'Dispatched' that is bookkeeping, not reality — suppress
            # dispatch/standstill alerts on it (En Route/In Tow are real)
            if svc.get("chain_second"):
                base["chain_second"] = True
                if status == "Dispatched":
                    continue

            # --- dispatch overdue ---
            if status == "Dispatched":
                since = self.status_epoch(svc) or now
                mins = (now - since) / 60000
                base["dispatch_min"] = round(mins, 1)
                if mins >= builtin_threshold("DISPATCH_OVERDUE", "dispatch_min",
                                             DISPATCH_OVERDUE_MIN) \
                        and not self.busy_on_rap(rid) \
                        and "DISPATCH_OVERDUE" not in disabled:
                    key = f"dispatch:{svc['sa_id']}"
                    active[key] = {**base, "type": "DISPATCH_OVERDUE",
                                   "detail": f"Dispatched {mins:.0f} min without En Route",
                                   "level": "urgent", "since": since}

            # --- movement + standstill: EN ROUTE ONLY (on location / in tow
            # are expected to be stationary) ---
            if status == "En Route":
                since = self.status_epoch(svc) or now
                enroute_min = (now - since) / 60000
                base["enroute_min"] = round(enroute_min, 1)
                base["dist_m"] = int(dist_m) if dist_m is not None else None
                base["gps_age_min"] = round(gps_age_min, 1) if gps_age_min is not None else None
                # standing still: all positions in last STANDSTILL_MIN within radius
                still_min = builtin_threshold("STANDING_STILL", "wait_min", STANDSTILL_MIN)
                recent = [h for h in hist if now - h["t"] <= still_min * 60000 * 1.2]
                still = False
                if len(recent) >= 3 and enroute_min >= still_min:
                    span = max(haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
                               for a in recent for b in recent)
                    still = span <= STANDSTILL_RADIUS_M
                if still:
                    # paired tow: the mate leg's location is the DROP-OFF
                    # point. A standstill far from the pickup but near the
                    # drop-off = car already towed, status not flipped.
                    at_dropoff = False
                    dropoff_m = None
                    mate = self._mate_of(svc)
                    if (mate and mate.get("lat") is not None
                            and pos.get("lat") is not None):
                        dropoff_m = haversine_m(pos["lat"], pos["lng"],
                                                mate["lat"], mate["lng"])
                        at_dropoff = dropoff_m <= AT_LOCATION_RADIUS_M
                    if at_dropoff:
                        pass  # at drop-off = forgot to flip status, not stuck
                    # at the service location = probably forgot to flip status
                    elif dist_m is not None and dist_m <= AT_LOCATION_RADIUS_M:
                        pass  # no alert; shown as AT LOC on the board
                    elif ("FAR_AWAY" not in disabled and dist_m is not None
                          and dist_m >= builtin_threshold("FAR_AWAY", "dist_km",
                                                          FAR_DISTANCE_M)):
                        key = f"far:{svc['sa_id']}"
                        active[key] = {**base, "type": "FAR_AWAY",
                                       "detail": (f"Standstill {still_min:.0f}+ min, "
                                                  f"still {dist_m/1000:.1f} km from service"),
                                       "level": "urgent",
                                       "since": now - STANDSTILL_MIN * 60000}
                    elif "STANDING_STILL" not in disabled:
                        key = f"still:{svc['sa_id']}"
                        active[key] = {**base, "type": "STANDING_STILL",
                                       "detail": (f"No movement > {STANDSTILL_RADIUS_M} m "
                                                  f"in {still_min:.0f} min"),
                                       "level": "urgent",
                                       "since": now - still_min * 60000}

            # --- member waiting (Spotted) ---
            if status == "Spotted":
                since = self.status_epoch(svc) or now
                mins = (now - since) / 60000
                if mins >= builtin_threshold("MEMBER_WAITING", "wait_min",
                                             MEMBER_WAIT_MIN) \
                        and "MEMBER_WAITING" not in disabled:
                    key = f"wait:{svc['sa_id']}"
                    active[key] = {**base, "type": "MEMBER_WAITING",
                                   "detail": f"Member spotted/waiting {mins:.0f} min",
                                   "level": "minor", "since": since}

            # --- ETA expired + still far away ---
            # The driver posted 'ETA n MIN' (current driver, post-dispatch
            # only), the window has passed, and they're still >10 min out
            # (assumed ~40 km/h average tow response speed -> 10 min ~ 6.7 km).
            if status in ("Dispatched", "En Route") and not svc.get("chain_second"):
                eta = (self.etas.get(svc["sa_id"])
                       or self.etas.get(svc.get("parent_id")))
                if eta and eta["posted"] >= (svc.get("status_since") or 0):
                    if ("ETA_EXPIRED" not in disabled and now > eta["high"]
                            and dist_m is not None
                            and dist_m > builtin_threshold("ETA_EXPIRED", "dist_km",
                                                           ETA_EXPIRED_DIST_M)):
                        key = f"etaexp:{svc['sa_id']}"
                        active[key] = {**base, "type": "ETA_EXPIRED",
                                       "detail": (f"ETA {eta['text'].upper()} passed "
                                                  f"{(now - eta['high'])/60000:.0f} min ago, "
                                                  f"still {dist_m/1000:.1f} km out"),
                                       "level": "urgent", "since": eta["high"]}

            # --- user-defined rules ---
            if rules:
                _eta = (self.etas.get(svc["sa_id"])
                        or self.etas.get(svc.get("parent_id")))
                _eta_current = (_eta if _eta and _eta["posted"] >=
                                (svc.get("status_since") or 0) else None)
                _eta_pass_min = None
                if _eta_current and now > _eta_current["high"]:
                    _eta_pass_min = round((now - _eta_current["high"]) / 60000, 1)
                ctx = {"status": status,
                       "eta_exists": bool(_eta_current),
                       "eta_passed_min": _eta_pass_min,
                       "eta_late_min": (round((now - _eta_current["low"]) / 60000, 1)
                                        if _eta_current else None),
                       "enroute_min": base.get("enroute_min"),
                       "dispatch_min": base.get("dispatch_min"),
                       "wait_min": round((now - (svc.get("status_since")
                                         or svc.get("last_modified") or now))/60000, 1),
                       "dist_km": round(dist_m/1000, 2) if dist_m is not None else None,
                       "gps_age_min": round(gps_age_min, 1) if gps_age_min is not None else None,
                       "work_type": svc.get("work_type"),
                       "driver": name,
                       "call_id": svc.get("call_id"),
                       "moving": None}
                for rule in rules:
                    if not rule_matches(rule, ctx):
                        continue
                    key = f"custom:{rule.get('id')}:{svc['sa_id']}"
                    active[key] = {**base, "type": "CUSTOM",
                                   "custom_name": rule.get("name") or "Custom alert",
                                   "detail": rule.get("name") or "Custom alert matched",
                                   "level": rule.get("level") or "minor",
                                   "rule_id": rule.get("id"), "since": now}
        return active

    def cleared_log(self):
        """Callback list (all completed services in the last 12 h; the
        dashboard shows 20 and pages via load-more). Excludes RAP and
        external drivers; tow pairs deduped to one row; one row per
        (call, driver). Canceled services are included but flagged."""
        now = now_ms()
        done = [s for s in self.services.values()
                if s.get("cleared_at") and now - s["cleared_at"] <= 12 * 3600 * 1000
                and (s.get("status") in DONE_STATUSES
                     or s.get("status") == "Canceled")
                and not self.is_external(s)
                and (s.get("call_type") or "").upper() != "RAP"]
        # dedupe tow pairs: 'related' + 'Immediately Follow' = same job
        seen, out = set(), []
        for s in sorted(done, key=lambda x: (x.get("sched_start") or 0,
                                             x.get("appt") or "")):
            rel = s.get("related")
            if rel and rel in {x["sa_id"] for x in done} and s.get("reltype") == "Immediately Follow":
                # both legs of the pair are done: keep only the HEAD
                mate = self.services.get(rel, {})
                h_key = (mate.get("sched_start") or 0, mate.get("appt") or "")
                s_key = (s.get("sched_start") or 0, s.get("appt") or "")
                if s_key > h_key:
                    continue  # this is the second leg — the head represents it
            if s["sa_id"] in seen:
                continue
            seen.add(s["sa_id"])
            wo = self.wo_cache.get(s.get("parent_id")) or {}
            # exact status times from the SA feed (ground truth, works even
            # for services cleared before the tracker watched); merge across
            # the pair (tow legs split the journey: drive leg holds
            # Spotted/Scheduled/On Location, tow leg holds
            # In Tow/Tow Complete/Cleared)
            merged = {}
            leg_ids = [s["sa_id"]]
            if s.get("reltype") == "Immediately Follow" and s.get("related"):
                mate = self.services.get(s["related"])
                if mate:
                    leg_ids.append(mate["sa_id"])
            for lid in leg_ids:
                for k, v in (self.feed_times.get(lid) or {}).items():
                    if k not in merged or v < merged[k]:
                        merged[k] = v
            sched_t = (merged.get("Scheduled") or merged.get("Dispatched")
                       or s.get("sched_start"))
            arrived = (merged.get("On Location") or merged.get("Tow Loaded")
                       or (s.get("arrived_at") if s.get("arrived_live")
                           else None))
            cleared_t = (merged.get("Cleared") or merged.get("Tow Complete")
                         or s.get("cleared_at"))
            # member wait (user's definition): earliest Spotted/Scheduled/
            # Dispatched -> On Location/Tow Loaded (true wait regardless of
            # reassignments). Exact sources only: SA feed ground truth or a
            # flip the tracker watched live.
            starts = [merged[k] for k in ("Spotted", "Scheduled", "Dispatched")
                      if merged.get(k)]
            if arrived and starts:
                wait = round(max(0.0, (arrived - min(starts)) / 60000))
            elif arrived and s.get("arrived_live") and s.get("sched_start"):
                wait = round(max(0.0, (arrived - s["sched_start"]) / 60000))
            else:
                wait = None
            onscene = None
            if cleared_t and arrived:
                onscene = round(max(0.0, (cleared_t - arrived) / 60000))
            out.append({
                "cleared_at": ms_to_central(cleared_t),
                "cleared_ts": cleared_t,
                "call_id": s.get("call_id"),
                "canceled": s.get("status") == "Canceled",
                "member_name": wo.get("member_name"),
                "phone": s.get("phone"),
                "benefit": wo.get("benefit") or s.get("benefit"),
                "work_type": (s.get("work_type")
                              or s.get("work_type_id") or s.get("call_type")),
                "vehicle": s.get("vehicle"),
                "driver": (s.get("resource_name") or
                           self.driver_name(s.get("resource_id"))),
                "wait_min": wait,
                "onscene_min": onscene,
                "appt": s.get("appt"),
                "sched_start": ms_to_central(sched_t),
            })
        # one row per (call, driver): later clears of the same call win
        by_call = {}
        for c in out:
            key = (c["call_id"], c["driver"])
            if key not in by_call or c["cleared_ts"] > by_call[key]["cleared_ts"]:
                by_call[key] = c
        out = sorted(by_call.values(), key=lambda x: x["cleared_ts"] or 0,
                     reverse=True)
        return out

    def _mate_of(self, svc):
        """The other leg of a tow pair: forward link (svc.related) or reverse
        (any service whose related == svc.sa_id)."""
        rel = svc.get("related")
        if rel and svc.get("reltype") == "Immediately Follow":
            mate = self.services.get(rel)
            if mate:
                return mate
        # reverse link: the OTHER leg points at us ('Related_Service__c' on the
        # second leg); many payloads only carry the link one way
        for s in self.services.values():
            if (s.get("related") == svc["sa_id"]
                    and s.get("reltype") == "Immediately Follow"):
                return s
        # last resort: same driver + same call, exactly one other leg = pair
        # (the partner may already be cleared — the drop-off address is still
        # valid and it stops the dropoff from flashing then vanishing when the
        # followed leg flips)
        cands = [s for s in self.services.values()
                 if s["sa_id"] != svc["sa_id"]
                 and s.get("resource_id") == svc.get("resource_id")
                 and s.get("call_id") and s.get("call_id") == svc.get("call_id")
                 and not self.future_day(s)]
        return cands[0] if len(cands) == 1 else None

    def _dropoff_of(self, svc):
        """Tow pair drop-off = the DESTINATION leg's address (the later leg of
        the pair — where the car is being towed). Only shown when the followed
        service is the EARLIER leg (drive to member); if the followed service
        is itself the destination leg, the drop-off is already the Address
        column, so None (keeps the display stable across leg flips)."""
        mate = self._mate_of(svc)
        if not mate:
            return None
        # the destination leg = the LATER-scheduled leg of the pair
        my_key = (svc.get("sched_start") or 0, svc.get("appt") or "")
        mate_key = (mate.get("sched_start") or 0, mate.get("appt") or "")
        if my_key >= mate_key:
            return None  # this leg IS the destination
        parts = [p for p in (mate.get("street"), mate.get("city")) if p]
        if parts:
            return ", ".join(parts)
        return self.dropoff_cache.get(mate.get("sa_id"))

    def snapshot(self):
        now = now_ms()
        snap_account = ACCOUNT_KEY
        alerts = self.compute_alerts()
        muted_types = set(SETTINGS.get("muted_types") or [])
        muted_drivers = {x.lower() for x in (SETTINGS.get("muted_drivers") or [])}
        alerts = {k: a for k, a in alerts.items()
                  if a.get("type") not in muted_types
                  and a.get("driver", "").split("(")[0].strip().lower() not in muted_drivers}
        # fire new urgent alerts
        for key, a in alerts.items():
            if key not in self.alerts and a["level"] == "urgent":
                fire_toast(a)
                self.log_event("alert_fired", **{k: a.get(k) for k in
                                                 ("type", "driver", "call_id", "detail")})
        self.alerts = alerts

        rows = []
        rids = {s["resource_id"] for s in self.services.values()
                if not self.cleared(s) and not self.future_day(s)
                and not self.is_external(s)
                and s.get("resource_id")}
        for rid in rids:
            svc = self.first_service(rid)
            if not svc:
                continue
            status = svc.get("status")
            if self.busy_on_rap(rid) and status in ("Scheduled", "Dispatched"):
                # driver is physically working a RAP call; the visible regular
                # call is just their queued next job
                status = "On RAP"
            d = self.drivers.get(rid, {})
            pos = d.get("pos") or {}
            dist_m = None
            if pos.get("lat") is not None and svc.get("lat") is not None:
                dist_m = haversine_m(pos["lat"], pos["lng"], svc["lat"], svc["lng"])
            since = svc.get("status_since") or svc.get("last_modified")
            # ETA from the newest feed comment (own feed or parent WO feed).
            # Stale ('*') = the post predates this service's current driver
            # being dispatched/en route: another team (or the previous
            # driver) posted it while the call was still Scheduled, or the
            # service was reassigned after the post.
            eta = (self.etas.get(svc["sa_id"])
                   or self.etas.get(svc.get("parent_id")))
            now_c = dt.datetime.now(ZONE)
            if eta:
                lo_c = dt.datetime.fromtimestamp(eta["low"] / 1000, ZONE)
                hi_c = dt.datetime.fromtimestamp(eta["high"] / 1000, ZONE)
                same_day = lo_c.date() == now_c.date()
                fmt = "%I:%M %p" if same_day else "%m/%d %I:%M %p"
                eta_str = f"{lo_c.strftime(fmt)}–{hi_c.strftime('%I:%M %p').lstrip('0')}"
                eta_stale = eta["posted"] < svc.get("status_since", 0) \
                    and status in ("Dispatched", "En Route")
            else:
                eta_str, eta_stale = None, False
            # movement state, straight from GPS history (same rule as the
            # standstill alert): all pings in the window within radius = still.
            # Applies to BOTH En Route and Dispatched: a driver who's moving
            # while still Dispatched forgot to flip; a standstill either way
            # is worth flagging.
            move_state = None
            move_since = None
            hist = d.get("history") or []
            if status in ("En Route", "Dispatched"):
                recent = [h for h in hist if now - h["t"] <= STANDSTILL_MIN * 60000 * 1.2]
                if len(recent) >= 3:
                    span = max(haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
                               for a in recent for b in recent)
                    if span <= STANDSTILL_RADIUS_M:
                        # how long have they been parked: first ping of the
                        # standstill run
                        move_since = recent[0]["t"]
                        # paired tow: near the mate leg's location = at the
                        # drop-off point (car already towed, forgot to flip)
                        mate = self._mate_of(svc)
                        dropoff_near = False
                        if (mate and mate.get("lat") is not None
                                and pos.get("lat") is not None):
                            dropoff_near = haversine_m(
                                pos["lat"], pos["lng"],
                                mate["lat"], mate["lng"]) <= AT_LOCATION_RADIUS_M
                        if dropoff_near:
                            move_state = "AT DROP OFF"
                        # at the service = forgot to flip status, not stuck
                        elif (dist_m is not None
                              and dist_m <= AT_LOCATION_RADIUS_M):
                            move_state = "AT LOC"
                        else:
                            move_state = "STANDSTILL"
                    else:
                        move_state = "MOVING"
                else:
                    # too few fresh pings to judge
                    move_state = None
            rows.append({
                "driver": self.driver_name(rid),
                "resource_id": rid,
                "sa_id": svc.get("sa_id"),
                "call_id": svc.get("call_id"),
                "appt": svc.get("appt"),
                "work_type": svc.get("work_type"),
                "status": status,
                "chain_second": bool(svc.get("chain_second")),
                "moving": move_state,
                "moving_min": round((now - move_since) / 60000) if move_since else None,
                "vehicle": svc.get("vehicle"),
                "eta": eta_str,
                "eta_stale": eta_stale,
                "eta_low": eta["low"] if eta else None,
                "eta_high": eta["high"] if eta else None,
                "status_min": round((now - since) / 60000, 1) if since else None,
                "address": svc.get("street"),
                "dropoff": self._dropoff_of(svc),
                "dist_km": round(dist_m / 1000, 1) if dist_m is not None else None,
                "gps_age_min": round((now - pos.get("t", 0)) / 60000, 1) if pos.get("t") else None,
                "alerts": [(a.get("custom_name") if a["type"] == "CUSTOM" else a["type"])
                           for k, a in alerts.items() if a["driver_id"] == rid],
            })
        rows.sort(key=lambda r: (self.driver_order.get(r["resource_id"], 10**6),
                                 str(r["driver"] or "")))
        return {
            "generated_at": now,
            "generated_central": ms_to_central(now),
            "last_data_age_s": (now - self.last_data_ts) / 1000 if self.last_data_ts else None,
            "alerts": list(alerts.values()),
            "rows": rows,
            "cleared": self.cleared_log(),
            "account_key": snap_account,
        }


# ---------------- toast ----------------
def fire_toast(alert):
    tname = alert.get('custom_name') or alert['type']
    title = f"{tname} - {alert.get('driver', '?')}"
    body = f"{alert.get('detail', '')} (call {alert.get('call_id', '?')})"
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode('{title.replace("'", "''")}')) | Out-Null
$t.Item(1).AppendChild($xml.CreateTextNode('{body.replace("'", "''")}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('FSL Tracker').Show($toast)
[System.Media.SystemSounds]::Exclamation.Play()
"""
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception as e:
        print("toast failed:", e, flush=True)


# ---------------- CDP listener ----------------
async def run():
    state = State()
    last_snapshot = 0
    msg_id = 0
    pending = {}
    sessions = {}
    req_meta = {}

    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version",
                                    timeout=5) as r:
            ws_url = json.load(r)["webSocketDebuggerUrl"]
    except Exception:
        print("=" * 60, flush=True)
        print("CANNOT REACH THE CONSOLE CHROME on port 9222.", flush=True)
        print("Fix: double-click start_chrome.bat, then log in to the", flush=True)
        print("dispatch console in that window. Keep it OPEN. Then", flush=True)
        print("restart this tracker (start_tracker.bat).", flush=True)
        print("=" * 60, flush=True)
        raise SystemExit(1)

    async with websockets.connect(ws_url, max_size=256 * 1024 * 1024,
                                  ping_interval=20) as ws:

        async def send(method, params=None, session_id=None, future=None):
            nonlocal msg_id
            msg_id += 1
            m = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                m["sessionId"] = session_id
            if future:
                pending[msg_id] = future
            await ws.send(json.dumps(m))

        async def call(method, params=None, session_id=None, timeout=15):
            fut = asyncio.get_running_loop().create_future()
            await send(method, params, session_id, fut)
            return await asyncio.wait_for(fut, timeout)

        async def get_body(sid, rid_):
            meta = req_meta.get(rid_)
            if not meta:
                return None, None
            try:
                res = await call("Network.getResponseBody", {"requestId": rid_},
                                 sid, timeout=10)
                return res.get("body", ""), meta
            except Exception:
                return None, meta

        async def handle_response(sid, rid_):
            body, meta = await get_body(sid, rid_)
            if not body or not meta:
                return
            url = meta.get("url", "")
            # ---- chatter feed pages: extract ETA comments ----
            if "/apex/fsl__vf0993_servicechatter" in url or \
               "/apex/fsl__vf0996_workorderchatter" in url:
                m = re.search(r"[?&]id=([0-9A-Za-z]+)", url)
                rec_id = m.group(1) if m else None
                eta = parse_eta_from_feed(body, now_ms())
                if eta and rec_id:
                    prev = state.etas.get(rec_id)
                    if not prev or eta["posted"] > prev["posted"]:
                        state.etas[rec_id] = eta
                        try:
                            with open(current_paths()["etas"], "w", encoding="utf-8") as f:
                                json.dump(state.etas, f)
                        except Exception:
                            pass
                        state.log_event("eta", record_id=rec_id, text=eta["text"],
                                        low=eta["low"], high=eta["high"])
                return
            try:
                state.detect_account(body)  # raw body carries ServiceTerritoryId
                obj = json.loads(body)
            except Exception:
                return
            try:
                if "apexremote" in url and isinstance(obj, list) and obj:
                    entry = obj[0]
                    action = entry.get("action", "")
                    res = entry.get("result")
                    if not isinstance(res, (dict, list)):
                        return
                    if isinstance(res, dict) and "updatedLivePositions" in res:
                        state.ingest_delta(res)
                    else:
                        state.ingest_bulk(res)
            except Exception:
                print("parse error:", traceback.format_exc()[:400], flush=True)

        async def handle(msg, sid):
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Target.attachedToTarget":
                ti = params["targetInfo"]
                sessions[params["sessionId"]] = {"type": ti["type"], "url": ti["url"]}
                if ti["type"] in ("page", "iframe", "webview"):
                    try:
                        await call("Network.enable", {}, params["sessionId"], timeout=5)
                    except Exception:
                        pass
            elif method == "Network.requestWillBeSent":
                req_meta[params["requestId"]] = {
                    "url": params["request"]["url"],
                    "method": params["request"]["method"],
                }
                # keep dict bounded
                if len(req_meta) > 4000:
                    for k in list(req_meta)[:1500]:
                        req_meta.pop(k, None)
            elif method == "Network.loadingFinished":
                u = req_meta.get(params["requestId"], {}).get("url", "")
                if ("apexremote" in u or "/apex/fsl__vf0993_servicechatter" in u
                        or "/apex/fsl__vf0996_workorderchatter" in u):
                    asyncio.create_task(handle_response(sid, params["requestId"]))

        async def reader():
            nonlocal last_snapshot
            while True:
                raw = await ws.recv()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if "id" in msg:
                    fut = pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(json.dumps(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue
                sid = msg.get("sessionId")
                if sid and sid not in sessions:
                    sessions[sid] = True  # allow events before attach confirmation
                asyncio.create_task(handle_msg(msg, sid))

        async def handle_msg(msg, sid):
            try:
                await handle(msg, sid)
            except Exception:
                print("event error:", traceback.format_exc()[:300], flush=True)

        async def snapshot_loop():
            while True:
                await asyncio.sleep(3)
                try:
                    snap = state.snapshot()
                    snap["login_required"] = state.login_required
                    with open(current_paths()["state"], "w", encoding="utf-8") as f:
                        json.dump(snap, f, ensure_ascii=False, indent=1)
                except Exception:
                    print("snapshot error:", traceback.format_exc()[:400], flush=True)

        async def fetch_feed_via_page(url_path):
            """Fetch a relative feed URL through the console tab's own session
            (Runtime.evaluate + fetch, same-origin, cookies included)."""
            try:
                targets = json.loads(await asyncio.get_running_loop().run_in_executor(
                    None, lambda: urllib.request.urlopen(
                        "http://127.0.0.1:9222/json/list", timeout=5).read()))
                page = next((t for t in targets if t.get("type") == "page"
                             and "dispatch-console" in t.get("url", "")), None)
                if not page or "/login" in page.get("url", ""):
                    state.login_required = True
                    return None
                ws_target = page.get("webSocketDebuggerUrl")
                if not ws_target:
                    return None
                import websockets as _w
                async with _w.connect(ws_target, max_size=20 * 1024 * 1024) as tws:
                    expr = ("fetch('" + url_path + "', {credentials:'include'})"
                            ".then(r=>r.text()).then(t=>({ok:true,body:t})) "
                            ".catch(e=>({ok:false,err:String(e)}))")
                    await tws.send(json.dumps({
                        "id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "awaitPromise": True,
                                   "returnByValue": True}}))
                    while True:
                        m = json.loads(await asyncio.wait_for(tws.recv(), timeout=20))
                        if m.get("id") == 1:
                            res = m.get("result", {}).get("result", {})
                            val = res.get("value") or {}
                            if val.get("ok"):
                                return val.get("body", "")
                            return None
            except Exception:
                return None

        async def eta_fetch_loop():
            """Actively read ETAs for followed services: fetches the service
            feed page for services with no ETA (or a stale one), one every
            ~20 s, round-robin. Uses the console tab's own session."""
            while True:
                await asyncio.sleep(20)
                try:
                    if (now_ms() - state.last_data_ts) / 1000 > 600:
                        continue  # console data stale; don't act on a stale board
                    now = now_ms()
                    rids = {s["resource_id"] for s in state.services.values()
                            if not state.cleared(s) and not state.is_external(s)
                            and s.get("resource_id")}
                    job = None
                    for rid in rids:
                        svc = state.first_service(rid)
                        if not svc:
                            continue
                        if svc.get("status") not in ("Dispatched", "En Route",
                                                     "On Location", "Tow Loaded",
                                                     "In Tow"):
                            continue
                        eta = (state.etas.get(svc["sa_id"])
                               or state.etas.get(svc.get("parent_id")))
                        stale = (not eta) or (now - eta["posted"] > 45 * 60000)
                        last_try = state.eta_fetch.get(svc["sa_id"], 0)
                        if stale and (now - last_try) > 600000:
                            job = svc
                            break
                    if not job:
                        continue
                    state.eta_fetch[job["sa_id"]] = now
                    # ETAs are posted on the WORK ORDER chatter (validated
                    # 2026-09-09: 60+ SA feeds read, zero ETAs; every ETA
                    # found sat on the WO feed) — fetch the WO feed, falling
                    # back to the SA feed when the service has no parent
                    if job.get("parent_id"):
                        url_path = ("/ACEContractorCommunity/apex/"
                                    "fsl__vf0996_workorderchatter?id="
                                    + job["parent_id"])
                    else:
                        url_path = ("/ACEContractorCommunity/apex/"
                                    "fsl__vf0993_servicechatter?id="
                                    + job["sa_id"])
                    body = await fetch_feed_via_page(url_path)
                    got_feed = bool(body and "feeditemcontent" in body)
                    state.log_event("eta_fetch",
                                    sa_id=job["sa_id"],
                                    parent_id=job.get("parent_id"),
                                    ok=bool(body), has_feed=got_feed,
                                    has_eta=bool(body and eta_ok(body)))
                    if body:
                        eta = parse_eta_from_feed(body, now_ms())
                        if eta:
                            prev = state.etas.get(job["sa_id"])
                            if not prev or eta["posted"] > prev["posted"]:
                                state.etas[job["sa_id"]] = eta
                                state.log_event("eta", record_id=job["sa_id"],
                                                text=eta["text"], low=eta["low"],
                                                high=eta["high"],
                                                source="autofetch")
                    await asyncio.sleep(2)
                except Exception:
                    print("eta fetch error:", traceback.format_exc()[:300],
                          flush=True)
                    await asyncio.sleep(30)

        def parse_lightbox_address(body):
            """vf002 lightbox -> 'Street, City' (Address block)."""
            txt = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
            txt = " | ".join(re.findall(r">([^<>]+)<", txt))
            txt = " ".join(txt.replace("&nbsp;", " ").split())
            m = re.search(r"Address\s*\|+\s*Landmark\s*\|+\s*City\s*\|+\s*([^|]+?)\s*\|+\s*Street\s*\|+\s*([^|]+?)\s*\|", txt)
            if m:
                return m.group(2).strip() + ", " + m.group(1).strip()
            m = re.search(r"Street\s*\|+\s*([^|]+?)\s*\|", txt)
            return m.group(1).strip() if m else None

        async def wo_enrich_loop():
            """Fetch WO lightboxes (member name / benefit / work type) for
            recently cleared services that lack them. 1 fetch / 22 s max,
            same session discipline as the ETA fetch."""
            while True:
                await asyncio.sleep(22)
                try:
                    if (now_ms() - state.last_data_ts) / 1000 > 600:
                        continue
                    now = now_ms()
                    # two WO fetches per cycle for faster name backfill
                    for _pass in range(2):
                        job = None
                        for s in state.services.values():
                            if not s.get("cleared_at") or s.get("parent_id") is None:
                                continue
                            if now - s["cleared_at"] > 12 * 3600 * 1000:
                                continue
                            pid = s["parent_id"]
                            if pid in state.wo_cache:
                                continue
                            if now - state.wo_fetch.get(pid, 0) < 600000:
                                continue
                            job = pid
                            break
                        if not job:
                            break
                        state.wo_fetch[job] = now
                        body = await fetch_feed_via_page(
                            "/ACEContractorCommunity/apex/"
                            "fsl__vf0999_workorderlightbox?id=" + job)
                        info = parse_wo_lightbox(body) if body else {}
                        if info.get("member_name"):
                            info["fetched"] = now
                            state.wo_cache[job] = info
                            state.log_event("wo_enrich", parent_id=job,
                                            member=info.get("member_name"),
                                            benefit=info.get("benefit"),
                                            work_type=info.get("work_type"))
                        await asyncio.sleep(1)
                except Exception:
                    print("wo enrich error:", traceback.format_exc()[:200],
                          flush=True)
                    await asyncio.sleep(30)

        async def dropoff_loop():
            """For followed tow-pair rows whose mate leg has no address in the
            gantt data: fetch the mate's lightbox (same read-only session) and
            cache 'street, city'. One fetch per cycle."""
            while True:
                await asyncio.sleep(22)
                try:
                    if (now_ms() - state.last_data_ts) / 1000 > 600:
                        continue
                    now = now_ms()
                    job = None
                    for rid in {s["resource_id"] for s in state.services.values()
                                if not state.cleared(s) and s.get("resource_id")}:
                        svc = state.first_service(rid)
                        if not svc:
                            continue
                        mate = state._mate_of(svc)
                        if not mate:
                            continue
                        mid = mate.get("sa_id")
                        if (mate.get("street")
                                or mid in state.dropoff_cache):
                            continue
                        if now - state.dropoff_fetch.get(mid, 0) < 600000:
                            continue
                        job = mid
                        break
                    if not job:
                        continue
                    state.dropoff_fetch[job] = now
                    body = await fetch_feed_via_page(
                        "/ACEContractorCommunity/apex/"
                        "fsl__vf002_serviceexpertlightboxform?id=" + job)
                    addr = parse_lightbox_address(body) if body else None
                    if addr:
                        state.dropoff_cache[job] = addr
                        state.log_event("dropoff", sa_id=job, address=addr)
                except Exception:
                    print("dropoff error:", traceback.format_exc()[:200],
                          flush=True)
                    await asyncio.sleep(30)

        async def feed_time_loop():
            """Fetch SA chatter feeds for cleared services to extract exact
            status-change times (Scheduled/On Location/Cleared). 1/22 s."""
            while True:
                await asyncio.sleep(22)
                try:
                    if (now_ms() - state.last_data_ts) / 1000 > 600:
                        continue
                    now = now_ms()
                    job = None
                    # two jobs per cycle (one feed fetch each) — backfill is
                    # 2x/22s ≈ 330/h, drains a shift's backlog in ~45 min
                    for _pass in range(2):
                        job = None
                        # newest first: the rows the user actually sees fill in first
                        for s in sorted(state.services.values(),
                                        key=lambda x: x.get("cleared_at") or 0,
                                        reverse=True):
                            if (not s.get("cleared_at")
                                    or s["sa_id"] in state.feed_times
                                    or s.get("sa_id") in state.feed_time_fetch):
                                continue
                            if now - s["cleared_at"] > 12 * 3600 * 1000:
                                continue
                            if (s.get("call_type") or "").upper() == "RAP":
                                continue
                            if state.is_external(s):
                                continue
                            if (now - state.feed_time_fetch.get(
                                    s["sa_id"], 0) < 600000):
                                continue
                            job = s
                            break
                        if not job:
                            break
                        state.feed_time_fetch[job["sa_id"]] = now
                        body = await fetch_feed_via_page(
                            "/ACEContractorCommunity/apex/"
                            "fsl__vf0993_servicechatter?id=" + job["sa_id"])
                        if body:
                            times, reassigned = parse_status_times_from_feed(body)
                            if times:
                                state.feed_times[job["sa_id"]] = times
                                state.log_event("feed_times",
                                                sa_id=job["sa_id"],
                                                call_id=job.get("call_id"),
                                                sched=times.get("Scheduled"),
                                                onsite=times.get("On Location"),
                                                cleared=times.get("Cleared"),
                                                reassigned=reassigned)
                        job = None
                        await asyncio.sleep(1)
                except Exception:
                    print("feed time error:", traceback.format_exc()[:200],
                          flush=True)
                    await asyncio.sleep(30)

        async def reload_console():
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/list",
                                            timeout=5) as r:
                    targets = json.load(r)
            except Exception:
                return False
            for t in targets:
                if (t.get("type") == "page"
                        and "dispatch-console" in t.get("url", "")):
                    if "/login" in t["url"]:
                        state.login_required = True
                        return False
                    try:
                        ws_target = t.get("webSocketDebuggerUrl")
                        if not ws_target:
                            return False
                        import websockets as _w
                        async with _w.connect(ws_target,
                                              max_size=10 * 1024 * 1024) as tws:
                            await tws.send(json.dumps(
                                {"id": 1, "method": "Page.reload", "params": {}}))
                            await asyncio.sleep(1)
                        state.last_reload_ts = now_ms()
                        state.log_event("watchdog_reload", url=t["url"][:200])
                        print("watchdog: reloaded console tab", flush=True)
                        return True
                    except Exception as e:
                        print("reload failed:", e, flush=True)
                        return False
            return False

        async def watchdog_loop():
            """Keeps the tracker's picture complete:
            - reload once shortly after startup if no full-day load arrived
              (deltas alone only carry CHANGED services — a fresh start that
              relies on deltas sees an incomplete board);
            - periodic full resync every 30 min even when deltas flow;
            - reload if data goes stale entirely;
            - toast when the session expires."""
            start_ts = now_ms()
            while True:
                await asyncio.sleep(30)
                try:
                    if state.login_required:
                        fire_toast({"type": "LOGIN NEEDED",
                                    "driver": "session expired",
                                    "detail": "Open the FSL Chrome window and log in",
                                    "call_id": "-"})
                        state.login_required = False
                        continue
                    uptime_min = (now_ms() - start_ts) / 60000
                    full_age_min = ((now_ms() - state.last_full_ts) / 60000
                                    if state.last_full_ts else None)
                    data_age_s = ((now_ms() - state.last_data_ts) / 1000
                                  if state.last_data_ts else 999)
                    since_reload_min = ((now_ms() - state.last_reload_ts) / 60000
                                        if state.last_reload_ts else 999)
                    need = False
                    if full_age_min is None and uptime_min > 1.5:
                        need = True   # startup: never got a full load
                    elif full_age_min is not None and full_age_min > 30:
                        need = True   # periodic resync
                    elif (full_age_min is None or full_age_min > 10) and data_age_s > 240:
                        need = True   # deltas stalled too
                    if need and since_reload_min > 2:
                        await reload_console()
                except Exception:
                    print("watchdog error:", traceback.format_exc()[:300],
                          flush=True)

        reader_task = asyncio.create_task(reader())
        await call("Target.setAutoAttach",
                   {"autoAttach": True, "waitForDebuggerOnStart": False,
                    "flatten": True})
        print(f"tracker v{VERSION} listening...", flush=True)
        snap_task = asyncio.create_task(snapshot_loop())
        wd_task = asyncio.create_task(watchdog_loop())
        eta_task = asyncio.create_task(eta_fetch_loop())
        wo_task = asyncio.create_task(wo_enrich_loop())
        ft_task = asyncio.create_task(feed_time_loop())
        do_task = asyncio.create_task(dropoff_loop())
        await asyncio.gather(reader_task, snap_task, wd_task, eta_task, wo_task,
                             ft_task, do_task)


if __name__ == "__main__":
    load_settings()
    import http.server
    import socketserver
    import threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=BASE, **kw)

        def do_GET(self):
            # serve ONLY the dashboard + its own state feed; anything else
            # (source code, logs, .git) stays private — the share link is
            # public, so the root must never list files
            path = self.path.split("?")[0]
            if path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", "/dashboard.html")
                self.end_headers()
                return
            if path == "/state.json":
                # account-scoped state file (legacy name before first detection)
                out = current_paths()["state"]
                try:
                    with open(out, encoding="utf-8") as f:
                        data = f.read().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self.send_error(404, "Not found")
                return
            if path == "/settings":
                # read-only view of mute prefs so any viewer's gear menu stays in sync
                out = json.dumps({"muted_types": SETTINGS.get("muted_types", []),
                                  "muted_drivers": SETTINGS.get("muted_drivers", []),
                                  "rules": SETTINGS.get("rules", []),
                                  "disabled_builtins": SETTINGS.get("disabled_builtins", []),
                                  "builtin_overrides": SETTINGS.get("builtin_overrides", {}),
                                  "numeric_names_external": SETTINGS.get("numeric_names_external", True)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            if path not in ("/dashboard.html", "/state.json"):
                self.send_error(404, "Not found")
                return
            super().do_GET()

        def do_POST(self):
            # dashboard settings menu -> muting prefs (localhost + tunnel both OK;
            # nothing else is writable and the body never touches the filesystem
            # outside settings.json)
            if self.path.split("?")[0] != "/settings":
                self.send_error(404, "Not found")
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                SETTINGS["muted_types"] = [str(x) for x in (body.get("muted_types") or [])]
                SETTINGS["muted_drivers"] = [str(x) for x in (body.get("muted_drivers") or [])]
                SETTINGS["rules"] = [r for r in (body.get("rules") or []) if isinstance(r, dict)]
                SETTINGS["disabled_builtins"] = [str(x) for x in (body.get("disabled_builtins") or [])]
                SETTINGS["builtin_overrides"] = {k: dict(v) for k, v in
                                                 (body.get("builtin_overrides") or {}).items()
                                                 if isinstance(v, dict)}
                SETTINGS["numeric_names_external"] = bool(body.get("numeric_names_external", True))
                save_settings()
                out = json.dumps({"ok": True}).encode()
                self.send_response(200)
            except Exception as e:
                out = json.dumps({"ok": False, "err": str(e)}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def end_headers(self):
            # dashboard.html must always revalidate — stale JS = stale logic
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, *a):
            pass

    class ThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    def serve():
        with ThreadingTCPServer(("127.0.0.1", 8787), Handler) as httpd:
            print(f"dashboard on http://127.0.0.1:8787/dashboard.html  (v{VERSION})", flush=True)
            httpd.serve_forever()

    threading.Thread(target=serve, daemon=True).start()
    while True:
        try:
            asyncio.run(run())
        except Exception:
            print("reconnecting:", traceback.format_exc()[:300], flush=True)
            time.sleep(5)