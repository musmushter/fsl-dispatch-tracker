"""Regression tests for tracker alert logic + SF time encoding + ETA parser."""
import json, time, importlib.util, sys, datetime as dt, re
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('tracker', r'C:/Users/musta/fsl_tracker/tracker.py')
m = importlib.util.module_from_spec(spec)
sys.modules['tracker'] = m
spec.loader.exec_module(m)

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
        _nums = [int(x) for x in re.findall(r"\d+", _got["text"])] if _got else []
        check("T28 parse: " + _txt[:36],
              _got is not None and _nums[0] == _lo and _nums[-1] == _hi,
              f"-> {_got and _got['text']}")

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
_st4.events_fh = open(r'C:/Users/musta/AppData/Local/Temp/t32_events.jsonl','a')
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
check("T32b 1st multi vote does not switch", m.ACCOUNT_KEY == "0Hh2R000000GnFtSAK")
_st4.detect_account(_multi)
check("T32c 2nd vote does not switch", m.ACCOUNT_KEY == "0Hh2R000000GnFtSAK")
_st4.detect_account(_multi)
check("T32d 3rd consecutive vote switches", m.ACCOUNT_KEY == OTHER)
m.ACCOUNT_KEY = _oldkey if '_oldkey' in dir() else None
print(f"{ok} passed, {fail} failed")

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
    m.SETTINGS.clear(); m.SETTINGS.update(old_settings)
finally:
    m.State.first_service = _orig

sys.exit(1 if fail else 0)
