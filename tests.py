"""Regression tests for tracker alert logic + SF time encoding + ETA parser."""
import json, time, importlib.util, sys, datetime as dt, re, os, tempfile, asyncio
from zoneinfo import ZoneInfo

# derive paths from THIS file's location — works on any machine (Windows dev
# box resolves to the identical directory, Linux to ~/fsl_tracker)
_HERE = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.gettempdir()
spec = importlib.util.spec_from_file_location('tracker', os.path.join(_HERE, 'tracker.py'))
m = importlib.util.module_from_spec(spec)
sys.modules['tracker'] = m
spec.loader.exec_module(m)

# The suite exercises compute_alerts with synthetic services, and the alert
# engine fires real desktop toasts — so a plain test run spams the user's
# notification centre with fixture alerts (D22/D21b/P3, call 555xxx). Stub the
# notifier out: tests assert on alert OBJECTS, never on the toast itself.
_real_fire_toast = m.fire_toast          # kept intact for the T43 sound cases
m.fire_toast = lambda *a, **k: None

CH = ZoneInfo("America/Chicago")
now = int(time.time() * 1000)
ok = fail = 0

def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name} {detail}")
    else:
        fail += 1
        print(f"FAIL {name} {detail}")

# ---- encoding ----
sa_wall = 1788537120000   # raw SchedStartTime for SA-82369096
true_epoch = m.sf_ms_to_epoch(sa_wall)
check("encoding: sched 3:52 PM CDT",
      dt.datetime.fromtimestamp(true_epoch/1000, CH).strftime("%H:%M") == "15:52",
      f"-> {dt.datetime.fromtimestamp(true_epoch/1000, CH).strftime('%H:%M %p')}")

# ---- service factory: LMD is TRUE epoch ----
def mk(status, lmd_min_ago, sid, drv, call='999001', svc_lat=29.674117, svc_lng=-95.268745):
    sa = {'Id': sid, 'AppointmentNumber': 'SA-' + sid, 'D3_Call_ID__c': call,
          'WorkType': 'Battery Test', 'Status': status, 'StatusCategory': status,
          'WO_Call_Type__c': 'MEMBER', 'Latitude': svc_lat, 'Longitude': svc_lng,
          'LastModifiedDate': now - lmd_min_ago * 60000}
    return {'Resource': '0H' + drv, 'ResourceName': drv, 'Fields': {'s': 1, 'v': sa}}

def hist(lat, lng, spread=0.0, n=15, step=60):
    return [{'lat': lat + spread * (i % 3), 'lng': lng + spread * (i % 2),
             't': now - i * step * 1000} for i in range(n)]

st = m.State(); st.ingest_service(mk('Dispatched', 12, '08pT1', 'D1'))
st.drivers['0HD1'] = {'name': 'D1', 'pos': hist(29.76, -95.37)[0], 'history': hist(29.76, -95.37)}
check("T1 dispatch overdue", any(a['type'] == 'DISPATCH_OVERDUE' for a in st.snapshot()['alerts']))

st = m.State(); st.ingest_service(mk('En Route', 20, '08pT2', 'D2'))
# driver ~1.8 km from the service: mid-distance standstill
st.drivers['0HD2'] = {'name': 'D2', 'pos': hist(29.690, -95.280, 0.00001)[0],
                      'history': hist(29.690, -95.280, 0.00001, step=45)}
snap2 = st.snapshot()
check("T2 standing still (mid-distance)", any(a['type'] == 'STANDING_STILL' for a in snap2['alerts']),
      f"-> {[a['type'] for a in snap2['alerts']]}")

# T2b: standstill AT the service location -> NO alert, AT LOC on board
st = m.State(); st.ingest_service(mk('En Route', 20, '08pT2b', 'D2b'))
st.drivers['0HD2b'] = {'name': 'D2b', 'pos': hist(29.6741, -95.2687, 0.00001)[0],
                       'history': hist(29.6741, -95.2687, 0.00001, step=45)}
snap2b = st.snapshot()
check("T2b standstill at location -> no alert", not snap2b['alerts'],
      f"-> {[a['type'] for a in snap2b['alerts']]}")
row2b = [r for r in snap2b['rows'] if r['driver'] == 'D2b'][0]
check("T2b-b movement shows AT LOC", row2b['moving'] == 'AT LOC', f"-> {row2b['moving']}")

st = m.State(); st.ingest_service(mk('En Route', 20, '08pT3', 'D3'))
h = [{'lat': 29.60 + 0.001*i, 'lng': -95.35 + 0.001*i, 't': now - i*60000} for i in range(15)]
st.drivers['0HD3'] = {'name': 'D3', 'pos': h[0], 'history': h}
snap3 = st.snapshot()
check("T3 far+moving NO alert (far needs standstill)", not snap3['alerts'],
      f"-> {[a['type'] for a in snap3['alerts']]}")
row3 = [r for r in snap3['rows'] if r['driver'] == 'D3'][0]
check("T3b movement shows MOVING", row3['moving'] == 'MOVING', f"-> {row3['moving']}")

# T3c: standstill FAR away -> FAR_AWAY (not STANDING_STILL)
st = m.State(); st.ingest_service(mk('En Route', 20, '08pT3c', 'D3c'))
h = [{'lat': 29.60 + 0.00001*(i % 3), 'lng': -95.35 + 0.00001*(i % 2), 't': now - i*60000} for i in range(15)]
st.drivers['0HD3c'] = {'name': 'D3c', 'pos': h[0], 'history': h}
snap3c = st.snapshot()
types3c = [a['type'] for a in snap3c['alerts']]
check("T3c standstill far -> FAR_AWAY only", types3c == ['FAR_AWAY'], f"-> {types3c}")
if types3c:
    print("     detail:", snap3c['alerts'][0]['detail'])
row3c = [r for r in snap3c['rows'] if r['driver'] == 'D3c'][0]
check("T3c-b movement shows STANDSTILL", row3c['moving'] == 'STANDSTILL', f"-> {row3c['moving']}")

# T3d: standstill ~1.2 km away (outside AT LOC radius) -> STANDING_STILL
st = m.State(); st.ingest_service(mk('En Route', 20, '08pT3d', 'D3d', svc_lat=29.674117, svc_lng=-95.268745))
h = [{'lat': 29.685 + 0.00001*(i % 3), 'lng': -95.280 + 0.00001*(i % 2), 't': now - i*60000} for i in range(15)]
st.drivers['0HD3d'] = {'name': 'D3d', 'pos': h[0], 'history': h}
snap3d = st.snapshot()
types3d = [a['type'] for a in snap3d['alerts']]
check("T3d standstill mid-distance -> STANDING_STILL", types3d == ['STANDING_STILL'], f"-> {types3d}")

# T3e: moving but close -> no alert
st = m.State(); st.ingest_service(mk('En Route', 20, '08pT4', 'D4'))
h = [{'lat': 29.670 + 0.0005*i, 'lng': -95.270 + 0.0005*i, 't': now - i*60000} for i in range(15)]
st.drivers['0HD4'] = {'name': 'D4', 'pos': h[0], 'history': h}
snap4 = st.snapshot()
check("T4 close+moving no alert", not snap4['alerts'])
row4 = [r for r in snap4['rows'] if r['driver'] == 'D4'][0]
check("T4b movement shows MOVING", row4['moving'] == 'MOVING')

st = m.State()
st.ingest_service(mk('Dispatched', 30, '08pR', 'D5', call='888888'))
st.services['08pR']['call_type'] = 'RAP'
sa_r = {'Id': '08pR2', 'D3_Call_ID__c': '777777', 'Status': 'Scheduled',
        'StatusCategory': 'Scheduled', 'WO_Call_Type__c': 'MEMBER',
        'LastModifiedDate': now - 60000, 'Latitude': 29.6, 'Longitude': -95.2}
st.ingest_service({'Resource': '0HD5', 'ResourceName': 'D5', 'Fields': {'s': 1, 'v': sa_r}})
row = [r for r in st.snapshot()['rows'] if r['driver'] == 'D5']
check("T5 RAP skipped", row and row[0]['call_id'] == '777777')

st = m.State(); st.ingest_service(mk('Spotted', 70, '08pT6', 'D6'))
check("T6 member waiting", any(a['type'] == 'MEMBER_WAITING' for a in st.snapshot()['alerts']))

# T6b: the RED ALERT marker is a CONSOLE-COLOUR fact, NOT one of the tracker's
# own alerts. The FSL Gantt writes the service's GanttColor__c straight into the
# bar's inline background (verified on the live board: normal bars are #228B22
# with matching payload values), so red/orange = high-risk on the console. A
# green bar carrying an urgent tracker alert (Jose Mora 504009: DISPATCH_OVERDUE)
# must NOT be marked; a red bar with no tracker alert at all MUST be.
# (Must live up here: later tests monkeypatch State.driver_name.)
def mk_color(color, sid, drv, status='En Route', lmd=20, call='999001'):
    sa = {'Id': sid, 'AppointmentNumber': 'SA-' + sid, 'D3_Call_ID__c': call,
          'WorkType': 'Battery Test', 'Status': status, 'StatusCategory': status,
          'WO_Call_Type__c': 'MEMBER', 'Latitude': 29.674117,
          'Longitude': -95.268745, 'LastModifiedDate': now - lmd * 60000}
    if color is not None:
        sa['GanttColor__c'] = color
    return {'Resource': '0H' + drv, 'ResourceName': drv, 'Fields': {'s': 1, 'v': sa}}

check("T6b green console bar is NOT red", not m.console_red_alert('#228B22'))
check("T6b red console bar IS red", m.console_red_alert('#C70606'))
check("T6b orange console bar IS red", m.console_red_alert('#FFA500'))
check("T6b blue console bar is NOT red", not m.console_red_alert('#0070D2'))
check("T6b no/garbage colour is NOT red",
      not m.console_red_alert(None) and not m.console_red_alert('')
      and not m.console_red_alert('red') and not m.console_red_alert('#22'))

st = m.State()
st.ingest_service(mk_color('#228B22', '08pT6b', 'D6b', status='Dispatched', lmd=20))
row6b = [r for r in st.snapshot()['rows'] if r['driver'] == 'D6b'][0]
check("T6b-b green bar + DISPATCH_OVERDUE is NOT a red alert",
      'DISPATCH_OVERDUE' in row6b['alerts'] and row6b['red_alert'] is False,
      f"-> color={row6b['gantt_color']} alerts={row6b['alerts']} red={row6b['red_alert']}")

st = m.State(); st.ingest_service(mk_color('#C70606', '08pT6d', 'D6d'))
row6d = [r for r in st.snapshot()['rows'] if r['driver'] == 'D6d'][0]
check("T6c red bar with no tracker alert IS a red alert",
      row6d['red_alert'] is True and row6d['alerts'] == [],
      f"-> alerts={row6d['alerts']} red={row6d['red_alert']}")

st = m.State(); st.ingest_service(mk_color(None, '08pT6e', 'D6e'))
row6e = [r for r in st.snapshot()['rows'] if r['driver'] == 'D6e'][0]
check("T6d no colour in the payload -> never red",
      row6e['red_alert'] is False and row6e['gantt_color'] is None,
      f"-> color={row6e['gantt_color']} red={row6e['red_alert']}")

# scoping: two live services, only the red-bar one is marked
st = m.State()
st.ingest_service(mk_color('#228B22', '08pT6f', 'D6f', call='610001'))
st.ingest_service(mk_color('#C70606', '08pT6g', 'D6g', call='610002'))
snap6f = st.snapshot()
rf = [r for r in snap6f['rows'] if r['call_id'] == '610001'][0]
rg = [r for r in snap6f['rows'] if r['call_id'] == '610002'][0]
check("T6e red marker scoped to the red-bar service",
      rf['red_alert'] is False and rg['red_alert'] is True,
      f"-> green={rf['red_alert']} red={rg['red_alert']}")


# ---- GPS age after normalization: synthesize a ping 25 min old (true time),
# wall-encoded the way Salesforce does (Central wall clock printed as UTC) ----
true_ping = now - 25 * 60000
wall = dt.datetime.fromtimestamp(true_ping / 1000, CH).replace(tzinfo=None)
wall_ping = int(wall.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
st = m.State()
lp = {'0HGX': {'s': 1, 'v': {'Id': '0HGX', 'LastKnownLatitude': 29.7, 'LastKnownLongitude': -95.3,
                             'LastKnownLocationDate': wall_ping}}}
st.ingest_delta({'updatedLivePositions': lp, 'updatedGanttServices': {'s': 1, 'v': []}})
age = (now - st.drivers['0HGX']['pos']['t']) / 60000
check("GPS age normalized (~25 min, not 325)", 20 <= age <= 30, f"-> {age:.1f} min")

# ---- ETA parser vs real captured comment ----
feed_body = ('''<div class="feeditemcontent"><div class="feeditembody">
<span class="feeditemtext cxfeeditemtext">MIF ETA 25-30MIN&lt;STEPH</span></div>
<div class="feeditemfooter">Comment &middot; Like &middot; Today at 4:29 PM</div></div>''')
# pretend "today" is 2026-09-04 (capture day): build ref via now (parser uses today)
eta = m.parse_eta_from_feed(feed_body, now)
check("ETA parsed", eta is not None, f"-> {eta and eta['text']}")
if eta:
    lo_c = dt.datetime.fromtimestamp(eta['low']/1000, CH)
    hi_c = dt.datetime.fromtimestamp(eta['high']/1000, CH)
    print(f"     window: {lo_c.strftime('%H:%M')}–{hi_c.strftime('%H:%M')} (posted {dt.datetime.fromtimestamp(eta['posted']/1000, CH).strftime('%H:%M')})")
    check("ETA window is 25-30 min", 24 <= (eta['low']-eta['posted'])/60000 <= 26
          and 29 <= (eta['high']-eta['posted'])/60000 <= 31)

# single-number ETA
eta1 = m.parse_eta_from_feed('<span class="feeditemtext">ETA 20 MIN</span> Today at 3:00 PM', now)
check("ETA single", eta1 is not None and abs((eta1['low']-eta1['posted'])/60000 - 20) < 1)

# no false positive
check("ETA none when absent", m.parse_eta_from_feed('<span>no eta here</span> Today at 3:00 PM', now) is None)

# ---- ETA flows into row ----
st = m.State()
st.ingest_service(mk('En Route', 20, '08pE', 'D7'))
st.services['08pE']['parent_id'] = '0WO-parent'
st.etas['0WO-parent'] = {'low': now + 15*60000, 'high': now + 30*60000, 'posted': now - 10*60000}
row = [r for r in st.snapshot()['rows'] if r['driver'] == 'D7']
check("ETA in row", row and row[0]['eta'] is not None, f"-> {row and row[0]['eta']}")

# ---- vehicle in row ----
st = m.State()
sa_v = {'Id': '08pV', 'D3_Call_ID__c': '999009', 'Status': 'En Route',
        'StatusCategory': 'En Route', 'WO_Call_Type__c': 'MEMBER',
        'Latitude': 29.674, 'Longitude': -95.268, 'LastModifiedDate': now - 5*60000,
        'FSL_Member_Vehicle_Name__c': 'Silver 2013 Ford Edge - PS'}
st.ingest_service({'Resource': '0HD8', 'ResourceName': 'D8', 'Fields': {'s': 1, 'v': sa_v}})
rowv = [r for r in st.snapshot()['rows'] if r['driver'] == 'D8']
check("vehicle in row", rowv and rowv[0]['vehicle'] == 'Silver 2013 Ford Edge - PS',
      f"-> {rowv and rowv[0]['vehicle']}")

# ---- tow pair chain logic ----
# T7: head active -> follow head even if second is earlier-sorted... construct:
# head sched 10:00 (active, En Route), second sched 11:00 (Dispatched, stale)
st = m.State()
def tow(sa_id, call, status, sched, lmd_min):
    return {'Id': sa_id, 'AppointmentNumber': 'SA-' + sa_id, 'D3_Call_ID__c': call,
            'WorkType': 'Passenger Car Tow', 'Status': status, 'StatusCategory': status,
            'WO_Call_Type__c': 'MEMBER', 'Latitude': 29.674, 'Longitude': -95.268,
            'LastModifiedDate': now - lmd_min*60000, 'SchedStartTime': sched}
head = tow('08pH1', '111111', 'En Route', now - 60*60000, 10)
second = tow('08pS1', '222222', 'Dispatched', now - 0*60000, 40)
item_h = {'Resource': '0HP1', 'ResourceName': 'P1', 'Fields': {'s':1,'v':head},
          'relatedService1': '08pS1', 'relationshipType': 'Immediately Follow'}
item_s = {'Resource': '0HP1', 'ResourceName': 'P1', 'Fields': {'s':1,'v':second},
          'relatedService1': '08pH1', 'relationshipType': 'Immediately Follow'}
st.ingest_service(item_h); st.ingest_service(item_s)
snap7 = st.snapshot()
row7 = [r for r in snap7['rows'] if r['driver'] == 'P1'][0]
check("T7 pair head active -> follow head", row7['call_id'] == '111111', f"-> {row7['call_id']}")
check("T7b head en route 10min still OK", not snap7['alerts'])

# T8: head cleared, second still showing 'Dispatched' -> second becomes the
# followed service (its En Route/In Tow transitions are real), flagged
# chain_second, and its stale 'Dispatched' fires NO alert
st2 = m.State()
head2 = tow('08pH2', '333333', 'Cleared', now - 90*60000, 45)
second2 = tow('08pS2', '444444', 'Dispatched', now - 30*60000, 35)
item_h2 = {'Resource': '0HP2', 'ResourceName': 'P2', 'Fields': {'s':1,'v':head2},
           'relatedService1': '08pS2', 'relationshipType': 'Immediately Follow'}
item_s2 = {'Resource': '0HP2', 'ResourceName': 'P2', 'Fields': {'s':1,'v':second2},
           'relatedService1': '08pH2', 'relationshipType': 'Immediately Follow'}
st2.ingest_service(item_h2); st2.ingest_service(item_s2)
snap8 = st2.snapshot()
row8 = [r for r in snap8['rows'] if r['driver'] == 'P2'][0]
check("T8 head cleared -> follow second again", row8['call_id'] == '444444',
      f"-> {row8['call_id']}")
check("T8b stale Dispatched on chain second -> NO alert", not snap8['alerts'],
      f"-> {[a['type'] for a in snap8['alerts']]}")
check("T8c flagged chain_second", row8['chain_second'] is True)

# T8d: chain-second now En Route 20 min, standstill far away -> alert SHOULD
# fire (its En Route transitions are real; only 'Dispatched' is stale)
st2b = m.State()
head2b = tow('08pH2', '333333', 'Cleared', now - 90*60000, 45)
second2b = tow('08pS2', '444444', 'En Route', now - 30*60000, 20)
st2b.ingest_service({'Resource': '0HP2', 'ResourceName': 'P2', 'Fields': {'s':1,'v':head2b},
                     'relatedService1': '08pS2', 'relationshipType': 'Immediately Follow'})
st2b.ingest_service({'Resource': '0HP2', 'ResourceName': 'P2', 'Fields': {'s':1,'v':second2b},
                     'relatedService1': '08pH2', 'relationshipType': 'Immediately Follow'})
h = [{'lat': 29.60 + 0.00001*(i % 3), 'lng': -95.35 + 0.00001*(i % 2), 't': now - i*60000} for i in range(15)]
st2b.drivers['0HP2'] = {'name': 'P2', 'pos': h[0], 'history': h}
snap8d = st2b.snapshot()
check("T8d chain-second En Route standstill far -> FAR_AWAY fires",
      any(a['type'] == 'FAR_AWAY' for a in snap8d['alerts']),
      f"-> {[a['type'] for a in snap8d['alerts']]}")

# T8e: Rickey pattern — same call ID, same sched time on both legs, head
# stuck 'Dispatched', second already 'En Route' -> follow the SECOND
st3b = m.State()
head3 = tow('08pX1', '557712', 'Dispatched', now - 30*60000, 35)
second3 = tow('08pX2', '557712', 'En Route', now - 30*60000, 12)
st3b.ingest_service({'Resource': '0HX1', 'ResourceName': 'X1', 'Fields': {'s':1,'v':head3},
                     'relatedService1': '08pX2', 'relationshipType': 'Immediately Follow'})
st3b.ingest_service({'Resource': '0HX1', 'ResourceName': 'X1', 'Fields': {'s':1,'v':second3},
                     'relatedService1': '08pX1', 'relationshipType': 'Immediately Follow'})
h = [{'lat': 29.77 + 0.002*i, 'lng': -95.31 + 0.002*i, 't': now - i*60000} for i in range(15)]
st3b.drivers['0HX1'] = {'name': 'X1', 'pos': h[0], 'history': h}
snap3b = st3b.snapshot()
row3b = [r for r in snap3b['rows'] if r['driver'] == 'X1']
check("T8e same-call pair, head Dispatched + second En Route -> follow second",
      row3b and row3b[0]['call_id'] == '557712' and not snap3b['alerts'],
      f"-> call={row3b and row3b[0].get('call_id')} alerts={[a['type'] for a in snap3b['alerts']]}")

# T9: unrelated normal Dispatched still alerts (control)
st3 = m.State()
st3.ingest_service(mk('Dispatched', 12, '08pN', 'P3', call='555555'))
check("T9 normal dispatch overdue still fires",
      any(a['type'] == 'DISPATCH_OVERDUE' for a in st3.snapshot()['alerts']))

# T10: never-started Scheduled service drops off after STALE_SCHED_HOURS
def sf_wall(true_ms):
    """Encode a true epoch ms the way Salesforce does (Central wall as UTC)."""
    wall = dt.datetime.fromtimestamp(true_ms / 1000, CH).replace(tzinfo=None)
    return int(wall.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

st4 = m.State()
sa_old = {'Id': '08pOLD', 'D3_Call_ID__c': '666666', 'Status': 'Scheduled',
          'StatusCategory': 'Scheduled', 'WO_Call_Type__c': 'MEMBER',
          'Latitude': 29.6, 'Longitude': -95.2,
          'LastModifiedDate': now - 5*3600*1000,
          'SchedStartTime': sf_wall(now - int(m.STALE_SCHED_HOURS * 3600 * 1000) - 30*60000)}
st4.ingest_service({'Resource': '0HP9', 'ResourceName': 'P9', 'Fields': {'s':1,'v':sa_old}})
snap10 = st4.snapshot()
check("T10 stale Scheduled (dead) hidden", not [r for r in snap10['rows'] if r['driver']=='P9'])

# T10b: Scheduled whose time hasn't passed yet stays visible
st5 = m.State()
sa_future = dict(sa_old, Id='08pNEW', D3_Call_ID__c='666667',
                 SchedStartTime=sf_wall(now + 30*60000), LastModifiedDate=now - 60000)
st5.ingest_service({'Resource': '0HP9', 'ResourceName': 'P9', 'Fields': {'s':1,'v':sa_future}})
snap10b = st5.snapshot()
row10b = [r for r in snap10b['rows'] if r['driver'] == 'P9']
check("T10b upcoming Scheduled visible", row10b and row10b[0]['status'] == 'Scheduled')

# T10c: rescheduled to TOMORROW -> hidden from today's board
st6 = m.State()
tomorrow_nz = (dt.datetime.now(CH) + dt.timedelta(days=1)).replace(hour=10, minute=0)
tomorrow_ms = int(tomorrow_nz.timestamp() * 1000)
sa_tomo = dict(sa_old, Id='08pTOMO', D3_Call_ID__c='666668',
               SchedStartTime=m.sf_ms_to_epoch(tomorrow_ms), LastModifiedDate=now - 60000)
st6.ingest_service({'Resource': '0HP9', 'ResourceName': 'P9', 'Fields': {'s':1,'v':sa_tomo}})
snap10c = st6.snapshot()
check("T10c tomorrow-rescheduled hidden",
      not [r for r in snap10c['rows'] if r['driver'] == 'P9'])

# T11: cleared-services log — pair dedup, wait time, 12 h window
st7 = m.State()
t0 = now - 2*3600*1000  # service happened 2 h ago

def done_svc(sa, call, rid, name, sched_ms, arrived_ms, cleared_ms, status='Cleared'):
    """Ingest a service and fast-forward it through its lifecycle."""
    m.now_ms = lambda: sched_ms
    st7.ingest_service({'Resource': rid, 'ResourceName': name, 'Fields': {'s':1,'v':{
        'Id': sa, 'D3_Call_ID__c': call, 'Status': 'Scheduled',
        'StatusCategory': 'Scheduled', 'WO_Call_Type__c': 'TOW',
        'Latitude': 29.6, 'Longitude': -95.2, 'SchedStartTime': m.sf_ms_to_epoch(sched_ms),
        'LastModifiedDate': sched_ms - 60000, 'Phone_Number__c': '713-555-0100',
        'FSL_Member_Vehicle_Name__c': '2019 Honda Civic'}}})
    m.now_ms = lambda: arrived_ms
    st7.ingest_service({'Resource': rid, 'ResourceName': name, 'Fields': {'s':1,'v':{
        'Id': sa, 'D3_Call_ID__c': call, 'Status': 'On Location',
        'StatusCategory': 'Dispatched', 'WO_Call_Type__c': 'TOW',
        'SchedStartTime': m.sf_ms_to_epoch(sched_ms),
        'LastModifiedDate': arrived_ms - 60000}}})
    m.now_ms = lambda: cleared_ms
    st7.ingest_service({'Resource': rid, 'ResourceName': name, 'Fields': {'s':1,'v':{
        'Id': sa, 'D3_Call_ID__c': call, 'Status': status,
        'StatusCategory': 'Completed', 'WO_Call_Type__c': 'TOW',
        'SchedStartTime': m.sf_ms_to_epoch(sched_ms),
        'LastModifiedDate': cleared_ms - 60000}}})

# pair: two SAs, same call, related
st7.services['08pH'] = {'sa_id': '08pH', 'appt': 'SA-1', 'call_id': '7001',
                        'status': 'Cleared', 'resource_id': '0HPH',
                        'resource_name': 'Head Driver', 'phone': '713-555-0100',
                        'vehicle': '2019 Honda Civic', 'call_type': 'TOW',
                        'sched_start': t0, 'arrived_at': t0 + 40*60000,
                        'arrived_live': True,
                        'cleared_at': t0 + 90*60000,
                        'related': '08pS', 'reltype': 'Immediately Follow',
                        'status_history': []}
st7.services['08pS'] = dict(st7.services['08pH'], sa_id='08pS', appt='SA-2',
                            related='08pH', sched_start=t0 + 60*60000,
                            arrived_at=t0 + 100*60000, cleared_at=t0 + 95*60000)
# standalone cleared service from a different driver
st7.services['08pX'] = {'sa_id': '08pX', 'appt': 'SA-3', 'call_id': '7002',
                        'status': 'Cleared', 'resource_id': '0HPX',
                        'resource_name': 'Solo Driver', 'phone': '281-555-0200',
                        'vehicle': '2015 F-150', 'call_type': 'LOCKOUT',
                        'work_type_name': 'LOCKOUT',
                        'sched_start': t0, 'arrived_at': t0 + 25*60000,
                        'arrived_live': True,
                        'cleared_at': now - 3*60000, 'status_history': []}
# cleared 13 h ago — outside the window, must NOT appear
st7.services['08pOLD'] = {'sa_id': '08pOLD', 'appt': 'SA-4', 'call_id': '7003',
                          'status': 'Cleared', 'resource_id': '0HPX',
                          'resource_name': 'Solo Driver', 'call_type': 'TOW',
                          'sched_start': now - 13*3600*1000,
                          'arrived_at': now - 12*3600*1000,
                          'cleared_at': now - 13*3600*1000, 'status_history': []}
clog = st7.cleared_log()
check("T11a cleared log has 2 rows (pair deduped, old dropped)", len(clog) == 2)
pair_rows = [c for c in clog if c['call_id'] == '7001']
check("T11b pair shows once with head's data",
      len(pair_rows) == 1 and pair_rows[0]['driver'] == 'Head Driver')
check("T11c wait time = arrived - sched (40 min)",
      pair_rows[0]['wait_min'] == 40)
solo = [c for c in clog if c['call_id'] == '7002'][0]
check("T11d solo row fields (phone, vehicle, type, driver, wait)",
      solo['phone'] == '281-555-0200' and solo['vehicle'] == '2015 F-150'
      and solo['work_type'] == 'LOCKOUT' and solo['driver'] == 'Solo Driver'
      and solo['wait_min'] == 25)
check("T11e newest-cleared first", clog[0]['call_id'] == '7002')

# T13: exact wait times from SA feed (Steffon case: Sched 4:40 PM, OnLoc
# 5:23 PM = 43 min wait; Cleared 5:38 PM = 15 min on scene)
st7.feed_times['08pX'] = {
    'Scheduled': t0, 'Dispatched': t0 + 20*60000,
    'On Location': t0 + 43*60000, 'Cleared': t0 + 58*60000}
clog2 = st7.cleared_log()
solo2 = [c for c in clog2 if c['call_id'] == '7002'][0]
check("T13a feed-based member wait = 43 min", solo2['wait_min'] == 43)
check("T13b feed-based on-scene = 15 min", solo2['onscene_min'] == 15)

# T13c: RAP services excluded from callback
st7.services['08pRAP'] = {'sa_id': '08pRAP', 'appt': 'SA-9', 'call_id': '7004',
                          'status': 'Cleared', 'resource_id': '0HPX',
                          'resource_name': 'Solo Driver', 'call_type': 'RAP',
                          'work_type_name': 'Roadside Assistance Program',
                          'sched_start': t0, 'arrived_at': t0 + 60000,
                          'cleared_at': now - 5*60000, 'status_history': []}
clog3 = st7.cleared_log()
check("T13c RAP not in callback", not [c for c in clog3 if c['call_id'] == '7004'])

# T12: external/contractor drivers are excluded everywhere
st8 = m.State()
ext = {'Id': '08pEXT', 'D3_Call_ID__c': '8001', 'Status': 'Dispatched',
       'StatusCategory': 'Dispatched', 'WO_Call_Type__c': 'TOW',
       'Latitude': 29.6, 'Longitude': -95.2,
       'SchedStartTime': m.sf_ms_to_epoch(now - 20*60000),
       'LastModifiedDate': now - 20*60000}
st8.ingest_service({'Resource': '0HPEXT', 'ResourceName': '198114 - Jamal Awawda',
                    'Fields': {'s': 1, 'v': ext}})
snap12 = st8.snapshot()
check("T12a external driver not on board",
      not [r for r in snap12['rows'] if 'Jamal' in (r['driver'] or '')])
check("T12b external driver gets no alerts",
      not [a for a in snap12['alerts'] if 'Jamal' in (a.get('driver') or '')])
# same call reassigned back to a regular driver -> visible again
st8.ingest_service({'Resource': '0HP9', 'ResourceName': 'P9',
                    'Fields': {'s': 1, 'v': dict(ext, Id='08pREG')}})
snap12b = st8.snapshot()
check("T12c reassigned call visible under regular driver",
      any(r['call_id'] == '8001' for r in snap12b['rows']))

# T14: ETA semantics — stale flag + ETA_EXPIRED alert
# T14a: ETA posted BEFORE dispatch (other team pre-arrival estimate) -> '*'
st9 = m.State()
now9 = m.now_ms()
eta_post = now9 - 40*60000          # posted while still Scheduled
st9.etas['08pT14'] = {"low": eta_post + 15*60000, "high": eta_post + 20*60000,
                      "posted": eta_post, "text": "ETA 15-20 M"}
svc14 = {'Id': '08pT14', 'D3_Call_ID__c': '9001', 'Status': 'En Route',
         'StatusCategory': 'En Route', 'WO_Call_Type__c': 'TOW',
         'Latitude': 29.6, 'Longitude': -95.2,
         'SchedStartTime': m.sf_ms_to_epoch(now9 - 60*60000),
         'LastModifiedDate': eta_post - 5*60000}
st9.ingest_service({'Resource': '0HP14', 'ResourceName': 'T14 Driver',
                    'Fields': {'s': 1, 'v': svc14}})
# driver went En Route 10 min ago (after the ETA post)
st9.services['08pT14']['status_since'] = now9 - 10*60000
snap14 = st9.snapshot()
row14 = [r for r in snap14['rows'] if r['call_id'] == '9001'][0]
check("T14a pre-dispatch ETA marked stale ('*')", row14['eta_stale'] is True)
check("T14a2 pre-dispatch ETA fires NO ETA_EXPIRED alert",
      not [a for a in snap14['alerts'] if a['type'] == 'ETA_EXPIRED'])
# T14b: ETA posted AFTER dispatch, window passed, driver far -> alert
eta_post2 = now9 - 35*60000         # posted 5 min after En Route
# (en route since now9-40min — the post came after the driver got moving)
st9.services['08pT14']['status_since'] = now9 - 40*60000
st9.etas['08pT14'] = {"low": eta_post2 + 10*60000, "high": eta_post2 + 15*60000,
                      "posted": eta_post2, "text": "ETA 10-15 M"}
# far away: GPS 8 km from the service, recent movement
st9.drivers.setdefault('0HP14', {"name": "T14 Driver", "pos": None, "history": []})
base_pos = {"lat": 29.6, "lng": -95.12, "t": now9 - 2*60000}
st9.drivers['0HP14']['pos'] = base_pos
st9.drivers['0HP14']['history'] = [
    {"lat": 29.6 + i*0.001, "lng": -95.12, "t": now9 - (30 - i) * 60000}
    for i in range(4)]
snap14b = st9.snapshot()
row14b = [r for r in snap14b['rows'] if r['call_id'] == '9001'][0]
check("T14b post-dispatch fresh ETA not stale", row14b['eta_stale'] is False)
check("T14b ETA passed + >10 min out -> ETA_EXPIRED",
      any(a['type'] == 'ETA_EXPIRED' and a['call_id'] == '9001'
          for a in snap14b['alerts']))
# T14c: same but driver CLOSE (1 km) -> no alert (about to arrive)
st9.drivers['0HP14']['pos'] = {"lat": 29.607, "lng": -95.2, "t": now9 - 60000}
st9.drivers['0HP14']['history'] = [
    {"lat": 29.606 + i*0.0005, "lng": -95.2, "t": now9 - (20 - i) * 60000}
    for i in range(4)]
snap14c = st9.snapshot()
check("T14c ETA passed but close -> no ETA_EXPIRED",
      not [a for a in snap14c['alerts'] if a['type'] == 'ETA_EXPIRED'])

# T15: tow-pair drop-off — standstill far from pickup but AT the mate leg's
# location (Anthony Ramsey case: car already towed to drop-off, status not
# flipped) -> no FAR_AWAY/STANDING_STILL alert, board shows AT DROP OFF
st10 = m.State()
now10 = m.now_ms()
# head leg (pickup): I-610 & Lawndale
svc15 = {'Id': '08pT15A', 'D3_Call_ID__c': '9501', 'Status': 'En Route',
         'StatusCategory': 'En Route', 'WO_Call_Type__c': 'TOW',
         'Latitude': 29.761, 'Longitude': -95.272,
         'SchedStartTime': m.sf_ms_to_epoch(now10 - 90*60000),
         'LastModifiedDate': now10 - 70*60000}
st10.ingest_service({'Resource': '0HP15', 'ResourceName': 'Anthony T15',
                     'Fields': {'s': 1, 'v': svc15}})
st10.services['08pT15A']['status_since'] = now10 - 70*60000
# mate leg (drop-off) 8 km away with its own coords
svc15b = {'Id': '08pT15B', 'D3_Call_ID__c': '9501', 'Status': 'Dispatched',
          'StatusCategory': 'Dispatched', 'WO_Call_Type__c': 'TOW',
          'Latitude': 29.72, 'Longitude': -95.30,
          'SchedStartTime': m.sf_ms_to_epoch(now10 - 60*60000),
          'LastModifiedDate': now10 - 60*60000}
st10.ingest_service({'Resource': '0HP15', 'ResourceName': 'Anthony T15',
                     'Fields': {'s': 1, 'v': svc15b}})
# link the pair: head.related = mate
st10.services['08pT15A']['related'] = '08pT15B'
st10.services['08pT15A']['reltype'] = 'Immediately Follow'
# driver standstill AT the drop-off (mate) location: stationary 12 min
st10.drivers['0HP15'] = {"name": "Anthony T15", "pos": None, "history": []}
st10.drivers['0HP15']['pos'] = {"lat": 29.7201, "lng": -95.3001, "t": now10 - 60000}
st10.drivers['0HP15']['history'] = [
    {"lat": 29.7201 + i*0.00001, "lng": -95.3001, "t": now10 - (11 - i) * 60000}
    for i in range(4)]
snap15 = st10.snapshot()
row15 = [r for r in snap15['rows'] if r['call_id'] == '9501'][0]
check("T15a at drop-off -> no FAR_AWAY/STANDING_STILL alert",
      not [a for a in snap15['alerts'] if a['type'] in ('FAR_AWAY', 'STANDING_STILL')])
check("T15b board shows AT DROP OFF", row15['moving'] == 'AT DROP OFF')
# T15c: same standstill but NOT near the drop-off -> alert still fires
st10.drivers['0HP15']['pos'] = {"lat": 29.79, "lng": -95.35, "t": now10 - 60000}
st10.drivers['0HP15']['history'] = [
    {"lat": 29.790 + i*0.00001, "lng": -95.35, "t": now10 - (11 - i) * 60000}
    for i in range(4)]
snap15b = st10.snapshot()
check("T15c standstill far from both points -> still alerts",
      any(a['type'] in ('FAR_AWAY', 'STANDING_STILL')
          for a in snap15b['alerts']))

# T16: confirmed-external resource blocklist ('JAD (A)', resource 174584)
st11 = m.State()
now11 = m.now_ms()
svc17 = {'Id': '08pT17', 'D3_Call_ID__c': '9701', 'Status': 'Tow Loaded',
         'StatusCategory': 'Dispatched', 'WO_Call_Type__c': 'TOW',
         'Latitude': 29.6, 'Longitude': -95.2,
         'SchedStartTime': m.sf_ms_to_epoch(now11 - 30*60000),
         'LastModifiedDate': now11 - 30*60000}
st11.ingest_service({'Resource': '0HP17', 'ResourceName': 'JAD (A) 346 772 5992 (174584 )',
                     'Fields': {'s': 1, 'v': svc17}})
# simulate the blocklist mapping: resource id carries the SF resource number here
st11.services['08pT17']['resource_id'] = '174584'
snap17 = st11.snapshot()
check("T16 blocklisted external resource hidden from board",
      not [r for r in snap17['rows'] if 'JAD' in (r['driver'] or '')])
# regular driver with odd name format but different id stays visible
st11b = m.State()
st11b.ingest_service({'Resource': '0HP18', 'ResourceName': 'Rojae Marcel(49)   713 384 3954(199309 )',
                      'Fields': {'s': 1, 'v': dict(svc17, Id='08pT18', D3_Call_ID__c='9702')}})
st11b.services['08pT18']['resource_id'] = '199309'
snap17b = st11b.snapshot()
check("T16b regular driver with odd name format still visible",
      any('Rojae' in (r['driver'] or '') for r in snap17b['rows']))

print()
# T17: reverse-linked pair (only the SECOND leg carries 'related') — dropoff and
# AT DROP OFF must still resolve via the reverse lookup
st12 = m.State()
now12 = m.now_ms()
head = {'Id': '08pT17A', 'D3_Call_ID__c': '9801', 'Status': 'En Route',
        'WO_Call_Type__c': 'TOW', 'Latitude': 29.761, 'Longitude': -95.272,
        'Street': '619 W 27th St',
        'SchedStartTime': m.sf_ms_to_epoch(now12 - 90*60000),
        'LastModifiedDate': now12 - 70*60000}
st12.ingest_service({'Resource': '0HP17', 'ResourceName': 'Rev Tester',
                     'Fields': {'s': 1, 'v': head}})
st12.services['08pT17A']['status_since'] = now12 - 70*60000
# second leg: only THIS one has related -> head (reverse link only)
second = {'Id': '08pT17B', 'D3_Call_ID__c': '9801', 'Status': 'Dispatched',
          'WO_Call_Type__c': 'TOW', 'Latitude': 29.72, 'Longitude': -95.30,
          'Street': '55 dealer rd', 'City': 'Houston',
          'Related_Service__c': '08pT17A',
          'SchedStartTime': m.sf_ms_to_epoch(now12 - 60*60000),
          'LastModifiedDate': now12 - 60*60000}
st12.ingest_service({'Resource': '0HP17', 'ResourceName': 'Rev Tester',
                     'Fields': {'s': 1, 'v': second}})
st12.services['08pT17B']['reltype'] = 'Immediately Follow'
snap17 = st12.snapshot()
row17 = [r for r in snap17['rows'] if r['call_id'] == '9801'][0]
check("T17a reverse-linked pair: dropoff shown", row17['dropoff'] == '55 dealer rd, Houston')
print()
# T18: reassigned call — feed has TWO Dispatched posts (two drivers); the member
# wait still measures from the EARLIEST Scheduled (true member wait)
st13 = m.State()
now13 = m.now_ms()
# feed: Scheduled t0, Dispatched twice (first driver 4:40, second 5:10), OnLoc 5:23
t_sched = now13 - 120*60000
t_d1 = now13 - 110*60000
t_d2 = now13 - 80*60000
t_onloc = now13 - 67*60000
t_cleared = now13 - 52*60000
st13.feed_times['08pT18'] = {"Scheduled": t_sched, "Dispatched": t_d2,
                             "On Location": t_onloc, "Cleared": t_cleared}
# (reassigned detection kept in the feed parse for the audit log only)
svc18 = {'Id': '08pT18', 'D3_Call_ID__c': '9901', 'Status': 'Cleared',
         'WO_Call_Type__c': 'TOW', 'Street': '1 test st',
         'SchedStartTime': m.sf_ms_to_epoch(t_sched),
         'LastModifiedDate': now13 - 52*60000}
st13.ingest_service({'Resource': '0HP18', 'ResourceName': 'Reassign Tester',
                     'Fields': {'s': 1, 'v': svc18}})
st13.services['08pT18']['cleared_at'] = t_cleared
clog18 = st13.cleared_log()
row18 = [c for c in clog18 if c['call_id'] == '9901'][0]
# waited = OnLoc(67min ago) - latest Dispatch(80min ago) = 13 min (not 110-67=43)
check("T18 reassigned: wait from earliest Scheduled (53 min)", row18['wait_min'] == 53)
# control: single-dispatch call still uses earliest (Scheduled)
st14 = m.State()
st14.feed_times['08pT19'] = {"Scheduled": t_sched, "Dispatched": t_d1,
                             "On Location": t_onloc, "Cleared": t_cleared}
st14.ingest_service({'Resource': '0HP19', 'ResourceName': 'Normal Tester',
                     'Fields': {'s': 1, 'v': dict(svc18, Id='08pT19', D3_Call_ID__c='9902')}})
st14.services['08pT19']['cleared_at'] = t_cleared
clog19 = st14.cleared_log()
row19 = [c for c in clog19 if c['call_id'] == '9902'][0]
# normal: waited = OnLoc - Scheduled = 120-67 = 53
check("T19 normal call: wait from Scheduled (53 min)", row19['wait_min'] == 53)

print()
# T20: followed = destination (later) leg -> dropoff not shown (redundant; the
# Address column IS the drop-off) and display stays stable across leg flips
st15 = m.State()
now15 = m.now_ms()
head20 = {'Id': '08pT20A', 'D3_Call_ID__c': '9950', 'Status': 'Cleared',
          'WO_Call_Type__c': 'TOW', 'Street': '619 W 27th St',
          'SchedStartTime': m.sf_ms_to_epoch(now15 - 90*60000),
          'LastModifiedDate': now15 - 70*60000}
st15.ingest_service({'Resource': '0HP20', 'ResourceName': 'Dest Tester',
                     'Fields': {'s': 1, 'v': head20}})
st15.services['08pT20A']['related'] = '08pT20B'
st15.services['08pT20A']['reltype'] = 'Immediately Follow'
dest = {'Id': '08pT20B', 'D3_Call_ID__c': '9950', 'Status': 'Tow Loaded',
        'WO_Call_Type__c': 'TOW', 'Street': '55 dealer rd', 'City': 'Houston',
        'SchedStartTime': m.sf_ms_to_epoch(now15 - 60*60000),
        'LastModifiedDate': now15 - 60*60000}
st15.ingest_service({'Resource': '0HP20', 'ResourceName': 'Dest Tester',
                     'Fields': {'s': 1, 'v': dest}})
st15.services['08pT20B']['related'] = '08pT20A'
st15.services['08pT20B']['reltype'] = 'Immediately Follow'
row20 = [r for r in st15.snapshot()['rows'] if r['call_id'] == '9950'][0]
check("T20 followed=destination leg: no duplicate dropoff", row20['dropoff'] is None)

print()

print()
# T21: driver busy on a RAP call (On Location) + regular call Dispatched 20 min
# ago -> NO dispatch-overdue alert (they're legitimately on the RAP)
st21 = m.State()
st21.ingest_service(mk('On Location', 30, '08pT21r', 'D21', call='555111'))
st21.services['08pT21r']['call_type'] = 'RAP'
st21.ingest_service(mk('Dispatched', 20, '08pT21d', 'D21', call='555112'))
snap21 = st21.snapshot()
types21 = [a['type'] for a in snap21['alerts']]
check("T21 dispatched regular call suppressed while on RAP",
      not any(t == 'DISPATCH_OVERDUE' for t in types21), f"-> {types21}")
# control: without the RAP, the overdue alert fires
st21b = m.State()
st21b.ingest_service(mk('Dispatched', 20, '08pT21e', 'D21b', call='555113'))
check("T21b control: overdue fires without RAP",
      any(a['type'] == 'DISPATCH_OVERDUE' for a in st21b.snapshot()['alerts']))

# T22: driver actively on a RAP call -> visible queued call shows status 'On RAP'
st22 = m.State()
st22.ingest_service(mk('On Location', 30, '08pT22r', 'D22', call='555211'))
st22.services['08pT22r']['call_type'] = 'RAP'
st22.ingest_service(mk('Dispatched', 20, '08pT22d', 'D22', call='555212'))
row22 = [r for r in st22.snapshot()['rows'] if r['driver'] == 'D22'][0]
check("T22 queued call shows On RAP while driver busy", row22['status'] == 'On RAP',
      f"-> {row22['status']}")
# control: RAP cleared -> real Dispatched shows again
st22.services['08pT22r']['status'] = 'Cleared'
st22.services['08pT22r']['cleared_at'] = m.now_ms()
check("T22b RAP cleared -> Dispatched restored",
      [r for r in st22.snapshot()['rows'] if r['driver'] == 'D22'][0]['status'] == 'Dispatched')

# ---- T23: custom rule engine ----
_r1 = {"id":"x","name":"ER>25","match":"all","conds":[
    {"field":"status","op":"eq","value":"En Route"},
    {"field":"enroute_min","op":"gte","value":25}]}
_ctx = {"status":"En Route","enroute_min":31.0,"dist_km":2.0}
check("T23a rule all-match fires", m.rule_matches(_r1, _ctx) is True)
check("T23b rule below threshold", m.rule_matches(_r1, {"status":"En Route","enroute_min":10}) is False)
check("T23c rule wrong status", m.rule_matches(_r1, {"status":"Dispatched","enroute_min":31}) is False)
_r2 = {"id":"y","name":"any","match":"any","conds":[
    {"field":"status","op":"eq","value":"On Location"},
    {"field":"enroute_min","op":"gte","value":25}]}
check("T23d rule any-match", m.rule_matches(_r2, _ctx) is True)
check("T23e empty conds never fire", m.rule_matches({"conds":[]}, _ctx) is False)
check("T23f contains op", m.rule_matches(
    {"conds":[{"field":"work_type","op":"contains","value":"tow"}]},
    {"work_type":"Passenger Car Tow"}) is True)
check("T23g null field with gte is safe", m.rule_matches(
    {"conds":[{"field":"dist_km","op":"gte","value":5}]}, {"dist_km":None}) is False)

# ---- T24: disabled builtins gate emissions ----
_st = m.State.__new__(m.State)  # bare instance
def _svc(**kw):
    base = {"sa_id":"SA-T24","resource_id":"R1","call_id":"C1","status":"Dispatched",
            "status_since": now - 30*60000, "last_modified": now - 30*60000,
            "cleared": False, "chain_second": False, "work_type":"Tow",
            "sched_start": now, "appt":"x"}
    base.update(kw); return base
_svc_obj = _svc()
_st.services = {"SA-T24": _svc_obj}
_st.drivers = {"R1": {"pos": {"lat": None, "lng": None, "t": None}, "history": []}}
_st.alerts = {}
_st.etas = {}
_st.cleared_log = lambda self=None: []
_orig = m.State.first_service
m.State.first_service = lambda self, rid: _svc()
m.State.busy_on_rap = lambda self, rid: False
m.State.driver_name = lambda self, rid: "Test Driver"
m.State.is_external = lambda self, s: False
m.State.future_day = lambda self, s: False
m.State.cleared = lambda self, s: False
m.drivers_pos = {}
try:
    old_settings = dict(m.SETTINGS)
    m.SETTINGS["disabled_builtins"] = ["DISPATCH_OVERDUE"]
    m.SETTINGS["rules"] = []
    _al = m.State.compute_alerts(_st)
    check("T24a disabled builtin suppressed",
          not any(a["type"]=="DISPATCH_OVERDUE" for a in _al.values()))
    m.SETTINGS["disabled_builtins"] = []
    m.SETTINGS["rules"] = [{"id":"c1","name":"Disp>20","enabled":True,"level":"urgent",
                            "match":"all","conds":[{"field":"dispatch_min","op":"gte","value":20}]}]
    _al2 = m.State.compute_alerts(_st)
    types2 = [a["type"] for a in _al2.values()]
    check("T24b builtin fires when enabled", "DISPATCH_OVERDUE" in types2)
    customs = [a for a in _al2.values() if a["type"]=="CUSTOM"]
    check("T24c custom rule fires", len(customs)==1 and customs[0]["custom_name"]=="Disp>20",
          f"-> {types2}")
    check("T24d custom key binds to service",
          any(k.startswith("custom:c1:SA-T24") for k in _al2))
    m.SETTINGS["rules"] = [{"id":"c2","name":"no","enabled":True,"match":"all",
                            "conds":[{"field":"dispatch_min","op":"gte","value":90}]}]
    _al3 = m.State.compute_alerts(_st)
    check("T24e custom rule not matched stays silent",
          not any(a["type"]=="CUSTOM" for a in _al3.values()))
    # ---- T25: builtin threshold overrides ----
    m.SETTINGS["disabled_builtins"] = []
    m.SETTINGS["rules"] = []
    m.SETTINGS["builtin_overrides"] = {"DISPATCH_OVERDUE": {"dispatch_min": 45}}
    _al4 = m.State.compute_alerts(_st)   # dispatched 30 min ago
    check("T25a override 45min suppresses 30min dispatch",
          not any(a["type"] == "DISPATCH_OVERDUE" for a in _al4.values()))
    _st.services["SA-T24"]["status_since"] = now - 50*60000
    _st.services["SA-T24"]["last_modified"] = now - 50*60000
    m.State.first_service = lambda self, rid: _st.services["SA-T24"]
    _al5 = m.State.compute_alerts(_st)
    check("T25b override 45min fires at 50min",
          any(a["type"] == "DISPATCH_OVERDUE" for a in _al5.values()))
    m.SETTINGS["builtin_overrides"] = {}
    _st.services["SA-T24"]["status_since"] = now - 30*60000
    _st.services["SA-T24"]["last_modified"] = now - 30*60000
    _al6 = m.State.compute_alerts(_st)
    check("T25c default threshold restored",
          any(a["type"] == "DISPATCH_OVERDUE" for a in _al6.values()))

    # ---- T26: ETA conditions in custom rules ----
    m.SETTINGS["disabled_builtins"] = []
    m.SETTINGS["rules"] = []
    m.SETTINGS["builtin_overrides"] = {}
    _st.services["SA-T24"]["status"] = "En Route"
    _st.services["SA-T24"]["status_since"] = now - 40*60000
    _st.services["SA-T24"]["last_modified"] = now - 40*60000
    _st.drivers["R1"]["pos"] = {"lat": 29.76, "lng": -95.36, "t": now}
    _st.drivers["R1"]["history"] = [
        {"lat": 29.76, "lng": -95.36, "t": now-600000},
        {"lat": 29.7601, "lng": -95.3601, "t": now-300000},
        {"lat": 29.7602, "lng": -95.3602, "t": now}]
    _st.services["SA-T24"]["lat"] = 29.80; _st.services["SA-T24"]["lng"] = -95.40
    _eta = {"posted": now - 39*60000, "low": now - 10*60000,
            "high": now - 5*60000, "text": "x-y"}
    _st.etas = {"SA-T24": _eta}
    # rule: no ETA at all
    m.SETTINGS["rules"] = [{"id":"e1","name":"no eta","enabled":True,"level":"minor",
                            "match":"all","conds":[{"field":"eta_exists","op":"eq","value":"false"}]}]
    _a = m.State.compute_alerts(_st)
    check("T26a eta_exists=false does not fire when ETA present",
          not any(a.get("rule_id")=="e1" for a in _a.values()))
    _st.etas = {}
    _a = m.State.compute_alerts(_st)
    check("T26b eta_exists=false fires when no ETA",
          any(a.get("rule_id")=="e1" for a in _a.values()))
    # rule: ETA passed > 3 min ago
    m.SETTINGS["rules"] = [{"id":"e2","name":"eta passed","enabled":True,"level":"urgent",
                            "match":"all","conds":[{"field":"eta_exists","op":"eq","value":"true"},
                                                    {"field":"eta_passed_min","op":"gte","value":3}]}]
    _st.etas = {"SA-T24": _eta}   # high was 5 min ago -> passed 5 min
    _a = m.State.compute_alerts(_st)
    check("T26c eta_passed_min fires when window passed",
          any(a.get("rule_id")=="e2" for a in _a.values()))
    _eta2 = {"posted": now - 39*60000, "low": now + 10*60000,
             "high": now + 15*60000, "text": "x-y"}
    _st.etas = {"SA-T24": _eta2}  # future ETA
    _a = m.State.compute_alerts(_st)
    check("T26d future ETA: no fire",
          not any(a.get("rule_id")=="e2" for a in _a.values()))
    # stale ETA (posted before current status) must not count
    _eta3 = {"posted": now - 50*60000, "low": now - 20*60000,
             "high": now - 15*60000, "text": "x-y"}
    _st.etas = {"SA-T24": _eta3}
    m.SETTINGS["rules"] = [{"id":"e1","name":"no eta","enabled":True,"level":"minor",
                            "match":"all","conds":[{"field":"eta_exists","op":"eq","value":"false"}]},
                           {"id":"e2","name":"eta passed","enabled":True,"level":"urgent","match":"all",
                            "conds":[{"field":"eta_exists","op":"eq","value":"true"},
                                     {"field":"eta_passed_min","op":"gte","value":3}]}]
    _a = m.State.compute_alerts(_st)
    check("T26e stale ETA counts as no ETA",
          any(a.get("rule_id")=="e1" for a in _a.values())
          and not any(a.get("rule_id")=="e2" for a in _a.values()))
    _st.etas = {}

    m.SETTINGS.clear(); m.SETTINGS.update(old_settings)
finally:
    m.State.first_service = _orig


# ---- T27: ETA regex against real feed wording (scouted 2026-09-14, 80 WO feeds) ----
def _eta_parse(body_text):
    # wrap text the way feed HTML does so FEED_TEXT_RE finds it
    html = '<span class="feeditemtext"> ' + body_text + ' </span>' + \
           '<span class="feeditemtext">Today at 3:00 PM</span>'
    return m.parse_eta_from_feed(html, now)

_eta_cases = [
    # (comment text, expected low-min, high-min or None for no-match)
    ("MIF ETA 25-30MINS", 25, 30),
    ("ETA 15 to 20 minutes", 15, 20),
    ("Have delay eta35/45mins", 35, 45),
    ("---kmi--updated eta---15-20min---mbr cb nap//lrm//rs", 15, 20),
    ("ETA 10-15 MINS BY SD", 10, 15),
    ("c 631252 eta 20-25mins spoke w/Fabi", 20, 25),
    ("ETA is 10-15 min", 10, 15),
    ("ETA 25MIN< STEPH", 25, 25),
    ("ETA 15 PER DRIVER", 15, 15),
    ("ETA Confirmed 25 minutes", 25, 25),
    ("ETA 30 minutes", 30, 30),
    ("ETA 12 MIN", 12, 12),
    # no-match cases
    ("INC - Expired - 631", None, None),
    ("inc ics 1252 adv", None, None),
    ("ETA was updated", None, None),
    ("eta given", None, None),
    ("driver will call mbr for eta", None, None),
    ("ETA 2026-08-09 timestamp", None, None),
]
for _txt, _lo, _hi in _eta_cases:
    _got = _eta_parse(_txt)
    if _lo is None:
        check("T27 no-match: " + _txt[:34], _got is None,
              f"-> {_got and _got['text']}")
    else:
        okc = _got is not None and _got["text"]
        _nums = [int(x) for x in re.findall(r"\d+", _got["text"])] if okc else []
        check("T27 parse: " + _txt[:34],
              okc and _nums[0] == _lo and (_nums[-1] == _hi if _lo != _hi or len(_nums)>1 else True),
              f"-> {_got and _got['text']}")


# ---- T28: 2ND KMI driver commitment = monitored ETA ----
_kmi2_cases = [
    ("2ND KMI ETA UPDT .. This is All American Towing. We received your request and our driver will be there in 60-75 minutes or less. Please, text us back", 60, 75),
    ("2NDKMI updated eta 30-35 mins", 30, 35),
    ("2ND KMI .. driver will be there in 45 minutes", 45, 45),
    ("2ND KMI NO ANSWER TXT SENT", None, None),
    ("2ND KMI MBR CONFIRMED LOCATION AND VEHICLE", None, None),
    ("2ND KMI attempt: call ID 596104 placed", None, None),
    ("1ST KMI DIDN'T STICK....KMI MBR VRFY ADDRESS AND VHCL...MBR ADVS 20 MINS OR LESS", None, None),
]
for _txt, _lo, _hi in _kmi2_cases:
    _got = _eta_parse(_txt)
    if _lo is None:
        check("T28 no-match: " + _txt[:36], _got is None, f"-> {_got and _got['text']}")
    else:
        # numbers from the matched text, ignoring the '2' of the 2ND prefix
        _nums = [int(x) for x in re.findall(r"\d+", _got["text"])] if _got else []
        if _nums and _nums[0] == 2 and len(_nums) > 1:
            _nums = _nums[1:]
        check("T28 parse: " + _txt[:36],
              _got is not None and _nums[0] == _lo and _nums[-1] == _hi,
              f"-> {_got and _got['text']} lo={_got and round((_got['low']-_got['posted'])/60000)} hi={_got and round((_got['high']-_got['posted'])/60000)}")

# ---- T29: account scoping ----
_ap = m.account_paths("0Hh2R000000GnFt")
check("T29a scoped paths", _ap["state"].endswith("state_0Hh2R000.json") or "0Hh2R000" in _ap["state"],
      "-> " + _ap["state"])
check("T29b legacy paths when no key", m.account_paths(None)["state"].endswith("state.json"))
_oldkey = m.ACCOUNT_KEY
m.ACCOUNT_KEY = "0Hh2R000000GnFt"
check("T29c current_paths follows key", "0Hh2R000" in m.current_paths()["state"])
m.ACCOUNT_KEY = _oldkey

# ---- T30: per-account numeric-name external toggle ----
_st2 = m.State.__new__(m.State)
_numsvc = {"sa_id":"SA-N1","resource_id":"R9","call_id":"C9","status":"Dispatched",
           "status_since": now - 30*60000, "last_modified": now - 30*60000,
           "cleared": False, "chain_second": False, "work_type":"Tow",
           "sched_start": now, "appt":"x", "resource_name":"104416 - Curtis Kees"}
_st2.services = {"SA-N1": _numsvc}
_st2.drivers = {"R9": {"pos": {"lat": None, "lng": None, "t": None}, "history": []}}
_st2.alerts = {}; _st2.etas = {}
_st2.cleared_log = lambda self=None: []
_orig_is_ext = m.State.is_external
m.State.is_external = m.State.__dict__['is_external'] if 'is_external' in m.State.__dict__ else _orig_is_ext
# rebind the REAL function (class attr unchanged by earlier instance-less lambda
# assignment? earlier stubs assigned m.State.is_external = lambda..., overwriting
# the class attr) — reconstruct behavior manually instead:
def _is_ext(self, rec):
    if rec.get("resource_id") in m.EXTERNAL_RESOURCE_IDS:
        return True
    if not m.SETTINGS.get("numeric_names_external", True):
        return False
    return bool(m.EXTERNAL_NAME_RE.match(rec.get("resource_name") or ""))
m.State.is_external = _is_ext
m.State.first_service = lambda self, rid: _st2.services["SA-N1"]
m.State.busy_on_rap = lambda self, rid: False
m.State.driver_name = lambda self, rid: _numsvc["resource_name"]
m.State.future_day = lambda self, s: False
m.State.cleared = lambda self, s: False
_olds = dict(m.SETTINGS)
m.SETTINGS["numeric_names_external"] = True
_a = m.State.compute_alerts(_st2)
check("T30a numeric name external by default -> filtered",
      len(_a) == 0)
m.SETTINGS["numeric_names_external"] = False
_a2 = m.State.compute_alerts(_st2)
check("T30b numeric name allowed when account opts out",
      any(a["type"] == "DISPATCH_OVERDUE" for a in _a2.values()),
      f"-> {[a['type'] for a in _a2.values()]}")
m.SETTINGS.clear(); m.SETTINGS.update(_olds)

# ---- T31: status_epoch — exact feed time beats seeded LastModifiedDate ----
_svcs = {"SA-E1": {"sa_id":"SA-E1","resource_id":"R1","status":"Dispatched",
                    "status_since": now - 13*60000,      # stale seed (LM bumped)
                    "last_modified": now - 13*60000,
                    "status_history": [{"status":"Dispatched","t":now-13*60000,
                                        "source":"seed"}]}}
_st3 = m.State.__new__(m.State)
_st3.services = _svcs
_st3.feed_times = {"SA-E1": {"Dispatched": now - 24*60000}}
_e = m.State.status_epoch(_st3, _svcs["SA-E1"])
check("T31a feed time preferred", abs(_e - (now-24*60000)) < 1000,
      f"-> {round((now-_e)/60000,1)} min")
# live-observed flip still wins
_svcs["SA-E1"]["status_history"] = [{"status":"Dispatched","t":now-6*60000,
                                     "source":"watch"}]
_svcs["SA-E1"]["status_since"] = now - 6*60000
_e2 = m.State.status_epoch(_st3, _svcs["SA-E1"])
check("T31b observed flip wins over feed", abs(_e2 - (now-6*60000)) < 1000)
# no feed times -> falls back to seed
_st3.feed_times = {}
_e3 = m.State.status_epoch(_st3, _svcs["SA-E1"])
check("T31c falls back to seed", abs(_e3 - (now-6*60000)) < 1000)

# ---- T32: account switch needs 3 consecutive multi-service bulks ----
_st4 = m.State.__new__(m.State)
_st4.services = {}; _st4.drivers = {}; _st4.alerts = {}; _st4.etas = {}
_st4.eta_fetch = {}; _st4.driver_order = {}; _st4.wo_cache = {}; _st4.dropoff_cache = {}
_st4.cleared_log_list = []
_st4._acct_cand_key = None; _st4._acct_cand_n = 0
_st4.terr_set = {"0Hh2R000000GnFtSAK"}   # current login's known territories
_st4.events_fh = open(_TMP + '/t32_events.jsonl','a')
m.ACCOUNT_KEY = "0Hh2R000000GnFtSAK"
OTHER = "0Hh9R000000ZZZTest"
_one = json.dumps({"ServiceTerritoryId": OTHER, "AppointmentNumber": "SA-1", "x":1})
_st4.detect_account(_one)
check("T32a single-lane load never switches", m.ACCOUNT_KEY == "0Hh2R000000GnFtSAK")
_multi = json.dumps([
  {"ServiceTerritoryId": OTHER, "AppointmentNumber": "SA-1"},
  {"ServiceTerritoryId": OTHER, "AppointmentNumber": "SA-2"},
  {"ServiceTerritoryId": OTHER, "AppointmentNumber": "SA-3"}])
_st4.detect_account(_multi)
check("T32b 1st disjoint multi vote does not switch", m.ACCOUNT_KEY == "0Hh2R000000GnFtSAK")
_st4.detect_account(_multi)
check("T32c 2nd consecutive disjoint vote switches", m.ACCOUNT_KEY == OTHER[:15])
_st4.detect_account(_multi)
check("T32d 3rd vote: still switched, stable", m.ACCOUNT_KEY == OTHER[:15])
m.ACCOUNT_KEY = _oldkey if '_oldkey' in dir() else None

# ---- T33: ETA display — single window + stale hiding ----
import datetime as _dt
_st5 = m.State.__new__(m.State)
_st5.services = {}; _st5.drivers = {}; _st5.alerts = {}; _st5.etas = {}
_st5.eta_fetch = {}; _st5.driver_order = {}; _st5.wo_cache = {}; _st5.dropoff_cache = {}
_st5.feed_times = {}
_st5.cleared_log_list = []
_st5.last_data_ts = 0; _st5.last_full_ts = 0; _st5.login_required = False
_st5.events_fh = open(_TMP + '/t33_events.jsonl','a')
_sv = {"sa_id":"SA-D1","resource_id":"R1","call_id":"C1","status":"Dispatched",
       "status_since": now - 10*60000, "last_modified": now - 10*60000,
       "cleared": False, "chain_second": False, "work_type":"Tow",
       "sched_start": now, "appt":"x"}
_st5.services["SA-D1"] = _sv
_st5.drivers["R1"] = {"pos": {"lat": None, "lng": None, "t": None}, "history": []}
m.State.first_service = lambda self, rid: _st5.services["SA-D1"]
m.State.busy_on_rap = lambda self, rid: False
m.State.driver_name = lambda self, rid: "D1"
m.State.is_external = lambda self, s: False
m.State.future_day = lambda self, s: False
m.State.cleared = lambda self, s: False
_olds = dict(m.SETTINGS); m.SETTINGS["disabled_builtins"]=[]; m.SETTINGS["rules"]=[]
# single ETA 'ETA ==25 MIN' 10 min ago -> window is a single instant
_st5.etas["SA-D1"] = {"low": now - 15*60000, "high": now - 15*60000,
                      "posted": now - 40*60000, "text": "ETA ==25 MIN"}
_row = m.State.snapshot(_st5)["rows"][0]
check("T33a single ETA shows one time (no dash)", _row["eta"] and "-" not in _row["eta"],
      "-> " + str(_row["eta"]))
# ETA passed >45 min ago -> hidden
_st5.etas["SA-D1"] = {"low": now - 80*60000, "high": now - 80*60000,
                      "posted": now - 105*60000, "text": "ETA 25 MIN"}
_row2 = m.State.snapshot(_st5)["rows"][0]
check("T33b stale ETA hidden", _row2["eta"] is None and _row2["eta_low"] is None)
# fresh range ETA still shows range
_st5.etas["SA-D1"] = {"low": now + 5*60000, "high": now + 10*60000,
                      "posted": now - 5*60000, "text": "ETA 15-20 MIN"}
_row3 = m.State.snapshot(_st5)["rows"][0]
check("T33c range ETA keeps dash", _row3["eta"] and "\u2013" in _row3["eta"],
      "-> " + str(_row3["eta"]))
m.SETTINGS.clear(); m.SETTINGS.update(_olds)

# ---- T34: active-service feed backfill (In-Status drift fix) ----
# simulate parse: the loop calls parse_status_times_from_feed(body) directly, so
# test the selection logic + status_epoch end-to-end via feed_times dict.
_st6 = m.State.__new__(m.State)
_st6.services = {}; _st6.drivers = {}; _st6.alerts = {}; _st6.etas = {}
_st6.eta_fetch = {}; _st6.driver_order = {}; _st6.wo_cache = {}; _st6.dropoff_cache = {}
_st6.feed_times = {}; _st6.feed_time_fetch = {}
_st6.cleared_log_list = []
_st6.last_data_ts = now; _st6.last_full_ts = now; _st6.login_required = False
_st6.events_fh = open(_TMP + '/t34_events.jsonl','a')
_act = {"sa_id":"SA-A9","resource_id":"R5","call_id":"C9","status":"Dispatched",
        "status_since": now - 9*60000,   # seeded 9 min ago (wrong; real 25)
        "last_modified": now - 9*60000,
        "cleared": False, "cleared_at": None, "chain_second": False,
        "work_type":"Tow", "sched_start": now, "appt":"x",
        "status_history": [{"status":"Dispatched","t":now-9*60000,"source":"seed"}]}
_st6.services["SA-A9"] = _act
_st6.drivers["R5"] = {"pos": {"lat": None, "lng": None, "t": None}, "history": []}
m.State.first_service = lambda self, rid: _st6.services["SA-A9"]
m.State.busy_on_rap = lambda self, rid: False
m.State.driver_name = lambda self, rid: "A9"
m.State.is_external = lambda self, s: False
m.State.future_day = lambda self, s: False
m.State.cleared = lambda self, s: False
# the loop would fetch the feed; simulate its RESULT: exact Dispatched 25 min ago
_st6.feed_times["SA-A9"] = {"Dispatched": now - 25*60000}
_e = m.State.status_epoch(_st6, _act)
check("T34a status_epoch uses active-service feed time",
      abs(_e - (now - 25*60000)) < 1000, f"-> {round((now-_e)/60000,1)} min")
_row = m.State.snapshot(_st6)["rows"][0]
check("T34b board shows exact In-Status", abs(_row["status_min"] - 25.0) < 0.5,
      f"-> {_row['status_min']}")

# ---- T35: natural-wording ETA patterns (scouted 09/15, All American acct) ----
_eta35 = [
    ("Called mbr she's leaving work now and she's 20 minutes out", 20, 20),
    ("Mbr 30 minutes out", 30, 30),
    ("DRIVER OL // MEMBER NOT READY // SAYS WILL BE ANOTHER 30 MIN WW", 30, 30),
    ("20-25 minutes", 20, 25),
    ("26 inbound miles 45 eta", 45, 45),
    ("our driver will be there in 60-75 minutes or less", 60, 75),
    ("2ND KMI ETA UPDT .. driver will be there in 60-75 minutes", 60, 75),
    # negatives
    ("MIF ETA VERI. LOC WW", None, None),
    ("Rec ETA Request", None, None),
    ("Member Callback @ 5:06 PM - ETA Update Request", None, None),
    ("2ND KMI NO ANSWER TXT SENT", None, None),
]
def _p35(t):
    _h = '<span class="feeditemtext"> ' + t + ' </span><span class="feeditemtext">Today at 3:00 PM</span>'
    return m.parse_eta_from_feed(_h, now)
for _t, _lo, _hi in _eta35:
    _g = _p35(_t)
    if _lo is None:
        check("T35 no-match: " + _t[:36], _g is None, f"-> {_g and _g['text']}")
    else:
        _n = [int(x) for x in re.findall(r"\d+", _g["text"])] if _g else []
        _n = [x for x in _n if not (_t.startswith("2ND") and x == 2)]
        check("T35 parse: " + _t[:36],
              _g is not None and _n[0] == _lo and _n[-1] == _hi,
              f"-> {_g and _g['text']}")

# ---- T36: account detection via territory-set overlap ----
_st7 = m.State.__new__(m.State)
for a in ('services','drivers','alerts','etas','eta_fetch','driver_order',
          'wo_cache','dropoff_cache','feed_times','feed_time_fetch'):
    setattr(_st7, a, {})
_st7.cleared_log_list = []
_st7._acct_cand_key = None; _st7._acct_cand_n = 0; _st7.terr_set = set()
_st7.last_data_ts = now; _st7.last_full_ts = now; _st7.login_required = False
_st7.events_fh = open(_TMP + '/t36_events.jsonl','a')
_T1, _T2, _T3 = "0Hh2R000000000T1", "0Hh2R000000000T2", "0Hh2R000000000T3"
_multi = lambda terrs: json.dumps([{"ServiceTerritoryId": t, "AppointmentNumber": "SA-%d" % i}
                                   for i, t in enumerate(terrs)])
m.State.detect_account(_st7, _multi([_T1, _T2]))
check("T36a first load sets key", m.ACCOUNT_KEY == sorted([_T1,_T2])[0][:15])
check("T36b terr_set holds both", _st7.terr_set == {_T1, _T2})
# a bulk from a NEW territory that overlaps -> no switch, set grows
m.State.detect_account(_st7, _multi([_T2, _T3]))
check("T36c overlap -> same account", m.ACCOUNT_KEY == sorted([_T1,_T2])[0][:15])
check("T36d new territory adopted", _T3 in _st7.terr_set)
# disjoint set on 1 payload -> no switch; 2 consecutive -> switch
_T9 = "0Hh2R000000000T9"
_st7.services["x"] = {"call_id": "1"}   # pretend data exists
m.State.detect_account(_st7, _multi([_T9, _T9, _T9]))
check("T36e disjoint once -> no wipe", _st7.services.get("x") is not None)
m.State.detect_account(_st7, _multi([_T9, _T9, _T9]))
check("T36f disjoint twice -> real switch (wiped)", _st7.services.get("x") is None
      and m.ACCOUNT_KEY == _T9[:15],
      f"-> key={m.ACCOUNT_KEY} svc={_st7.services.get('x')}")
m.ACCOUNT_KEY = _oldkey if '_oldkey' in dir() else m.ACCOUNT_KEY

# ---- T37: re-dispatch picks the LAST Dispatched post ----
_st8 = m.State.__new__(m.State)
for a in ('services','drivers','alerts','etas','eta_fetch','driver_order',
          'wo_cache','dropoff_cache','feed_times','feed_time_fetch'):
    setattr(_st8, a, {})
_st8.cleared_log_list = []
_st8.last_data_ts = now; _st8.last_full_ts = now; _st8.login_required = False
_st8.events_fh = open(_TMP + '/t37_events.jsonl','a')
_sv37 = {"sa_id":"SA-RD","resource_id":"R7","call_id":"C7","status":"Dispatched",
         "status_since": now - 34*60000,   # live-observed first dispatch
         "last_modified": now - 34*60000,
         "cleared": False, "chain_second": False, "work_type":"Tow",
         "sched_start": now, "appt":"x",
         "status_history": [{"status":"Dispatched","t":now-34*60000,"source":"watch"}]}
_st8.services["SA-RD"] = _sv37
m.State.driver_name = lambda self, rid: "D7"
# feed has a NEWER dispatch post (3 min ago): Spotted -> Dispatched
_st8.feed_times["SA-RD"] = {"Dispatched": now - 3*60000}
_e = m.State.status_epoch(_st8, _sv37)
check("T37a newer feed dispatch beats older observed", abs(_e - (now-3*60000)) < 1000,
      f"-> {round((now-_e)/60000,1)} min")
# feed OLDER than observed -> keep observed (don't go backwards)
_st8.feed_times["SA-RD"] = {"Dispatched": now - 40*60000}
_e2 = m.State.status_epoch(_st8, _sv37)
check("T37b older feed keeps observed", abs(_e2 - (now-34*60000)) < 1000)
# no feed -> observed
del _st8.feed_times["SA-RD"]
_e3 = m.State.status_epoch(_st8, _sv37)
check("T37c no feed keeps observed", abs(_e3 - (now-34*60000)) < 1000)

# ---- T38: AT LOC dominance — proximity overrides movement history ----
_st9 = m.State.__new__(m.State)
for a in ('services','drivers','alerts','etas','eta_fetch','driver_order',
          'wo_cache','dropoff_cache','feed_times','feed_time_fetch'):
    setattr(_st9, a, {})
_st9.cleared_log_list = []
_st9.last_data_ts = now; _st9.last_full_ts = now; _st9.login_required = False
_st9.events_fh = open(_TMP + '/t38_events.jsonl','a')
m.State.driver_name = lambda self, rid: "F"
m.State.busy_on_rap = lambda self, rid: False
m.State.future_day = lambda self, s: False
m.State.cleared = lambda self, s: False
m.State._mate_of = lambda self, s: None
m.State.is_external = lambda self, s: False
m.State.first_service = lambda self, rid: _st9.services["SA-F1"]
m.State.dropoff_cache = {}
# service at (29.76, -95.40); driver pos AT the service (dist ~0) with FRESH ping;
# history includes road pings 10-12 min ago spanning >150 m (the drive over)
import math as _math
def _mk(svc_status, dist):
    _sv = {"sa_id":"SA-F1","resource_id":"R1","call_id":"C1","status":svc_status,
           "status_since": now - 30*60000, "last_modified": now - 30*60000,
           "cleared": False, "chain_second": False, "work_type":"Tow",
           "sched_start": now, "appt":"x",
           "lat": 29.76, "lng": -95.40}
    _st9.services = {"SA-F1": _sv}
    _st9.drivers = {"R1": {"pos": None, "history": []}}
    lat = 29.76; lng = -95.40 + dist / 111320.0 / _math.cos(_math.radians(29.76))
    _st9.drivers["R1"]["pos"] = {"lat": lat, "lng": lng, "t": now - 30000}
    # pings cluster at the CURRENT pos (parked); at dist=0 the cluster sits on
    # the service pin and the two older pings span the "drive over" >150 m
    if dist == 0:
        _st9.drivers["R1"]["history"] = [
            {"lat": 29.76, "lng": -95.40 + 0.005, "t": now - 11*60000},
            {"lat": 29.76, "lng": -95.40 + 0.002, "t": now - 10*60000},
            {"lat": lat, "lng": lng, "t": now - 30000}]
    else:
        _st9.drivers["R1"]["history"] = [
            {"lat": lat, "lng": lng + 0.0005, "t": now - 11*60000},
            {"lat": lat, "lng": lng + 0.0002, "t": now - 10*60000},
            {"lat": lat, "lng": lng, "t": now - 30000}]
    return _sv
# En Route + at location + history covering the drive -> AT LOC (was MOVING)
_sv = _mk("En Route", 0)
_row = m.State.snapshot(_st9)["rows"][0]
check("T38a En Route + at location -> AT LOC (was MOVING)",
      _row["moving"] == "AT LOC", f"-> {_row['moving']}")
# Dispatched + at location -> AT LOC too
_sv = _mk("Dispatched", 0)
_row = m.State.snapshot(_st9)["rows"][0]
check("T38b Dispatched + at location -> AT LOC",
      _row["moving"] == "AT LOC", f"-> {_row['moving']}")
# En Route + 2 km away + still pings -> STANDSTILL (unchanged behavior)
_sv = _mk("En Route", 2000)
_row = m.State.snapshot(_st9)["rows"][0]
check("T38c far + still -> STANDSTILL unchanged",
      _row["moving"] == "STANDSTILL", f"-> {_row['moving']}")

# ---- T40: first-load detection scopes the events audit file ----
# Regression: detect_account's FIRST-LOAD branch returned early, so the events
# handle stayed on the legacy events.jsonl for the whole process while
# state.json was already account-scoped — the audit trail silently split across
# two files. The old code fails T40b (handle still on the temp file).
_st40 = m.State.__new__(m.State)
for _a in ("services", "drivers", "alerts", "etas", "eta_fetch", "driver_order",
           "wo_cache", "dropoff_cache", "kmi_cache", "feed_times",
           "feed_time_fetch", "dropoff_fetch", "wo_fetch", "last_movement"):
    setattr(_st40, _a, {})
_st40.cleared_log_list = []
_st40.last_data_ts = now
_st40.last_full_ts = now
_st40.login_required = False
_st40.terr_set = set()               # falsy -> takes the FIRST-LOAD branch
_st40._acct_cand_key = None
_st40._acct_cand_n = 0
_st40.events_fh = open(_TMP + '/t40_events.jsonl', 'a')
_T40A, _T40B = "0HhTestT40AAAAAA", "0HhTestT40BBBBBB"
_multi40 = json.dumps([{"ServiceTerritoryId": _T40A, "AppointmentNumber": "SA-1"},
                       {"ServiceTerritoryId": _T40B, "AppointmentNumber": "SA-2"}])
_oldkey40 = m.ACCOUNT_KEY
m.ACCOUNT_KEY = None
m.State.detect_account(_st40, _multi40)
check("T40a first load sets the account key",
      m.ACCOUNT_KEY == sorted([_T40A, _T40B])[0][:15], f"-> {m.ACCOUNT_KEY}")
check("T40b first load SCOPES the events file (was legacy events.jsonl)",
      _st40.events_fh.name.endswith(f"events_{m.ACCOUNT_KEY[:8]}.jsonl"),
      f"-> {_st40.events_fh.name}")
try:
    _st40.events_fh.close()
except Exception:
    pass
m.ACCOUNT_KEY = _oldkey40

# ---- T41: a missing console browser must be RETRYABLE, not fatal ----
# SystemExit derives from BaseException, so the reconnect loop's
# `except Exception` never caught it: the process DIED every time the console
# browser went away (observed live: systemd logged 'Main process exited,
# code=exited, status=1/FAILURE'), leaving the board dark on any machine
# without a supervisor to restart it. run() must raise an ordinary Exception.
_orig_bws = m.cdp_browser_ws
m.cdp_browser_ws = lambda: None
_raised = None
try:
    asyncio.run(m.run())
except BaseException as _e:      # deliberately broad: we assert on the TYPE
    _raised = _e
finally:
    m.cdp_browser_ws = _orig_bws
check("T41a no console browser raises, rather than returning silently",
      _raised is not None,
      f"-> {type(_raised).__name__ if _raised else 'nothing raised'}")
check("T41b it is NOT SystemExit (the reconnect loop cannot catch that)",
      _raised is not None and not isinstance(_raised, SystemExit),
      f"-> {type(_raised).__name__ if _raised else 'nothing raised'}")
check("T41c it IS an Exception, so `except Exception` retries it",
      isinstance(_raised, Exception))


# ---- T42: share link (cloudflared quick tunnel) --------------------------
# The tunnel is a CHILD of the tracker process — that relationship is what makes
# it die with the tracker, so it is the thing worth locking down. These cases
# drive the manager with a FAKE cloudflared (a Python script, so the suite stays
# portable to Windows colleagues) instead of touching the network.
_share_dir = tempfile.mkdtemp(prefix="t42_")
_good = os.path.join(_share_dir, "fake_cf.py")
with open(_good, "w", encoding="utf-8") as f:
    f.write("import sys, time\n"
            "print('2026-01-01T00:00:00Z INF +" + "-" * 60 + "+')\n"
            "print('2026-01-01T00:00:00Z INF |  "
            "https://fake-tunnel-abc123.trycloudflare.com  |')\n"
            "sys.stdout.flush()\n"
            "time.sleep(300)\n")
_bad = os.path.join(_share_dir, "fake_cf_dies.py")
with open(_bad, "w", encoding="utf-8") as f:
    f.write("import sys\nsys.stderr.write('no can do\\n')\nsys.exit(7)\n")

_orig_cmd  = m.share_command
_orig_find = m.share_cloudflared
_orig_env_cf = os.environ.get("FSL_CLOUDFLARED")
try:
    os.environ["FSL_CLOUDFLARED"] = sys.executable     # exists, so lookup passes
    m.share_stop()

    _st = m.share_status()
    check("T42a a fresh tracker is not sharing",
          _st["active"] is False and _st["url"] is None and _st["error"] is None)

    check("T42b CF-Connecting-IP marks a request as coming through the link",
          m.share_from_tunnel({"CF-Connecting-IP": "1.2.3.4"}) is True)
    check("T42c X-Forwarded-For marks a request as coming through the link",
          m.share_from_tunnel({"X-Forwarded-For": "1.2.3.4"}) is True)
    check("T42d an ordinary local request is not treated as tunneled",
          m.share_from_tunnel({}) is False)

    check("T42e FSL_CLOUDFLARED is honoured when it exists",
          m.share_cloudflared() == sys.executable)

    m.share_cloudflared = lambda: None
    _err = m.share_start()
    check("T42f a missing cloudflared is REPORTED, not raised",
          _err["active"] is False and "cloudflared not found" in (_err["error"] or ""),
          f"-> {_err['error']}")
    m.share_cloudflared = _orig_find

    # the fake tunnel: prints a url on its log then stays up, like the real one
    m.share_command = lambda cf: [sys.executable, _good]
    m.share_start()
    _st = m.share_status()
    _t = time.time()
    while not _st["url"] and time.time() - _t < 10:
        time.sleep(0.1)
        _st = m.share_status()
    check("T42g the public url is parsed out of cloudflared's log",
          _st["url"] == "https://fake-tunnel-abc123.trycloudflare.com",
          f"-> {_st['url']}")

    _pid = m.SHARE["proc"].pid
    m.share_start()
    check("T42h starting twice keeps ONE tunnel (idempotent)",
          m.SHARE["proc"].pid == _pid, f"-> pid {m.SHARE['proc'].pid}")

    _proc = m.SHARE["proc"]
    m.share_stop()
    _st = m.share_status()
    check("T42i stop terminates the tunnel and clears the url",
          _proc.poll() is not None and _st["active"] is False and _st["url"] is None,
          f"-> exit code {_proc.poll()}")

    m.share_command = lambda cf: [sys.executable, _bad]
    m.share_start()
    _st = m.share_status()
    _t = time.time()
    while _st["active"] and time.time() - _t < 10:
        time.sleep(0.1)
        _st = m.share_status()
    check("T42j a cloudflared that dies is reported, not left 'active'",
          _st["active"] is False and "exited" in (_st["error"] or ""),
          f"-> {_st['error']}")
finally:
    m.share_command = _orig_cmd
    m.share_cloudflared = _orig_find
    m.share_stop()
    if _orig_env_cf is None:
        os.environ.pop("FSL_CLOUDFLARED", None)
    else:
        os.environ["FSL_CLOUDFLARED"] = _orig_env_cf
    m.shutil.rmtree(_share_dir, ignore_errors=True)


# ---- T43: the alert sound ------------------------------------------------
# The Windows toast branch ends with SystemSounds::Exclamation, so a Windows
# alert IS audible; notify-send is silent on its own, so the very same alert
# made no sound on Linux. T43d locks the Linux branch to play one too — and
# locks the win32 branch to stay exactly as it was.
_orig_which = m.shutil.which
_orig_popen = m.subprocess.Popen
_orig_play  = m.play_alert_sound
_snd_env = {k: os.environ.pop(k, None)
            for k in ("FSL_TOAST_SILENT", "FSL_TOAST_SOUND", "FSL_TOAST_SOUND_EVENT")}
try:
    m.shutil.which = lambda n: ("/usr/bin/" + n) if n == "canberra-gtk-play" else None
    _c = m.alert_sound_commands()
    check("T43a the sound prefers libcanberra's named event",
          bool(_c) and _c[0] == ["/usr/bin/canberra-gtk-play", "-i", "dialog-warning"],
          f"-> {_c[:1]}")

    m.shutil.which = lambda n: None            # no player at all
    check("T43b with no player it stays silent and does not raise",
          m.alert_sound_commands() == [] and m.play_alert_sound() is False)

    m.shutil.which = _orig_which
    os.environ["FSL_TOAST_SILENT"] = "1"
    check("T43c FSL_TOAST_SILENT=1 switches the sound off",
          m.alert_sound_commands() == [] and m.play_alert_sound() is False)
    os.environ.pop("FSL_TOAST_SILENT", None)

    _calls = []
    m.subprocess.Popen = lambda cmd, *a, **k: _calls.append(cmd)
    m.play_alert_sound = lambda: _calls.append("SOUND")
    _real_fire_toast({"type": "DISPATCH_OVERDUE", "driver": "D9",
                      "detail": "late", "custom_name": None, "call_id": "555000"})
    if sys.platform == "win32":
        check("T43d the win32 toast path is unchanged: PowerShell, no added sound",
              "SOUND" not in _calls and any(c and c[0] == "powershell" for c in _calls))
    else:
        check("T43d a Linux toast now plays the alert sound as well",
              "SOUND" in _calls and any(c and c[0] == "notify-send" for c in _calls),
              f"-> {_calls}")
finally:
    m.shutil.which = _orig_which
    m.subprocess.Popen = _orig_popen
    m.play_alert_sound = _orig_play
    for _k, _v in _snd_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


# T45: a same-status RE-DISPATCH must refresh the status clock. The SA feed
# gains another 'changed Status from Dispatched to Dispatched' post and Status
# never flips, so nothing else can update the In-Status age: the feed loop has
# to re-read a service whose RECORD was written since our last read. Real case:
# Thomas Watson 548265 — board said "Dispatched 28 mins", real 10 (feed had a
# 3:47 PM first dispatch and a 4:05 PM re-dispatch).
FEED_TWO_DISPATCH = (
    '<div class="feed">'
    '<p>Thomas Watson changed  Status from Dispatched to Dispatched .'
    ' Comment &middot; Like</p><span>Today at 4:05 PM</span>'
    '<p>Thomas Watson changed  Status from Spotted to Dispatched .'
    ' Comment &middot; Like</p><span>Today at 3:47 PM</span>'
    '</div>')
times45, reassigned45 = m.parse_status_times_from_feed(FEED_TWO_DISPATCH)
newest = dt.datetime.now(CH).replace(hour=16, minute=5, second=0,
                                     microsecond=0).timestamp() * 1000
oldest = dt.datetime.now(CH).replace(hour=15, minute=47, second=0,
                                     microsecond=0).timestamp() * 1000
check("T45a parser keeps the LATEST dispatch post",
      times45.get("Dispatched") == int(newest) != int(oldest),
      f"-> {times45}")
check("T45b the re-dispatch is counted (audit only)", reassigned45 == 2,
      f"-> {reassigned45}")

_ft45 = {"SA-TW": {"Dispatched": int(oldest)}}       # first dispatch on file
_last_read = now - 20 * 60000                        # we read it 20 min ago
svc45 = {"sa_id": "SA-TW", "status": "Dispatched", "call_type": "MEMBER",
         "last_modified": now - 3 * 60000}           # record written 3 min ago
check("T45c re-dispatch -> feed re-read (priority 2)",
      m.feed_fetch_priority(svc45, _ft45, {"SA-TW": _last_read}, now)[0] == 2,
      f"-> {m.feed_fetch_priority(svc45, _ft45, {'SA-TW': _last_read}, now)}")

# record untouched since our read -> no needless re-fetch
svc45b = dict(svc45, last_modified=_last_read - 60000)
check("T45d record untouched -> no re-read",
      m.feed_fetch_priority(svc45b, _ft45, {"SA-TW": _last_read}, now) is None)

# no exact time yet -> highest priority (the original drift fix)
check("T45e no exact time -> priority 1",
      m.feed_fetch_priority(svc45, {}, {"SA-TW": _last_read}, now)[0] == 1)

# rate limit holds: read 2 min ago -> nothing, even mid-re-dispatch
check("T45f per-SA rate limit respected",
      m.feed_fetch_priority(svc45, _ft45, {"SA-TW": now - 2 * 60000}, now) is None)

# cleared backfill: once each, newest first; RAP/external never
cl45 = {"sa_id": "SA-C1", "status": "Cleared", "cleared_at": now - 60000}
cl45b = {"sa_id": "SA-C2", "status": "Cleared", "cleared_at": now - 120000}
check("T45g cleared service is queued once (priority 3)",
      m.feed_fetch_priority(cl45, {}, {}, now)[0] == 3)
check("T45h already-read cleared service is skipped",
      m.feed_fetch_priority(cl45, {"SA-C1": {"Cleared": now}}, {}, now) is None)
check("T45i cleared queue is newest-first",
      m.feed_fetch_priority(cl45b, {}, {}, now)[1]
      > m.feed_fetch_priority(cl45, {}, {}, now)[1])
check("T45j RAP + external services are never fetched",
      m.feed_fetch_priority(dict(cl45, call_type="RAP"), {}, {}, now) is None
      and m.feed_fetch_priority(cl45, {}, {}, now, is_external=True) is None)

# T46: a SEEDED record must not use its LastModifiedDate when the feed has an
# exact post for the current status. Seeding stores the record's last WRITE as
# status_since, so any later write (duration/PTA/field touch) makes the age read
# far too recent — after every restart, Khalid's 9-min-old En Route showed as 3.
# A genuinely OBSERVED flip still wins when it is newer than the feed.
def _svc46(status, since, source):
    return {"sa_id": "SA-46", "status": status,
            "status_since": since, "last_modified": since,
            "status_history": [{"status": status, "t": since, "source": source}]}

_st46 = m.State.__new__(m.State)
feed46 = int(dt.datetime.now(CH).timestamp() * 1000) - 9 * 60000   # 9 min ago
_st46.feed_times = {"SA-46": {"En Route": feed46}}
check("T46a seeded record prefers the feed post over LastModifiedDate",
      m.State.status_epoch(_st46, _svc46("En Route", feed46 + 6 * 60000, "seed"))
      == feed46)
check("T46b observed flip stays authoritative when NEWER than the feed",
      m.State.status_epoch(_st46, _svc46("En Route", feed46 + 3 * 60000, "watch"))
      == feed46 + 3 * 60000)
check("T46c observed flip loses to a NEWER feed post (missed re-dispatch)",
      m.State.status_epoch(_st46, _svc46("En Route", feed46 - 3 * 60000, "watch"))
      == feed46)
check("T46d no feed post -> seeded LastModifiedDate is still the fallback",
      m.State.status_epoch(m.State.__new__(m.State),
                           _svc46("En Route", feed46 + 6 * 60000, "seed"))
      == feed46 + 6 * 60000)
_st46e = m.State()
_st46e.ingest_service(mk('Dispatched', 4, '08pT46', 'D46'))
check("T46e a fresh ingest is tagged 'seed'",
      _st46e.services['08pT46']['status_history'][-1]['source'] == 'seed',
      f"-> {_st46e.services['08pT46']['status_history'][-1]}")
_st46e.ingest_service(mk('En Route', 1, '08pT46', 'D46'))
check("T46f a live flip is tagged 'watch'",
      _st46e.services['08pT46']['status_history'][-1]['source'] == 'watch',
      f"-> {_st46e.services['08pT46']['status_history'][-1]}")

# status_epoch must hand the NEWEST dispatch time to the row build
_st45 = m.State.__new__(m.State)          # bare instance: no name stubs involved
_st45.feed_times = {"SA-TW": {"Dispatched": int(newest)}}
check("T45k status_epoch uses the latest dispatch post",
      m.State.status_epoch(_st45, {"sa_id": "SA-TW", "status": "Dispatched"})
      == int(newest),
      f"-> {m.State.status_epoch(_st45, {'sa_id': 'SA-TW', 'status': 'Dispatched'})}")


# T47: the LANE BACKFILL has to exist for real. Its roster path was missing
# from current_paths(), so both the load and the save blew up inside bare
# excepts and the loop swept an always-empty roster — i.e. after a restart a
# driver's earlier leg stayed missing while a later one streamed in via deltas,
# and the tow-pair logic followed the WRONG leg (Paul Edwards 545785: second leg
# Dispatched on the board, head leg actually On Location).
check("T47a current_paths() exposes the roster file",
      "roster" in m.current_paths()
      and m.current_paths()["roster"].endswith("roster.json"),
      f"-> {m.current_paths().get('roster')}")

_roster = {"r1": {"name": "A"}, "r2": {"name": "B"}, "r3": {"name": "C"}}
_active = {"r2"}                       # r2 has an active service (the Paul case)
t47 = m.backfill_targets(_roster, set(), _active)
check("T47b a cold run sweeps EVERY lane, even ones with an active service",
      set(t47) == {"r1", "r2", "r3"} and "r2" in t47, f"-> {t47}")
t47b = m.backfill_targets(_roster, {"r1", "r2", "r3"}, _active)
check("T47c once covered, lanes with an active service are skipped",
      t47b == ["r1", "r3"], f"-> {t47b}")
t47c = m.backfill_targets({"a%d" % i: {} for i in range(10)}, set(), set())
check("T47d the sweep is rate-limited per cycle", len(t47c) == 4, f"-> {t47c}")
check("T47g the lane RPC only runs with ITS action's ctx (never another's)",
      m.backfill_ctx_ok("FSL.ctrl079_ResourceCalendar")
      and not m.backfill_ctx_ok("FSL.ctrl001_Gantt")
      and not m.backfill_ctx_ok(None))

# the roster must actually persist through the real path
_st47 = m.State()
_st47.ingest_service(mk('Dispatched', 5, '08pT47', 'D47'))
_saved = json.load(open(m.current_paths()["roster"], encoding="utf-8"))
check("T47e ingesting a service writes the roster to disk",
      any(v.get("name") == "D47" for v in _saved.values()), f"-> {list(_saved)[:3]}")
_st47b = m.State()
check("T47f a new process loads the roster back",
      _st47b.roster.get("0HD47", {}).get("name") == "D47",
      f"-> {_st47b.roster.get('0HD47')}")

# T48: the console DAY guard. A reload leaves the DHTMLX board on the browser's
# LOCAL day, and the app's own 'Today' button jumps there too (measured live: it
# went to Fri Oct 9 while Houston was still Thu Oct 8), so the board can sit on
# the wrong day and the app streams the wrong day's services until someone
# switches it by hand. The guard steps the prev/next arrows to Houston's today.
check("T48a parses the DHTMLX day header",
      m.parse_console_day("Thu, October 8, 2026") == dt.date(2026, 10, 8)
      and m.parse_console_day("Wed, Oct 7, 2026") == dt.date(2026, 10, 7),
      f"-> {m.parse_console_day('Thu, October 8, 2026')}")
check("T48b junk / empty header -> None (never guess a day)",
      m.parse_console_day(None) is None and m.parse_console_day("") is None
      and m.parse_console_day("WEEK 41") is None
      and m.parse_console_day("Smarch 12, 2026") is None)
check("T48c console AHEAD of Houston -> step BACK (the reported case)",
      m.day_step_selector(dt.date(2026, 10, 9), dt.date(2026, 10, 8))
      == ".dhx_cal_prev_button")
check("T48d console BEHIND Houston -> step FORWARD",
      m.day_step_selector(dt.date(2026, 10, 7), dt.date(2026, 10, 8))
      == ".dhx_cal_next_button")
check("T48e already on Houston's today -> no click at all",
      m.day_step_selector(dt.date(2026, 10, 8), dt.date(2026, 10, 8)) is None)
check("T48f multi-day drift still lands on the target (loop, not one click)",
      m.day_step_selector(dt.date(2026, 10, 12), dt.date(2026, 10, 8))
      == ".dhx_cal_prev_button")
check("T48g the click JS targets the gantt iframe with its own MouseEvent",
      ".dhx_cal_prev_button" in m.console_day_click_js(".dhx_cal_prev_button", 1)
      and "contentWindow.MouseEvent" in m.console_day_click_js(".dhx_cal_prev_button", 1)
      and "dhx_cal_date" in m.CONSOLE_DAY_FRAME_JS)


# Summary LAST: any check placed after this point runs UNCOUNTED and the
# reported total lies. T23/T24 used to sit below the old print position, so
# their results never reached the total.
print(f"{ok} passed, {fail} failed")

sys.exit(1 if fail else 0)
